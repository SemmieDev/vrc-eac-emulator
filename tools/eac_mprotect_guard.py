#!/usr/bin/env python3
"""Stop EasyAntiCheat from wiping a game-local DLL's PE header.

Why this exists
---------------
When EAC rejects a module (a game-local DLL that is not part of the game's
signed file list) it neutralises it by erasing the first page:

    mprotect(dll_base, 0x1000, PROT_READ|PROT_WRITE|PROT_EXEC)
    memset  (dll_base, 0, 0x1000)

Wine then calls ``RtlImageNtHeader()`` on the erased header, receives NULL and
dereferences it: the process dies inside ntdll (``EXCEPTION_ACCESS_VIOLATION``,
``info[1]=8``).

The erasing ``memset`` is only reached when the preceding ``mprotect`` reports
success.  This tool rewrites the ``prot`` argument of that one ``mprotect`` call
to a value the kernel rejects, so the call fails and the VM takes its failure
path, skipping the erase.

How
---
* attach with ptrace to the game process,
* set an execute breakpoint on EAC's ``mprotect`` wrapper,
* when it targets the DLL page (a single 4 KiB page), change ``prot`` to an
  invalid value,
* detach and let the game run.

The breakpoint is a hardware breakpoint, so no single-stepping is required.

Limitations
-----------
* Timing-sensitive: EAC must reach its module check after the breakpoint is set.
* It does not make EAC accept the DLL. It only stops the header wipe.
* ``--mprotect-offset`` is specific to the EAC build; the default was verified
  against one build only.

Usage
-----
    ./eac_mprotect_guard.py --pid 1234 --dll version.dll

    ./eac_mprotect_guard.py \\
        --exe /path/to/VRChat.exe --dll version.dll \\
        --launcher /path/to/proton --compat /path/to/compatdata
"""

from __future__ import annotations

import argparse
import ctypes
import os
import signal
import struct
import subprocess
import sys
import time
from pathlib import Path

# --- ptrace (Linux x86-64) -------------------------------------------------
PTRACE_PEEKDATA = 2
PTRACE_PEEKUSER = 3
PTRACE_POKEUSER = 6
PTRACE_CONT = 7
PTRACE_GETREGS = 12
PTRACE_SETREGS = 13
PTRACE_ATTACH = 16
PTRACE_DETACH = 17

# offsetof(struct user, u_debugreg) on x86-64
DEBUGREG_BASE = 848
DR6 = DEBUGREG_BASE + 6 * 8
DR7 = DEBUGREG_BASE + 7 * 8

# x86-64 `struct user_regs_struct` field order, as returned by PTRACE_GETREGS
REG_NAMES = (
    "r15 r14 r13 r12 rbp rbx r11 r10 r9 r8 rax rcx rdx rsi rdi orig_rax "
    "rip cs eflags rsp ss fs_base gs_base ds es fs gs"
).split()
RDX = REG_NAMES.index("rdx")

# Default for --mprotect-offset. Image-relative address of the wrapper EAC uses
# to change the protection of the DLL page. Build specific; override if needed.
DEFAULT_MPROTECT_OFFSET = 0x4DA93A

# Value handed to mprotect that the kernel rejects (EINVAL), so the call fails.
INVALID_PROT = 0x10


def eac_image_base(maps: str) -> int | None:
    """Find EAC's in-game module: an anonymous memfd view of >= 64 KiB."""
    for line in maps.splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) < 6 or "/memfd:" not in fields[5] or fields[2] != "00000000":
            continue
        begin, end = (int(v, 16) for v in fields[0].split("-"))
        if end - begin >= 0x10000:
            return begin
    return None


def dll_mapping(maps: str, name: str) -> int | None:
    """Find the first file-backed page of a named DLL."""
    suffix = "/" + name.lower()
    for line in maps.splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) < 6 or fields[2] != "00000000":
            continue
        if fields[5].lower().replace("\\", "/").endswith(suffix):
            return int(fields[0].split("-")[0], 16)
    return None


