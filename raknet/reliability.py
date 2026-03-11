"""
Reliability.py - RakNet 信頼制御 / Split Packet 再構成
Mirrors: RakNet/Reliability.cpp

numpy を使用:
  - Split Packet の高速バイト結合 (np.frombuffer + np.concatenate)
  - ACK/NACK シーケンス番号のレンジ圧縮 (np.diff)
"""
import numpy as np
from util.buffer import Buffer
from util.logger import Logger

log = Logger("Reliability")

# Reliability 定数
UNRELIABLE                = 0
UNRELIABLE_SEQUENCED      = 1
RELIABLE                  = 2
RELIABLE_ORDERED          = 3
RELIABLE_SEQUENCED        = 4
UNRELIABLE_WITH_ACK       = 5
RELIABLE_WITH_ACK         = 6
RELIABLE_ORDERED_WITH_ACK = 7

_RELIABLE_SET  = frozenset([RELIABLE, RELIABLE_ORDERED, RELIABLE_SEQUENCED,
                             RELIABLE_WITH_ACK, RELIABLE_ORDERED_WITH_ACK])
_ORDERED_SET   = frozenset([RELIABLE_ORDERED, RELIABLE_ORDERED_WITH_ACK])
_SEQUENCED_SET = frozenset([UNRELIABLE_SEQUENCED, RELIABLE_SEQUENCED])


class Frame:
    __slots__ = [
        "reliability", "has_split", "length_bits",
        "reliable_msg_index", "sequenced_msg_index",
        "order_index", "order_channel",
        "split_count", "split_id", "split_index",
        "body",
    ]

    def __init__(self):
        self.reliability = RELIABLE_ORDERED
        self.has_split = False
        self.length_bits = 0
        self.reliable_msg_index = 0
        self.sequenced_msg_index = 0
        self.order_index = 0
        self.order_channel = 0
        self.split_count = 0
        self.split_id = 0
        self.split_index = 0
        self.body = b""

    def is_reliable(self):  return self.reliability in _RELIABLE_SET
    def is_ordered(self):   return self.reliability in _ORDERED_SET
    def is_sequenced(self): return self.reliability in _SEQUENCED_SET


def parse_frame_set(data: bytes) -> tuple:
    buf = Buffer(data)
    _pid    = buf.read_uint8()
    seq_num = buf.read_uint24_le()
    frames  = []

    while buf.remaining() > 0:
        frame = Frame()
        flags = buf.read_uint8()
        frame.reliability = (flags >> 5) & 0x07
        frame.has_split   = bool(flags & 0x10)
        frame.length_bits = buf.read_uint16_be()
        byte_len = (frame.length_bits + 7) // 8

        if frame.is_reliable():
            frame.reliable_msg_index = buf.read_uint24_le()
        if frame.is_sequenced():
            frame.sequenced_msg_index = buf.read_uint24_le()
        if frame.is_ordered():
            frame.order_index   = buf.read_uint24_le()
            frame.order_channel = buf.read_uint8()
        if frame.has_split:
            frame.split_count = buf.read_uint32_be()
            frame.split_id    = buf.read_uint16_be()
            frame.split_index = buf.read_uint32_be()

        frame.body = buf.read(byte_len)
        frames.append(frame)

    return seq_num, frames


def build_frame(body: bytes, reliability: int = RELIABLE_ORDERED,
                reliable_msg_index: int = 0,
                order_index: int = 0, order_channel: int = 0) -> bytes:
    buf = Buffer()
    buf.write_uint8((reliability & 0x07) << 5)
    buf.write_uint16_be(len(body) * 8)
    if reliability in _RELIABLE_SET:
        buf.write_uint24_le(reliable_msg_index)
    if reliability in _ORDERED_SET:
        buf.write_uint24_le(order_index)
        buf.write_uint8(order_channel)
    buf.write(body)
    return buf.get_bytes()


def build_frame_set(sequence_number: int, frame_data: bytes) -> bytes:
    buf = Buffer()
    buf.write_uint8(0x84)
    buf.write_uint24_le(sequence_number)
    buf.write(frame_data)
    return buf.get_bytes()


def _build_range_packet(header: int, seq_nums: list) -> bytes:
    """numpy の np.diff でシーケンス番号をレンジに圧縮して ACK/NACK をビルド"""
    if not seq_nums:
        return b""

    arr = np.unique(np.array(seq_nums, dtype=np.uint32))

    if len(arr) == 1:
        ranges = [(int(arr[0]), int(arr[0]))]
    else:
        gaps   = np.where(np.diff(arr) > 1)[0]
        starts = np.concatenate([[0], gaps + 1])
        ends   = np.concatenate([gaps, [len(arr) - 1]])
        ranges = [(int(arr[s]), int(arr[e])) for s, e in zip(starts, ends)]

    buf = Buffer()
    buf.write_uint8(header)
    buf.write_uint16_be(len(ranges))
    for s, e in ranges:
        single = (s == e)
        buf.write_bool(single)
        buf.write_uint24_le(s)
        if not single:
            buf.write_uint24_le(e)

    return buf.get_bytes()


def build_ack(seq_nums: list) -> bytes:
    return _build_range_packet(0xC0, seq_nums)


def build_nack(seq_nums: list) -> bytes:
    return _build_range_packet(0xA0, seq_nums)


class SplitPacketAssembler:
    """
    Split Packet の再構成。
    numpy.frombuffer + numpy.concatenate で高速バイト結合。
    """

    def __init__(self):
        self._buffers: dict = {}

    def add(self, frame: Frame) -> bytes | None:
        sid = frame.split_id
        if sid not in self._buffers:
            self._buffers[sid] = {"count": frame.split_count, "parts": {}}

        entry = self._buffers[sid]
        entry["parts"][frame.split_index] = frame.body
        total = entry["count"]

        if len(entry["parts"]) >= total:
            arrays = [np.frombuffer(entry["parts"][i], dtype=np.uint8) for i in range(total)]
            combined = np.concatenate(arrays)
            del self._buffers[sid]
            log.debug(f"Split Packet 再構成: ID={sid}, {total}パーツ, {combined.nbytes} bytes")
            return combined.tobytes()

        return None