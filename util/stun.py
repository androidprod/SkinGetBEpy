"""
stun.py - NAT Discovery via STUN
Mirrors: discoverExternalIP in main.cpp
"""
import socket
import struct
import random
import time
from util.logger import Logger

log = Logger("STUN")

STUN_SERVERS = [
    ("stun.l.google.com", 19302),
    ("stun1.l.google.com", 19302),
    ("stun.sipgate.net", 3478),
]

def discover_external_ip(sock_unused: socket.socket = None) -> str:
    """
    STUN (Binding Request) を送信して外部 IP を取得する。
    サーバーソケットとの干渉を避けるため、独自のソケットを作成する。
    """
    log.info("外部 IP を検出中 (STUN)...")
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2.0)

    try:
        for host, port in STUN_SERVERS:
            try:
                addr = socket.gethostbyname(host)
            except Exception:
                continue

            # STUN Binding Request (RFC 5389 / 8489)
            # Message Type: 0x0001 (Binding Request)
            # Message Length: 0x0000
            # Magic Cookie: 0x2112A442
            # Transaction ID: 12 bytes random
            transaction_id = bytes([random.randint(0, 255) for _ in range(12)])
            request = struct.pack(">HH I", 0x0001, 0x0000, 0x2112A442) + transaction_id

            try:
                sock.sendto(request, (addr, port))
                data, _ = sock.recvfrom(512)
            except (socket.timeout, Exception):
                continue

            if len(data) < 20:
                continue

            try:
                msg_type, msg_len, magic = struct.unpack(">HHI", data[:8])
            except struct.error:
                continue
            if msg_type != 0x0101: # Binding Response
                continue

            # 属性パース
            pos = 20
            while pos + 4 <= len(data):
                attr_type = (data[pos] << 8) | data[pos+1]
                attr_len  = (data[pos+2] << 8) | data[pos+3]
                pos += 4
                
                if attr_type == 0x0020: # XOR-MAPPED-ADDRESS
                    if attr_len >= 8:
                        # Skip family (1 byte) + port (2 bytes)
                        # We only care about IPv4 for now as per main.cpp
                        ip_bytes = data[pos+4:pos+8]
                        ip_int = struct.unpack(">I", ip_bytes)[0]
                        ip_int ^= 0x2112A442  # XOR with Magic Cookie
                        
                        ext_ip = f"{(ip_int >> 24) & 0xFF}.{(ip_int >> 16) & 0xFF}.{(ip_int >> 8) & 0xFF}.{ip_int & 0xFF}"
                        log.ok(f"外部 IP 検出完了: {ext_ip} (via {host})")
                        return ext_ip
                
                pos += (attr_len + 3) & ~3 # 4-byte padding

    except Exception as e:
        log.debug(f"STUN エラー: {e}")
    finally:
        sock.close()

    log.warn("外部 IP の検出に失敗しました (NAT 内部、または STUN サーバーに到達不能)")
    return "Unknown (Maybe behind strict NAT)"
