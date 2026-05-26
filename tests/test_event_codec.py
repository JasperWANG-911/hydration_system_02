"""Round-trip tests for the application event codec.

Run from the eticksheet root:  python tests/test_event_codec.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from event_codec import (  # noqa: E402
    EVENT_INTAKE,
    EVENT_LIGHT,
    EVENT_RESET,
    PAYLOAD_SIZE,
    Event,
    EventCodecError,
    decode,
    encode,
    intake,
    light,
    reset,
)


def test_payload_fits_one_frame():
    assert PAYLOAD_SIZE == 7
    assert PAYLOAD_SIZE <= 16  # always a single SOLO frame


def test_intake_add_round_trip():
    ev = intake(50, ts=1700000000)
    raw = encode(ev)
    assert len(raw) == 7
    back = decode(raw)
    assert back.type == EVENT_INTAKE
    assert back.arg == 50
    assert back.ts == 1700000000


def test_intake_negative_round_trip():
    back = decode(encode(intake(-50)))
    assert back.type == EVENT_INTAKE
    assert back.arg == -50  # signed int16 survives


def test_light_round_trip():
    assert decode(encode(light(True))).arg == 1
    assert decode(encode(light(False))).arg == 0
    assert decode(encode(light(True))).type == EVENT_LIGHT


def test_reset_round_trip():
    assert decode(encode(reset(shutdown=True))).arg == 1
    assert decode(encode(reset(shutdown=False))).arg == 0
    assert decode(encode(reset(shutdown=False))).type == EVENT_RESET


def test_type_name():
    assert intake(50).type_name == "INTAKE"
    assert light(True).type_name == "LIGHT"
    assert reset(True).type_name == "RESET"


def test_unknown_type_rejected():
    raw = encode(Event(type=0x7F, arg=0))
    try:
        decode(raw)
    except EventCodecError:
        pass
    else:
        raise AssertionError("expected EventCodecError for unknown type")


def test_short_payload_rejected():
    try:
        decode(b"\x01\x02")
    except EventCodecError:
        pass
    else:
        raise AssertionError("expected EventCodecError for short payload")


def test_arg_out_of_range_rejected():
    try:
        encode(Event(type=EVENT_INTAKE, arg=40000))  # > int16 max
    except EventCodecError:
        pass
    else:
        raise AssertionError("expected EventCodecError for oversized arg")


def test_end_to_end_through_framing():
    """The real path: event -> payload -> frame -> payload -> event."""
    from framing import build_frame, unwrap_frame

    ev = intake(-50, ts=1700000123)
    frame = build_frame(encode(ev), msg_id=0x10)
    recovered = decode(unwrap_frame(frame))
    assert recovered == ev


if __name__ == "__main__":
    import sys

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
