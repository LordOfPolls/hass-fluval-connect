"""Offline check: real captured frames must decode, commands must encode to known bytes.

Run with: python3 test_fluval.py  (needs cbor2)
"""

import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "custom_components" / "fluval"))
from protocol import (  # noqa: E402
    DAYS, encode, encode_channels, encode_clock, encode_pro, levels_now,
    levels_to_rgbw, mix_to_levels, next_transitions, parse_levels, parse_status,
    rgbw_to_levels, simple_schedule, status_map, transitions,
)

# Real D0 FF dump captured from Roma&Shaker2.0_6AC0B2: Pro mode, on, 12 schedule points.
DUMP = bytes.fromhex(
    "d2af000f010202f50318640400050006000e0008430800 3c0943120 03c0a42140"
    "00b44646447640c440000050 00d58490c051e00000000052d00000500061e140014"
    "00071e505039500c00505039500c14323224320d28323224320e0064644764110064"
    "644764131e64644764141e00004700151e000000000f467f0c000e0001".replace(" ", "")
)


def test_decode():
    s = parse_status(DUMP)
    assert s["mode"] == "pro" and s["power"] is True
    assert s["firmware"] == 15
    assert s["channel_count"] == 4
    assert s["channels"] == {"red": 100, "green": 0, "blue": 0, "white": 0}
    assert s["weather"] == "off"
    # 7f 0c00 0e00 01: bit7 clear -> disabled, bits 0-6 set -> every day, 12:00-14:00.
    assert s["dynamic_effects"] == [{"enabled": False, "days": list(DAYS),
                                     "from": "12:00", "to": "14:00", "effect": "storm"}]
    assert s["sunrise"] == {"at": "08:00", "ramp_min": 60}
    assert s["sunset"] == {"at": "18:00", "ramp_min": 60}
    assert s["sleep"] == "20:00"
    assert s["day_levels"] == [100, 100, 71, 100]
    assert s["night_levels"] == [0, 0, 5, 0]
    assert len(s["pro_schedule"]) == 12
    assert s["pro_schedule"][0] == {"at": "05:30", "levels": [0, 0, 0, 0]}
    assert s["pro_schedule"][-1] == {"at": "21:30", "levels": [0, 0, 0, 0]}

    for bad in (b"", b"\xd1\xa1", bytes.fromhex("d0ff")):
        try:
            parse_status(bad)
        except ValueError:
            continue
        raise AssertionError(f"should have rejected {bad.hex()}")
    print("decode ok")


def test_encode():
    assert encode(power=True) == bytes.fromhex("d1a102f5")
    assert encode(power=False) == bytes.fromhex("d1a102f4")
    assert encode(mode=0) == bytes.fromhex("d1a10100")
    assert encode(mode=2) == bytes.fromhex("d1a10102")
    assert encode(weather=3) == bytes.fromhex("d1a10e03")
    assert encode(preview=1440) == bytes.fromhex("d1a118331905a0")
    # Channel writes carry key 14 = 0, cancelling any running weather effect.
    assert encode_channels([100, 0, 0, 0]) == bytes.fromhex("d1a50318640400050006000e00")

    # 2026-08-30 is a Sunday -> ISO weekday 7.
    assert encode_clock(datetime(2026, 8, 30, 14, 5, 9)) == bytes.fromhex("cd1a081e070e0509")
    # 2026-01-01 is a Thursday -> 4.
    assert encode_clock(datetime(2026, 1, 1, 0, 0, 0)) == bytes.fromhex("cd1a010104000000")

    # A written Pro schedule must decode back to exactly what went in.
    points = [(7, 30, [80, 80, 57, 80]), (5, 30, [0, 0, 0, 0]),
              (12, 0, [80, 80, 57, 80]), (21, 30, [0, 0, 0, 0])]
    frame = encode_pro(points)
    back = parse_status(b"\xd2" + frame[1:])["pro_schedule"]
    assert [p["at"] for p in back] == ["05:30", "07:30", "12:00", "21:30"], back  # sorted
    assert back[1]["levels"] == [80, 80, 57, 80]
    # The light silently ignores a schedule outside 4..12 points, so reject it here.
    ok = [(h, 0, [0, 0, 0, 0]) for h in range(4)]
    for bad in ([(24, 0, [0, 0, 0, 0])] + ok, [(0, 0, [0, 0, 0])] + ok,
                [(1, 0, [0, 0, 0, 101])] + ok, [], ok[:3],
                [(h, 0, [0, 0, 0, 0]) for h in range(13)]):
        try:
            encode_pro(bad)
        except ValueError:
            continue
        raise AssertionError(f"should have rejected {bad}")
    print("encode ok")


