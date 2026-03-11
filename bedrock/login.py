"""
Login.py - Bedrock Login パケット処理 + レスポンス生成
Mirrors: Bedrock/Login.cpp + PlayStatus.cpp
"""
import zlib
import json
import struct

from util.buffer import Buffer
from util.logger import Logger
from bedrock.packets import (
    PACKET_LOGIN,
    PACKET_PLAY_STATUS,
    PACKET_DISCONNECT,
    PACKET_RESOURCE_PACKS_INFO,
    PACKET_RESOURCE_PACK_STACK,
    PACKET_START_GAME,
    PACKET_NETWORK_SETTINGS,
    PLAY_STATUS_LOGIN_SUCCESS,
    PLAY_STATUS_PLAYER_SPAWN,
    GAME_PACKET_WRAPPER,
)
from crypto.jwt_util import decode_payload, extract_chain
from bedrock.skin import SkinData, extract_skin_from_jwt

log = Logger("Login")


# ────────────────────────────────────────────────────────────
#  ゲームパケットのラップ / アンラップ
# ────────────────────────────────────────────────────────────

def wrap_game_packet(payload: bytes, compress: bool = True) -> bytes:
    """
    Bedrock ゲームパケットをラップする。
    C++ 版の makeBatch / sendFrame 相当。

    compress=False: 0xFE + payload (NetworkSettings 応答用、algo byte なし)
    compress=True:  0xFE + 0x00 (algo=deflate) + raw deflate(payload)
    """
    if not compress:
        # NetworkSettings 応答: 0xFE + そのまま (varint長 + サブパケット)
        return bytes([GAME_PACKET_WRAPPER]) + payload
    else:
        # 圧縮有効後: 0xFE + algo(0x00) + raw deflate
        compressor = zlib.compressobj(
            level=zlib.Z_DEFAULT_COMPRESSION,
            method=zlib.DEFLATED,
            wbits=-15,  # raw deflate (no zlib header)
        )
        body = compressor.compress(payload) + compressor.flush()
        return bytes([GAME_PACKET_WRAPPER, 0x00]) + body


def unwrap_game_packet(data: bytes) -> bytes:
    """
    ゲームパケット (0xFE + payload) をアンラップして解凍されたデータを返す。
    C++ 版の processGamePacket 相当。

    0xFE の次の1バイトで判別:
      0x00 -> algo=deflate: 残りを raw deflate 解凍
      0xFF -> 無圧縮: 残りをそのまま返す
      それ以外 -> varint 長さプレフィクス付きサブパケット (初回 NetworkSettings 要求時)
    """
    if not data or data[0] != GAME_PACKET_WRAPPER:
        raise ValueError(f"不正なゲームパケットヘッダ: 0x{data[0]:02X}")

    payload = data[1:]
    if not payload:
        return b""

    algo = payload[0]

    if algo == 0x00:
        # algo byte = 0x00 → raw deflate (wbits=-15)
        try:
            return zlib.decompress(payload[1:], wbits=-15)
        except zlib.error:
            pass
        # フォールバック: zlib auto-detect
        try:
            return zlib.decompress(payload[1:], wbits=47)
        except zlib.error:
            pass
        return payload[1:]

    elif algo == 0xFF:
        # algo byte = 0xFF → 無圧縮
        return payload[1:]

    else:
        # algo byte なし (初回の REQUEST_NETWORK_SETTINGS 等)
        # まず zlib を試す
        if algo == 0x78:
            try:
                return zlib.decompress(payload)
            except zlib.error:
                pass

        # raw deflate を試す
        try:
            return zlib.decompress(payload, wbits=-15)
        except zlib.error:
            pass

        # 無圧縮として扱う
        return payload


def read_packets(decompressed: bytes) -> list:
    """
    解凍済みデータから Bedrock サブパケットを読み込む。
    """
    buf = Buffer(decompressed)
    packets = []
    while buf.remaining() > 0:
        try:
            length = buf.read_varint()
            pkt_data = buf.read(length)
            packets.append(pkt_data)
        except BufferError:
            break
    return packets


def make_sub_packet(payload: bytes) -> bytes:
    """varint 長さプレフィックスを付けてサブパケットをビルド"""
    buf = Buffer()
    buf.write_varint(len(payload))
    buf.write(payload)
    return buf.get_bytes()


# ────────────────────────────────────────────────────────────
#  Login パケット解析
# ────────────────────────────────────────────────────────────

