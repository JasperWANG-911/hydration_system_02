"""PicoRelay BLE framing — wrap/unwrap a single GATT write.

Frame layout (little-endian):
    byte 0     FLAGS   (bit7=START, bit6=END)
    byte 1     MSG_ID
    bytes 2-3  SEQ     (uint16, always 0 — we only send single frames)
    bytes 4..  PAYLOAD

Our events are all <= 16 bytes, so every write is a SOLO frame
(FLAGS = START|END). Shared by sender (build_frame) and receiver
(unwrap_frame).
"""
import struct

FLAG_START = 0x80
FLAG_END = 0x40
FLAG_SOLO = FLAG_START | FLAG_END
HEADER_BYTES = 4
PAYLOAD_PER_FRAME = 16  # 23-byte ATT MTU - 3 overhead - 4 header

_HEADER = struct.Struct("<BBH")  # FLAGS, MSG_ID, SEQ

STATUS_NAMES = {
    0: "Idle", 1: "EgressOk", 2: "EgressQueued", 3: "EgressFailed",
    4: "RateLimited", 5: "RxReceiving", 6: "RxOutOfSequence",
    7: "RxTimeout", 8: "RxOversize", 9: "MalformedHeader",
}


class FramingError(ValueError):
    """A byte stream violates the frame protocol."""


def build_frame(payload, msg_id):
    """Wrap `payload` in a single SOLO frame (bytes)."""
    if len(payload) > PAYLOAD_PER_FRAME:
        raise FramingError(f"payload {len(payload)}B exceeds {PAYLOAD_PER_FRAME}B single-frame limit")
    return _HEADER.pack(FLAG_SOLO, msg_id, 0) + bytes(payload)


def parse_frame(frame):
    """Split a frame into (flags, msg_id, seq, payload)."""
    if len(frame) < HEADER_BYTES:
        raise FramingError(f"frame shorter than header: {len(frame)}B")
    flags, msg_id, seq = _HEADER.unpack(frame[:HEADER_BYTES])
    return flags, msg_id, seq, frame[HEADER_BYTES:]


def unwrap_frame(frame):
    """Validate a SOLO frame and return its payload."""
    flags, _, _, payload = parse_frame(frame)
    if flags & FLAG_SOLO != FLAG_SOLO:
        raise FramingError(f"not a SOLO frame: flags=0x{flags:02x}")
    return payload


def parse_status(data):
    """Decode the 8-byte Status characteristic value."""
    if len(data) < 8:
        return f"<short status: {bytes(data)!r}>"
    status = data[0]
    counter = int.from_bytes(data[1:5], "little")
    last_msg = data[5]
    out_cap = int.from_bytes(data[6:8], "little")
    return f"{STATUS_NAMES.get(status, status)}  counter={counter}  last_msg=0x{last_msg:02x}  out_cap={out_cap}"