def find_game_pid(exe: str, prefix: str | None) -> int | None:
    """Locate the game process by its Wine (Z:) argv[0] and optional WINEPREFIX."""
    want = ("Z:" + exe.replace("/", "\\")).lower().encode()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            argv = (entry / "cmdline").read_bytes().split(b"\0")
            if not argv or argv[0].lower() != want:
                continue
            if prefix is not None:
                env = dict(
                    item.split(b"=", 1)
                    for item in (entry / "environ").read_bytes().split(b"\0")
                    if b"=" in item
                )
                if env.get(b"WINEPREFIX", b"").decode().rstrip("/") != prefix:
                    continue
            return int(entry.name)
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    return None


class Tracer:
    def __init__(self, pid: int):
        self.pid = pid
        self.libc = ctypes.CDLL("libc.so.6", use_errno=True)
        self._ptrace = self.libc.ptrace
        self._ptrace.argtypes = [
            ctypes.c_uint,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        self._ptrace.restype = ctypes.c_long
        self.regs = (ctypes.c_ulonglong * len(REG_NAMES))()

    def call(self, request: int, addr: int = 0, data: int = 0) -> int:
        ctypes.set_errno(0)
        result = self._ptrace(request, self.pid, addr, data)
        if result == -1 and ctypes.get_errno():
            raise OSError(ctypes.get_errno(), f"ptrace request={request}")
        return result

    def attach(self) -> None:
        self.call(PTRACE_ATTACH)
        os.waitpid(self.pid, 0)

    def detach(self) -> None:
        self.call(PTRACE_DETACH, 0, 0)

    def read_regs(self) -> dict[str, int]:
        self.call(PTRACE_GETREGS, 0, ctypes.addressof(self.regs))
        return dict(zip(REG_NAMES, self.regs))

    def write_rdx(self, value: int) -> None:
        self.regs[RDX] = value
        self.call(PTRACE_SETREGS, 0, ctypes.addressof(self.regs))

    def set_exec_breakpoint(self, address: int) -> None:
        self.call(PTRACE_POKEUSER, DEBUGREG_BASE, address)
        # L0 = 1 enables DR0; execution breakpoints need no RW/LEN bits.
        self.call(PTRACE_POKEUSER, DR7, 0x1)

    def breakpoint_hit(self) -> bool:
        return bool(self.call(PTRACE_PEEKUSER, DR6) & 0x1)

    def clear_breakpoint_status(self) -> None:
        self.call(PTRACE_POKEUSER, DR6, 0)


def become_subreaper() -> None:
    """Become a child subreaper so the game process can be traced.

    The game is a grandchild of the launcher; with ptrace_scope=1 an unrelated
    process cannot attach to it. Reparenting the orphaned descendants to us
    makes the attach permitted.
    """
    pr_set_child_subreaper = 36
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    if libc.prctl(pr_set_child_subreaper, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "prctl(PR_SET_CHILD_SUBREAPER)")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--pid", type=int, help="attach to a running game process")
    parser.add_argument("--dll", required=True, help="DLL file name, e.g. version.dll")
    parser.add_argument("--exe", help="path to the game executable (required to launch)")
    parser.add_argument("--launcher", help="wine/proton binary (required to launch)")
    parser.add_argument("--compat", help="STEAM_COMPAT_DATA_PATH; contains pfx/ (to launch)")
    parser.add_argument("--steam-client", help="STEAM_COMPAT_CLIENT_INSTALL_PATH (to launch)")
    parser.add_argument("--overrides", help="WINEDLLOVERRIDES (default: <dll>=n,b)")
    parser.add_argument(
        "--mprotect-offset",
        type=lambda v: int(v, 0),
        default=DEFAULT_MPROTECT_OFFSET,
        help=f"EAC image-relative offset of the mprotect wrapper (default: {DEFAULT_MPROTECT_OFFSET:#x})",
    )
    parser.add_argument("--wait", type=float, default=60.0, help="seconds to wait for the game to start")
    parser.add_argument("--timeout", type=float, default=120.0, help="seconds to wait for the breakpoint")
    parser.add_argument("--keep-attached", action="store_true", help="stay attached until the timeout instead of detaching at once")
    parser.add_argument("--wait-after", type=float, default=30.0, help="seconds to keep watching after detaching, then re-check the header")
    args = parser.parse_args()

    launch = (args.exe, args.launcher, args.compat)
    if args.pid is None:
        missing = [name for name, value in zip(("--exe", "--launcher", "--compat"), launch) if not value]
        if missing:
            parser.error(f"{', '.join(missing)} required unless --pid is given")

    process = None
    if args.pid:
        pid = args.pid
        prefix = None
    else:
        become_subreaper()
        overrides = args.overrides or f"{args.dll}=n,b"
        prefix = str(Path(args.compat) / "pfx")
        env = os.environ.copy()
        env["STEAM_COMPAT_DATA_PATH"] = str(args.compat)
        if args.steam_client:
            env["STEAM_COMPAT_CLIENT_INSTALL_PATH"] = str(args.steam_client)
        env["WINEDLLOVERRIDES"] = overrides
        env["PROTON_LOG"] = "0"
        process = subprocess.Popen(
            [args.launcher, "run", str(Path(args.exe).with_name("launch.exe"))],
            env=env,
            cwd=str(Path(args.exe).parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"launched (pid {process.pid}); waiting for {args.exe}", flush=True)
        deadline = time.monotonic() + args.wait
        pid = None
        while time.monotonic() < deadline:
            pid = find_game_pid(args.exe, prefix)
            if pid:
                break
            time.sleep(0.02)
        if not pid:
            print("game process not found", file=sys.stderr)
            return 1

    print(f"attaching to pid {pid}", flush=True)
    tracer = Tracer(pid)
    blocked = False
    dll = None
    try:
        tracer.attach()

        maps = Path(f"/proc/{pid}/maps").read_text()
        base = eac_image_base(maps)
        if not base:
            print("EAC module not found in maps", file=sys.stderr)
            return 1
        dll = dll_mapping(maps, args.dll)
        guard = base + args.mprotect_offset
        print(f"EAC base {base:#x}; {args.dll} at {dll and hex(dll)}; breakpoint {guard:#x}", flush=True)
        tracer.set_exec_breakpoint(guard)
        tracer.call(PTRACE_CONT)

        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            waited, status = os.waitpid(pid, os.WNOHANG)
            if not waited:
                time.sleep(0.01)
                continue
            if os.WIFEXITED(status) or os.WIFSIGNALED(status):
                print("game exited before the wipe", flush=True)
                return 1
            if not os.WIFSTOPPED(status):
                continue
            sig = os.WSTOPSIG(status)
            regs = tracer.read_regs()
            if sig == signal.SIGTRAP and tracer.breakpoint_hit():
                current = dll_mapping(Path(f"/proc/{pid}/maps").read_text(), args.dll)
                if current is not None:
                    dll = current
                targeted = regs["rip"] == guard and regs["rdi"] == dll and regs["rsi"] == 0x1000
                print(
                    f"mprotect addr={regs['rdi']:#x} size={regs['rsi']:#x} "
                    f"prot={regs['rdx']:#x} -> {'DLL page' if targeted else 'other'}",
                    flush=True,
                )
                if targeted:
                    tracer.write_rdx(INVALID_PROT)
                    blocked = True
                    print("  prot rewritten to a value the kernel rejects; the wipe is skipped", flush=True)
                    if not args.keep_attached:
                        break
                tracer.clear_breakpoint_status()
            tracer.call(PTRACE_CONT, 0, 0 if sig == signal.SIGTRAP else sig)
    finally:
        try:
            tracer.detach()
        except OSError:
            pass

    if not blocked:
        print("did not intercept the wipe within the timeout", file=sys.stderr)
        return 1
    print("detached; the game keeps running", flush=True)

    if dll and args.wait_after > 0:
        print(f"watching for {args.wait_after:g}s ...", flush=True)
        deadline = time.monotonic() + args.wait_after
        alive = True
        while time.monotonic() < deadline:
            if not Path(f"/proc/{pid}").exists():
                alive = False
                break
            time.sleep(0.2)
        if not alive:
            print("game process ended while watching", file=sys.stderr)
            return 1
        try:
            fd = os.open(f"/proc/{pid}/mem", os.O_RDONLY)
            try:
                header = os.pread(fd, 2, dll)
            finally:
                os.close(fd)
            print(f"{args.dll} header after {args.wait_after:g}s: {header!r} (expected b'MZ')", flush=True)
            if header != b"MZ":
                return 1
        except OSError as error:
            print(f"could not re-read the header: {error}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
