#pragma once

#include <memory>

#include "common/eos/eos_api.h"
#include "common/protocol/packet.h"

namespace anticheat_notification {
	// EOS_AntiCheatClient_BeginSession refuses to start without the outbound
	// transport, so bind it once even if the client has not asked for it yet.
	void ensure_message_to_server_bound(EOS_HAntiCheatClient handle, int32_t api_version);
}

class notify_message_to_server_handler {
public:
	static void handle(std::shared_ptr<packet> packet);
};
