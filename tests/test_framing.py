"""Tests for the PicoRelay framing protocol (single SOLO frames).

Run from the eticksheet root:  python tests/test_framing.py
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from protocol.framing import (  # noqa: E402
    FLAG_SOLO,
    HEADER_BYTES,
    PAYLOAD_PER_FRAME,
    FramingError,
    build_frame,
    parse_frame,
    parse_status,
    unwrap_frame,
)


def test_solo_frame_layout():
    payload = b"\x01\x02\x03\x04"
    frame = build_frame(payload, msg_id=7)
    flags, msg_id, seq, body = parse_frame(frame)
    assert flags == FLAG_SOLO
    assert msg_id == 7
    assert seq == 0
    assert body == payload
    assert len(frame) == HEADER_BYTES + len(payload)


def test_round_trip():
    payload = struct.pack("<HHIQ", 1, 0x4242, 1700000000, 0)
    assert unwrap_frame(build_frame(payload, msg_id=0x10)) == payload


def test_boundary_exact_frame_size():
    payload = b"x" * PAYLOAD_PER_FRAME
    assert unwrap_frame(build_frame(payload, msg_id=1)) == payload


def test_empty_payload():
    assert unwrap_frame(build_frame(b"", msg_id=1)) == b""


def test_oversize_payload_rejected():
    try:
        build_frame(b"x" * (PAYLOAD_PER_FRAME + 1), msg_id=1)
    except FramingError:
        pass
    else:
        raise AssertionError("expected FramingError for oversize payload")


def test_non_solo_frame_rejected():
    bad = bytes([0x80, 1, 0, 0]) + b"data"  # only START, not END
    try:
        unwrap_frame(bad)
    except FramingError:
        pass
    else:
        raise AssertionError("expected FramingError for non-SOLO frame")


def test_short_frame_rejected():
    try:
        parse_frame(b"\x01\x02")
    except FramingError:
        pass
    else:
        raise AssertionError("expected FramingError for short frame")


def test_parse_status():
    raw = bytes([1]) + (42).to_bytes(4, "little") + bytes([0x10]) + (510).to_bytes(2, "little")
    s = parse_status(raw)
    assert "EgressOk" in s and "counter=42" in s and "out_cap=510" in s


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except Exception as e:
            failed += 1
            print(f"  FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
