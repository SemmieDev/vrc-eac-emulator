#include "notify_message_to_server_handler.h"

#include <atomic>

#include "../../eos/eos_anticheat.h"
#include "../../eos/eos_platform.h"
#include "../../servers/websocket_server.h"
#include "base64.hpp"
#include "common/eos/eos_anticheat_types.h"
#include "common/protocol/packets/notify_message_to_server_packet.h"

namespace {
	std::atomic_bool message_to_server_bound{false};
}

void notify_message_to_server_callback(const EOS_AntiCheatClient_OnMessageToServerCallbackInfo* data) {
	std::vector<char> message_bytes;
	for (int i = 0; i < data->MessageDataSizeBytes; i++) {
		message_bytes.push_back(static_cast<const char*>(data->MessageData)[i]);
	}
	auto message_base64 = base64::encode_into<std::string>(message_bytes.begin(), message_bytes.end());

	auto packet = std::make_shared<notify_message_to_server_packet>();
	packet->require_bind = false;
	packet->base64_message_data = nullable_string(message_base64);
	packet->message_data_size = data->MessageDataSizeBytes;
	websocket_server::send_packet(packet);
}

void anticheat_notification::ensure_message_to_server_bound(EOS_HAntiCheatClient handle, int32_t api_version) {
	if (message_to_server_bound.exchange(true)) {
		return;
	}

	EOS_AntiCheatClient_AddNotifyMessageToServerOptions options{};
	options.ApiVersion = api_version;
	eos_anticheat::add_notify_message_to_server(handle, options, nullptr, &notify_message_to_server_callback);
	PLOGI.printf("AntiCheatClient_AddNotifyMessageToServer bound");
}

void notify_message_to_server_handler::handle(std::shared_ptr<packet> packet) {
	auto notify_message_to_server = std::static_pointer_cast<notify_message_to_server_packet>(packet);

	auto anticheat_interface = eos_platform::get_anticheat_client_interface();
	if (anticheat_interface == NULL) {
		PLOGF.printf("Could not retrieve anticheat client interface");
		return;
	}

	anticheat_notification::ensure_message_to_server_bound(anticheat_interface, notify_message_to_server->options.ApiVersion);
}
