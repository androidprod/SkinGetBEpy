"""
Buffer.py - Binary buffer reader/writer
Mirrors: Util/Buffer.cpp
"""
import struct


class Buffer:
    """バイナリデータの読み書きを管理するバッファクラス"""

    def __init__(self, data: bytes = b""):
        self.data = bytearray(data)
        self.pos = 0

    # ─── 読み込み ───────────────────────────────────────────

    def read(self, n: int) -> bytes:
        chunk = bytes(self.data[self.pos: self.pos + n])
        if len(chunk) < n:
            raise BufferError(f"読み込み不足: {n} バイト要求, {len(chunk)} バイトのみ利用可能")
        self.pos += n
        return chunk

    def read_uint8(self) -> int:
        return struct.unpack("B", self.read(1))[0]

    def read_uint16_be(self) -> int:
        return struct.unpack(">H", self.read(2))[0]

    def read_uint16_le(self) -> int:
        return struct.unpack("<H", self.read(2))[0]

    def read_uint24_le(self) -> int:
        b = self.read(3)
        return b[0] | (b[1] << 8) | (b[2] << 16)

    def read_uint32_be(self) -> int:
        return struct.unpack(">I", self.read(4))[0]

    def read_uint32_le(self) -> int:
        return struct.unpack("<I", self.read(4))[0]

    def read_int32_be(self) -> int:
        return struct.unpack(">i", self.read(4))[0]

    def read_uint64_be(self) -> int:
        return struct.unpack(">Q", self.read(8))[0]

    def read_int64_be(self) -> int:
        return struct.unpack(">q", self.read(8))[0]

    def read_bool(self) -> bool:
        return self.read_uint8() != 0

    def read_varstring(self) -> str:
        """varint 長さプレフィックス付き文字列 (Bedrock 形式)"""
        length = self.read_varint()
        return self.read(length).decode("utf-8", errors="replace")

    def read_string16(self) -> str:
        """Big-endian uint16 長さプレフィックス付き文字列"""
        length = self.read_uint16_be()
        return self.read(length).decode("utf-8", errors="replace")

    def read_varint(self) -> int:
        """unsigned varint (Little-Endian Base-128)"""
        result = 0
        shift = 0
        while True:
            b = self.read_uint8()
            result |= (b & 0x7F) << shift
            shift += 7
            if not (b & 0x80):
                break
        return result

    def read_zigzag_varint(self) -> int:
        """ZigZag エンコードされた signed varint"""
        n = self.read_varint()
        return (n >> 1) ^ -(n & 1)

    def read_float_le(self) -> float:
        return struct.unpack("<f", self.read(4))[0]

    def read_address(self) -> tuple:
        """RakNet アドレス形式を読み込む"""
        version = self.read_uint8()
        if version == 4:
            # XOR なしの単純な読み込み (C++ 版に合わせる)
            parts = [str(self.read_uint8()) for _ in range(4)]
            # ポートは Network Order (Big Endian)
            port = self.read_uint16_be()
            return ".".join(parts), port
        else:
            # IPv6
            self.read(2)   # AF_INET6 family
            port = self.read_uint16_be()
            self.read(4)   # flow info
            addr_bytes = self.read(16)
            self.read(4)   # scope id
            import ipaddress
            return str(ipaddress.ip_address(addr_bytes)), port

    # ─── 書き込み ───────────────────────────────────────────

    def write(self, b: bytes):
        self.data += b

    def write_uint8(self, v: int):
        self.data += struct.pack("B", v)

    def write_uint16_be(self, v: int):
        self.data += struct.pack(">H", v)

    def write_uint16_le(self, v: int):
        self.data += struct.pack("<H", v)

    def write_uint24_le(self, v: int):
        self.data += bytes([v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF])

    def write_uint32_be(self, v: int):
        self.data += struct.pack(">I", v)

    def write_uint32_le(self, v: int):
        self.data += struct.pack("<I", v)

    def write_int32_be(self, v: int):
        self.data += struct.pack(">i", v)

    def write_uint64_be(self, v: int):
        self.data += struct.pack(">Q", v)

    def write_int64_be(self, v: int):
        self.data += struct.pack(">q", v)

    def write_bool(self, v: bool):
        self.write_uint8(1 if v else 0)

    def write_string16(self, s: str):
        """Big-endian uint16 長さプレフィックス付き文字列"""
        b = s.encode("utf-8")
        self.write_uint16_be(len(b))
        self.data += b

    def write_varstring(self, s: str):
        """varint 長さプレフィックス付き文字列 (Bedrock 形式)"""
        b = s.encode("utf-8")
        self.write_varint(len(b))
        self.data += b

    def write_varint(self, v: int):
        """unsigned varint"""
        while True:
            b = v & 0x7F
            v >>= 7
            if v:
                self.data += bytes([b | 0x80])
            else:
                self.data += bytes([b])
                break

    def write_zigzag_varint(self, v: int):
        """ZigZag エンコードされた signed varint"""
        encoded = (v << 1) ^ (v >> 31)
        self.write_varint(encoded & 0xFFFFFFFF)

    def write_float_le(self, v: float):
        self.data += struct.pack("<f", v)

    def write_address(self, ip: str, port: int, version: int = 4):
        """RakNet アドレス形式で書き込む (C++ 版に合わせる)"""
        self.write_uint8(version)
        if version == 4:
            parts = [int(x) for x in ip.split(".")]
            for p in parts:
                self.write_uint8(p & 0xFF)
            # ポートは Big Endian
            self.write_uint16_be(port)
        else:
            import ipaddress
            self.write_uint16_le(23)  # AF_INET6
            self.write_uint16_be(port)
            self.write_uint32_be(0)
            self.data += ipaddress.ip_address(ip).packed
            self.write_uint32_be(0)

    # ─── ユーティリティ ─────────────────────────────────────

    def remaining(self) -> int:
        return max(0, len(self.data) - self.pos)

    def get_bytes(self) -> bytes:
        return bytes(self.data)

    def rest(self) -> bytes:
        return bytes(self.data[self.pos:])

    def __len__(self) -> int:
        return len(self.data)
