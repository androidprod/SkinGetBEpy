"""
Packets.py - Bedrock プロトコル パケット ID 定数
Mirrors: Bedrock/Packets.h
"""

# ── RakNet オフライン系 ────────────────────────────────────
RAKNET_UNCONNECTED_PING          = 0x01
RAKNET_UNCONNECTED_PING_OPEN     = 0x02
RAKNET_UNCONNECTED_PONG          = 0x1C
RAKNET_OPEN_CONNECTION_REQUEST1  = 0x05
RAKNET_OPEN_CONNECTION_REPLY1    = 0x06
RAKNET_OPEN_CONNECTION_REQUEST2  = 0x07
RAKNET_OPEN_CONNECTION_REPLY2    = 0x08

# ── RakNet 接続系 (Frame 内) ──────────────────────────────
RAKNET_CONNECTION_REQUEST        = 0x09
RAKNET_CONNECTION_REQUEST_ACCEPT = 0x10
RAKNET_NEW_INCOMING_CONNECTION   = 0x13
RAKNET_DISCONNECT                = 0x15
RAKNET_CONNECTED_PING            = 0x00
RAKNET_CONNECTED_PONG            = 0x03

# ── RakNet Frame Set ──────────────────────────────────────
RAKNET_FRAME_SET_MIN             = 0x80
RAKNET_FRAME_SET_MAX             = 0x8F
RAKNET_ACK                       = 0xC0
RAKNET_NACK                      = 0xA0
RAKNET_FRAME_SET_C1              = 0xC1

# ── Bedrock ゲームパケット ────────────────────────────────
GAME_PACKET_WRAPPER              = 0xFE  # ゲームパケットのラッパー

# ── Bedrock プロトコル パケット ID ─────────────────────────
PACKET_LOGIN                     = 0x01
PACKET_PLAY_STATUS               = 0x02
PACKET_DISCONNECT                = 0x05
PACKET_RESOURCE_PACKS_INFO       = 0x06
PACKET_RESOURCE_PACK_STACK       = 0x07
PACKET_RESOURCE_PACK_CLIENT_RESP = 0x08
PACKET_START_GAME                = 0x0B
PACKET_SET_TIME                  = 0x1C
PACKET_REQUEST_NETWORK_SETTINGS  = 0xC1
PACKET_NETWORK_SETTINGS          = 0x8F

# ── PlayStatus ステータス ──────────────────────────────────
PLAY_STATUS_LOGIN_SUCCESS        = 0
PLAY_STATUS_FAILED_CLIENT        = 1
PLAY_STATUS_FAILED_SPAWN         = 2
PLAY_STATUS_PLAYER_SPAWN         = 3

# ── ResourcePackClientResponse ────────────────────────────
RESOURCE_PACK_RESPONSE_REFUSED    = 1
RESOURCE_PACK_RESPONSE_SEND_PACKS = 2
RESOURCE_PACK_RESPONSE_HAVE_ALL   = 3
RESOURCE_PACK_RESPONSE_COMPLETED  = 4

# ─── RakNet マジックバイト ────────────────────────────────
RAKNET_MAGIC = bytes([
    0x00, 0xFF, 0xFF, 0x00,
    0xFE, 0xFE, 0xFE, 0xFE,
    0xFD, 0xFD, 0xFD, 0xFD,
    0x12, 0x34, 0x56, 0x78,
])