def test_schedule():
    s = parse_status(DUMP)
    # Dark from 21:30, ramping up out of the all-zero 05:30 point.
    assert transitions(s) == ([5 * 60 + 30], [21 * 60 + 30])

    on, off = next_transitions(s, datetime(2026, 8, 30, 4, 0))
    assert (on.hour, on.minute) == (5, 30) and on.day == 30
    assert (off.hour, off.minute) == (21, 30) and off.day == 30
    # Past both of today's transitions, they roll over to tomorrow.
    on, off = next_transitions(s, datetime(2026, 8, 30, 22, 0))
    assert on.day == 31 and (on.hour, on.minute) == (5, 30)
    assert off.day == 31 and (off.hour, off.minute) == (21, 30)
    # Verified on hardware: the light reports power False while a Pro schedule keeps
    # running, so the power flag must not blank the sensors.
    assert next_transitions({**s, "power": False},
                            datetime(2026, 8, 30, 4, 0)) == next_transitions(
                                s, datetime(2026, 8, 30, 4, 0))
    # Manual mode has no schedule at all.
    assert next_transitions({**s, "mode": "manual"}, datetime(2026, 8, 30, 4, 0)) == (None, None)

    # Auto mode: rises at sunrise, off at the sleep time.
    auto = {**s, "mode": "auto"}
    assert transitions(auto) == ([8 * 60], [20 * 60])
    # No sleep time and non-zero night levels means it never reaches zero.
    assert transitions({**auto, "sleep": None}) == ([8 * 60], [])
    # No sleep time but dark nights: off when the sunset ramp finishes.
    assert transitions({**auto, "sleep": None, "night_levels": [0, 0, 0, 0]}) \
        == ([8 * 60], [18 * 60])

    # A schedule that never goes dark, and one that never lights up.
    lit = {"mode": "pro", "power": True,
           "pro_schedule": [{"at": "00:00", "levels": [1, 0, 0, 0]},
                            {"at": "12:00", "levels": [9, 0, 0, 0]}]}
    assert transitions(lit) == ([], [])
    dark = {"mode": "pro", "power": True,
            "pro_schedule": [{"at": "00:00", "levels": [0, 0, 0, 0]},
                             {"at": "12:00", "levels": [0, 0, 0, 0]}]}
    assert transitions(dark) == ([], [])

    # Across a DST change the next time must carry the offset of the day it lands on,
    # or the timestamp sensors sit an hour out for a day. BST starts 2026-03-29.
    tz = ZoneInfo("Europe/London")
    on, _ = next_transitions(s, datetime(2026, 3, 28, 23, 0, tzinfo=tz))
    assert (on.day, on.hour, on.minute) == (29, 5, 30), on
    assert on.utcoffset() == timedelta(hours=1), on.utcoffset()
    # Live output is interpolated between the surrounding breakpoints.
    assert levels_now(s, datetime(2026, 8, 30, 12, 0)) == [80, 80, 57, 80]   # on a point
    assert levels_now(s, datetime(2026, 8, 30, 12, 10)) == [65, 65, 46, 65]  # halfway
    assert levels_now(s, datetime(2026, 8, 30, 12, 20)) == [50, 50, 36, 50]  # next point
    assert levels_now(s, datetime(2026, 8, 30, 3, 0)) == [0, 0, 0, 0]        # overnight
    # 05:30 [0,0,0,0] -> 05:45 [0,0,5,0]: a third of the way up the blue ramp.
    assert levels_now(s, datetime(2026, 8, 30, 5, 35)) == [0, 0, 2, 0]
    assert levels_now({**s, "mode": "manual"}, datetime(2026, 8, 30, 12, 10)) is None
    print("schedule ok")


def test_simple():
    pts = simple_schedule((7, 0), (21, 0), [80, 80, 57, 80], 60)
    assert pts == [(7, 0, [0, 0, 0, 0]), (8, 0, [80, 80, 57, 80]),
                   (20, 0, [80, 80, 57, 80]), (21, 0, [0, 0, 0, 0])]

    # round-trips through the wire format and yields the transitions the sensors need
    status = parse_status(status_map(b"\xd2" + encode_pro(pts)[1:]))
    status.update(mode="pro", power=True)
    assert transitions(status) == ([7 * 60], [21 * 60])

    for bad, why in (
        (((21, 0), (7, 0), [80] * 4, 30), "wraps past midnight"),
        (((7, 0), (8, 0), [80] * 4, 60), "two 60 min ramps do not fit in 60 min"),
    ):
        try:
            simple_schedule(*bad)
            raise AssertionError(f"accepted a schedule that {why}")
        except ValueError:
            pass

    # Every point of the light's own factory schedule is reachable from a colour, so
    # the picker is not a lossy shortcut for the mixes people actually use.
    assert mix_to_levels([255, 255, 182], 100, 80) == [80, 80, 57, 80]
    assert mix_to_levels([255, 0, 255], 0, 20) == [20, 0, 20, 0]
    assert mix_to_levels([0, 0, 255], 0, 71) == [0, 0, 71, 0]
    assert mix_to_levels([255, 255, 182], 100, 100) == [100, 100, 71, 100]
    assert mix_to_levels([0, 0, 0], 0, 0) == [0, 0, 0, 0]

    # What the light reports must survive a trip out through the colour picker and
    # back unchanged, or every hue nudge in the UI would dim the tank a little more.
    for levels in ([80, 80, 57, 80], [100, 100, 71, 100], [20, 0, 20, 0], [0, 0, 5, 0],
                   [100, 0, 0, 0], [7, 7, 7, 7]):
        rgbw = levels_to_rgbw(levels)
        brightness = round(max(levels) * 255 / 100)
        assert rgbw_to_levels(rgbw, brightness) == levels, (levels, rgbw, brightness)
    assert levels_to_rgbw([0, 0, 0, 0]) == (0, 0, 0, 0)
    # A colour whose achromatic part Home Assistant has already moved into W must
    # still reach full output -- this is what was capping the RGB side.
    assert max(rgbw_to_levels((73, 73, 0, 182), 255)) == 100
    assert rgbw_to_levels((255, 0, 0, 0), 255) == [100, 0, 0, 0]

    assert parse_levels("80, 80, 57, 80") == [80, 80, 57, 80]
    assert parse_levels([80, 80, 57, 80]) == [80, 80, 57, 80]
    for bad in ("80, 80, 57", "80, 80, 57, 101", [1, 2, 3, 4, 5, 6]):
        try:
            parse_levels(bad)
            raise AssertionError(f"accepted {bad!r}")
        except ValueError:
            pass

    print("simple ok")


if __name__ == "__main__":
    test_decode()
    test_encode()
    test_schedule()
    test_simple()
