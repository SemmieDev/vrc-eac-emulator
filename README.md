# EasyAntiCheat Emulator on VRChat

This project is a continuation of the original `vrc-eac-emulator`.

It includes bug fixes, improvements, and future enhancements.

> [!NOTE]
> This project does not, and will not, include a usage guide.
> 
> If you want to use it, you'll have to figure it out yourself.

## Running (Linux side)

`tools/eac_mprotect_guard.py` starts the game and stops EAC from doing two
things: erasing the first page of the game-local DLL, and registering that DLL
as a violation. It finds both patch points from code signatures and structural
checks instead of fixed offsets. If an EAC update changes those structures, the
tool stops without patching an uncertain location.

```sh
python3 tools/eac_mprotect_guard.py \
    --exe ~/VRChat/VRChat.exe \
    --dll ~/VRChat/version.dll \
    --launcher "$HOME/.local/share/Steam/steamapps/common/Proton - Experimental/proton" \
    --compat ~/proton \
    --steam-client ~/.steam/steam
```

`config.json` has to exist next to `VRChat.exe`, and the DLL has to sit in the
same directory as well.

## Libraries used

- [MinHook](https://github.com/TsudaKageyu/minhook) - TsudaKageyu
- [Plog](https://github.com/SergiusTheBest/plog) - Sergey Podobry
- [libhv](https://github.com/ithewei/libhv) - ithewei
- [JSON for Modern C++](https://github.com/nlohmann/json) - Niels Lohmann
- [base64](https://github.com/tobiaslocker/base64/tree/master) - Tobias Locker
