#include "bootstrapper.h"

#include <MinHook.h>
#include <common/constants.h>
#include <plog/Formatters/TxtFormatter.h>
#include <plog/Log.h>
#include <shlwapi.h>

#include "api/api_handler_registry.h"
#include "common/eos/eos_anticheat_types.h"
#include "eos/eos.h"
#include "eos/eos_anticheat.h"
#include "eos/eos_platform.h"
#include "handlers/handler_registry.h"
#include "servers/http_server.h"
#include "servers/websocket_server.h"

int bootstrapper::Dummy_WinMain() {
	handler_registry::init();
	api_handler_registry::init();

	websocket_server::launch(tcp_port);
	http_server::run(http_port);

	while (true) {
		if (!websocket_server::has_connection()) {
			// Ensure AntiCheat session has ended, in case of abnormal client shutdowns
			auto anticheat_interface = eos_platform::get_anticheat_client_interface();

			if (anticheat_interface == NULL) {
				PLOGF.printf("Could not retrieve anticheat client interface");
			} else {
				EOS_AntiCheatClient_EndSessionOptions options{};
				options.ApiVersion = EOS_ANTICHEATCLIENT_ENDSESSION_API_LATEST;
				auto result = eos_anticheat::end_session(anticheat_interface, options);

				if (result == EOS_Success) {
					PLOGI.printf("Abnormal client shutdown detected, AntiCheat session automatically ended");
				} else if (result != EOS_NotConfigured) {
					PLOGE.printf("Abnormal client shutdown detected, but failed to automatically end AntiCheat session (result=%d)", result);
				}
			}

			websocket_server::wait_for_connection();
			PLOGI.printf("Client reconnected. Resuming EOS loop");
		}

		Sleep(1000 / 30);  // emulate 30 fps

		websocket_server::tick();
		if (eos::is_eos_initialized() && eos_platform::is_platform_created()) {
			eos_platform::tick();
		}
	}
}

void bootstrapper::hook_winmain() {
	MH_Initialize();

	HMODULE unityPlayerHandle = GetModuleHandleA("UnityPlayer.dll");
	void* unityMain = GetProcAddress(unityPlayerHandle, "UnityMain");
	PLOGD.printf("UnityPlayer.dll WinMain=%llx", unityMain);

	void* originalFunc;
	if (MH_CreateHook(unityMain, &Dummy_WinMain, &originalFunc) != MH_OK || MH_EnableHook(unityMain) != MH_OK) {
		PLOGF.printf("Failed to create hook of UnityMain");
		return;
	}
	PLOGI.printf("Created WinMain function hook");
	PLOGI.printf("Waiting for the main process...");
}

void bootstrapper::main(int tcp_port, int http_port) {
	bootstrapper::tcp_port = tcp_port;
	bootstrapper::http_port = http_port;

	if (LoadLibraryA(EOS_SDK_PATH) == nullptr) {
		PLOGF.printf("Failed to perform LoadLibraryA EOSSDK-Win64-Shipping.dll!");
		return;
	}
	PLOGD.printf("EOSSDK-Win64-Shipping.dll Loaded");

	hook_winmain();
}