def parse_login_packet(pkt_data: bytes) -> SkinData | None:
    """
    Login パケット (ID=0x01) を解析してスキンデータを返す。
    main.cpp の tryParseLogin と同様に、複数のバリエーションを試行する。
    """
    def try_parse(data: bytes, protocol_varint: bool, has_payload_len: bool) -> SkinData | None:
        try:
            buf = Buffer(data)
            # Packet ID
            _pid = buf.read_varint()
            
            # Protocol Version
            if protocol_varint:
                protocol = buf.read_varint()
            else:
                protocol = buf.read_int32_be()
            
            if has_payload_len:
                _payload_len = buf.read_varint()
            
            # Chain Data
            chain_len = buf.read_uint32_le()
            if chain_len == 0 or buf.remaining() < chain_len:
                return None
            chain_raw = buf.read(chain_len).decode("utf-8", errors="replace")
            
            # Skin Data
            skin_len = buf.read_uint32_le()
            if skin_len == 0 or buf.remaining() < skin_len:
                return None
            skin_raw = buf.read(skin_len).decode("utf-8", errors="replace")
            
            # 簡易検証: chain に '{' が含まれ、skin が '.' を2つ含む (JWT)
            if "{" not in chain_raw or skin_raw.count(".") != 2:
                return None
                
            log.info(f"Login パケット解析成功 (protocol={protocol}, varint={protocol_varint}, payload_len={has_payload_len})")
            chain_tokens = extract_chain(chain_raw)
            return extract_skin_from_jwt(skin_raw, chain_tokens)
        except Exception:
            return None

    # main.cpp と同様に4つの組み合わせを試す
    for pv in [False, True]:
        for pl in [False, True]:
            res = try_parse(pkt_data, protocol_varint=pv, has_payload_len=pl)
            if res:
                return res
                
    log.error("Login パケットの全解析パターンが失敗しました")
    return None


# ────────────────────────────────────────────────────────────
#  レスポンス パケット生成
# ────────────────────────────────────────────────────────────

def make_network_settings() -> bytes:
    """NetworkSettings パケット生成 (0x8F) - Wrapped"""
    return wrap_game_packet(_make_network_settings(), compress=False)


def _make_network_settings() -> bytes:
    """NetworkSettings サブパケット生成"""
    buf = Buffer()
    buf.write_varint(PACKET_NETWORK_SETTINGS)
    buf.write_uint16_le(1)   # compression threshold
    buf.write_uint16_le(0)   # compression algo: 0=deflate
    buf.write_bool(False)     # client throttle
    buf.write_uint8(0)       # threshold
    buf.write_float_le(0.0)  # multiplier
    return make_sub_packet(buf.get_bytes())


def make_play_status(status: int) -> bytes:
    """PlayStatus パケット生成 (0x02) - Wrapped"""
    return wrap_game_packet(_make_play_status(status))


def _make_play_status(status: int) -> bytes:
    """PlayStatus サブパケット生成"""
    buf = Buffer()
    buf.write_varint(PACKET_PLAY_STATUS)
    buf.write_int32_be(status)
    return make_sub_packet(buf.get_bytes())


def make_disconnect(message: str) -> bytes:
    """Disconnect パケット生成 (0x05) - Wrapped"""
    return wrap_game_packet(_make_disconnect(message))


def _make_disconnect(message: str) -> bytes:
    """Disconnect サブパケット生成"""
    buf = Buffer()
    buf.write_varint(PACKET_DISCONNECT)
    buf.write_zigzag_varint(0) # reason: 0 (Disconnect)
    buf.write_bool(False)      # hide disconnection screen
    buf.write_varstring(message)
    buf.write_varstring(message) # filtered message
    return make_sub_packet(buf.get_bytes())


def make_resource_packs_info() -> bytes:
    """ResourcePacksInfo パケット (0x06)"""
    buf = Buffer()
    buf.write_varint(PACKET_RESOURCE_PACKS_INFO)
    buf.write_bool(False)   # must_accept
    buf.write_bool(False)   # has_scripts
    buf.write_bool(False)   # force_server_packs
    buf.write_uint16_le(0)  # behavior pack count
    buf.write_uint16_le(0)  # resource pack count
    payload = buf.get_bytes()
    return wrap_game_packet(make_sub_packet(payload))


def make_resource_pack_stack() -> bytes:
    """ResourcePackStack パケット (0x07)"""
    buf = Buffer()
    buf.write_varint(PACKET_RESOURCE_PACK_STACK)
    buf.write_bool(False)   # must_accept
    buf.write_varint(0)     # addon count
    buf.write_varint(0)     # resource pack count
    buf.write_string16("*") # game_version
    buf.write_uint32_le(0)  # experiment count
    buf.write_bool(False)   # has_editor_packs
    payload = buf.get_bytes()
    return wrap_game_packet(make_sub_packet(payload))
