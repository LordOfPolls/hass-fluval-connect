"""Fluval Connect-generation BLE protocol: frame codec and schedule maths.

Service FFF0: write commands to FFF2 with a 0xD1 header, read status from FFF1
notifications with a 0xD2 header. No pairing, no auth, one central at a time.
`D0 FF` asks for a full state dump. The clock is the one exception to the CBOR
framing -- see encode_clock.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import cbor2

SERVICE = "0000fff0-0000-1000-8000-00805f9b34fb"
CHAR_NOTIFY = "0000fff1-0000-1000-8000-00805f9b34fb"
CHAR_WRITE = "0000fff2-0000-1000-8000-00805f9b34fb"
NAME_PREFIX = ("Roma", "Shaker", "PlantPro")

READ_ALL = b"\xd0\xff"
HDR_CMD = 0xD1
HDR_STATUS = 0xD2

MODES = ("manual", "auto", "pro")
# Roma/Shaker 2.0 (product 564) is a 4-channel RGBW light; the Plant Pro family has a
# 5th channel on key 7
CH_NAMES = {4: ("red", "green", "blue", "white")}
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

K_VERSION, K_MODE, K_POWER, K_CH1 = 0, 1, 2, 3
K_SUNRISE, K_SUNSET, K_SLEEP, K_DAY, K_NIGHT, K_PRO = 8, 9, 10, 11, 12, 13
K_WEATHER, K_DYNAMIC, K_PREVIEW, K_FIND = 14, 15, 51, 52

KEYS = {"mode": K_MODE, "power": K_POWER, "weather": K_WEATHER,
        "sunrise": K_SUNRISE, "sunset": K_SUNSET, "sleep": K_SLEEP,
        "day": K_DAY, "night": K_NIGHT, "pro": K_PRO,
        "dynamic": K_DYNAMIC, "preview": K_PREVIEW}

WEATHER = {0: "off", 1: "storm", 2: "passing_storm", 3: "cloudy", 4: "moonlight"}


def encode(**kw) -> bytes:
    """CBOR command frame, keyword names mapped to protocol keys."""
    return bytes([HDR_CMD]) + cbor2.dumps({KEYS[k]: v for k, v in kw.items()})


def encode_clock(when: datetime | None = None) -> bytes:
    """Clock-sync frame. Not CBOR: raw 0xCD + yy MM dd dow HH mm ss.

    Mirrors LightKxtKt.createLightClockValue -- "yy:MM:dd:HH:mm:ss" plus
    TimeUtil.getWeeks(), which is the ISO weekday (Mon=1 .. Sun=7).
    """
    t = when or datetime.now()
    return bytes([0xCD, t.year % 100, t.month, t.day,
                  t.isoweekday(), t.hour, t.minute, t.second])


def encode_channels(levels) -> bytes:
    """Set channel brightness 0-100. Only visible in manual mode."""
    levels = list(levels)
    if len(levels) not in (4, 5) or not all(0 <= v <= 100 for v in levels):
        raise ValueError(f"need 4 or 5 levels in 0..100, got {levels}")
    body = {K_CH1 + i: int(v) for i, v in enumerate(levels)}
    # Key 14 is the weather effect
    return bytes([HDR_CMD]) + cbor2.dumps({**body, K_WEATHER: 0})


def encode_find() -> bytes:
    """Make the light flash to identify itself."""
    return bytes([HDR_CMD]) + cbor2.dumps({K_FIND: "find"})


def encode_pro(points, channels: int = 4) -> bytes:
    """Build a Pro schedule frame from [(hour, minute, [levels...]), ...].

    Wire form is a single byte-string: count, then one 2+channels byte record per
    point. The light interpolates between consecutive points, so a point is a
    breakpoint in a ramp rather than a discrete scene.
    """
    pts = sorted(points, key=lambda p: (p[0], p[1]))
    if not 4 <= len(pts) <= 12:
        raise ValueError(f"the light stores 4 to 12 schedule points, got {len(pts)}")
    blob = bytearray([len(pts)])
    for hour, minute, levels in pts:
        levels = list(levels)
        if not (0 <= hour < 24 and 0 <= minute < 60):
            raise ValueError(f"bad time {hour}:{minute}")
        if len(levels) != channels or not all(0 <= v <= 100 for v in levels):
            raise ValueError(f"need {channels} levels in 0..100, got {levels}")
        blob += bytes([hour, minute, *(int(v) for v in levels)])
    return encode(pro=bytes(blob))


def parse_dynamic(e: bytes) -> dict:
    """One 6-byte weather-effect slot: flags, start h:m, end h:m, effect id."""
    return {"enabled": bool(e[0] & 0x80),
            "days": [d for i, d in enumerate(DAYS) if e[0] >> i & 1],
            "from": f"{e[1]:02d}:{e[2]:02d}", "to": f"{e[3]:02d}:{e[4]:02d}",
            "effect": WEATHER.get(e[5], e[5])}


def status_map(frame: bytes) -> dict:
    """Raw key -> value map from a 0xD2 frame.
    """
    if not frame or frame[0] != HDR_STATUS:
        raise ValueError(f"not a status frame: {frame[:4].hex()}")
    return cbor2.loads(frame[1:])


def parse_status(frame) -> dict:
    """Decode a 0xD2 status frame, or an already-merged key map, into a friendly dict."""
    m = frame if isinstance(frame, dict) else status_map(frame)
    n = 5 if K_CH1 + 4 in m else 4          # Plant Pro 4.0 reports a 5th channel on key 7
    levels = [m.get(K_CH1 + i) for i in range(n)]
    out = {"firmware": m.get(K_VERSION),
           "mode": MODES[m[K_MODE]] if m.get(K_MODE) in (0, 1, 2) else m.get(K_MODE),
           "power": m.get(K_POWER),
           "channel_count": n,
           "channels": dict(zip(CH_NAMES[n], levels)) if n in CH_NAMES else levels,
           "levels": levels,
           "weather": WEATHER.get(m.get(K_WEATHER), m.get(K_WEATHER))}
    for key, name in ((K_SUNRISE, "sunrise"), (K_SUNSET, "sunset")):
        if key in m:
            h, mi, ramp = m[key]
            out[name] = {"at": f"{h:02d}:{mi:02d}", "ramp_min": ramp}
    if K_SLEEP in m:
        h, mi = m[K_SLEEP]
        out["sleep"] = None if h == 0xFF else f"{h:02d}:{mi:02d}"
    for key, name in ((K_DAY, "day_levels"), (K_NIGHT, "night_levels")):
        if key in m:
            out[name] = list(m[key])
    if K_PRO in m:
        blob, step = m[K_PRO], 2 + n
        out["pro_schedule"] = [
            {"at": f"{blob[i]:02d}:{blob[i + 1]:02d}", "levels": list(blob[i + 2:i + step])}
            for i in range(1, 1 + blob[0] * step, step)
        ]
    if K_DYNAMIC in m:
        out["dynamic_effects"] = [parse_dynamic(m[K_DYNAMIC][i:i + 6])
                                  for i in range(0, len(m[K_DYNAMIC]), 6)]
    out["raw"] = m
    return out


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def transitions(status: dict) -> tuple[list[int], list[int]]:
    """Minutes-of-day at which the light next starts lighting up / reaches zero.
    """
    mode = status.get("mode")
    if mode == "pro":
        pts = status.get("pro_schedule") or []
        if len(pts) < 2:
            return [], []
        dark = [not any(p["levels"]) for p in pts]
        if all(dark) or not any(dark):
            return [], []
        on = [_minutes(p["at"]) for i, p in enumerate(pts)
              if dark[i] and not dark[(i + 1) % len(pts)]]
        off = [_minutes(p["at"]) for i, p in enumerate(pts)
               if dark[i] and not dark[i - 1]]
        return sorted(on), sorted(off)
    if mode == "auto":
        on = [_minutes(status["sunrise"]["at"])] if status.get("sunrise") else []
        if status.get("sleep"):
            return on, [_minutes(status["sleep"])]
        # only reaches zero if those levels are zero -which happens at the end of the ramp.
        if not any(status.get("night_levels") or [1]) and status.get("sunset"):
            return on, [_minutes(status["sunset"]["at"])]
        return on, []
    return [], []


def next_transitions(status: dict, now: datetime) -> tuple[datetime | None, datetime | None]:
    """Absolute datetimes of the next on and next off, or None if not scheduled.
    """
    on, off = transitions(status)
    return _next(on, now), _next(off, now)


def _next(minutes: list[int], now: datetime) -> datetime | None:
    """First of these wall-clock times strictly after `now`, today or tomorrow.
    """
    for day in (0, 1):
        date = (now + timedelta(days=day)).date()
        for m in minutes:
            candidate = datetime.combine(date, time(m // 60, m % 60), tzinfo=now.tzinfo)
            if candidate > now:
                return candidate
    return None


def parse_levels(value, channels: int | None = None) -> list[int]:
    """Channel levels from a list, or from "80, 80, 57, 80" as typed into a text box."""
    if isinstance(value, str):
        value = value.replace(",", " ").split()
    levels = [int(v) for v in value]
    if channels is not None and len(levels) != channels:
        raise ValueError(f"light has {channels} channels, got {len(levels)} levels")
    if len(levels) not in (4, 5) or not all(0 <= v <= 100 for v in levels):
        raise ValueError(f"need 4 or 5 levels in 0..100, got {levels}")
    return levels


def simple_schedule(on_at, off_at, levels, ramp_min: int = 30) -> list[tuple]:
    """Four breakpoints -- dark, ramp up, hold, ramp back down -- from (hour, minute).
    """
    start = on_at[0] * 60 + on_at[1]
    end = off_at[0] * 60 + off_at[1]
    if end <= start:
        raise ValueError("off time must be later the same day than on time")
    if end - start < 2 * ramp_min:
        raise ValueError(
            f"{end - start} minutes of light is too short for two {ramp_min} minute ramps")
    dark = [0] * len(levels)
    return [(m // 60, m % 60, lv) for m, lv in (
        (start, dark), (start + ramp_min, levels),
        (end - ramp_min, levels), (end, dark))]


def mix_to_levels(color, white: int = 0, brightness: int = 100) -> list[int]:
    """Colour-picker style mix -> channel levels.
    """
    scale = brightness / 100
    r, g, b = color
    return [round(r / 255 * scale * 100), round(g / 255 * scale * 100),
            round(b / 255 * scale * 100), round(white * scale)]


def levels_to_rgbw(levels) -> tuple[int, ...] | None:
    """Channel levels -> an RGBW colour, normalised against the brightest channel."""
    if len(levels) != 4 or any(v is None for v in levels):
        return None
    top = max(levels)
    if not top:
        return (0, 0, 0, 0)
    return tuple(round(v * 255 / top) for v in levels)


def rgbw_to_levels(rgbw, brightness: int) -> list[int]:
    """An RGBW colour + brightness -> channel levels.
    """
    top = max(rgbw) or 255
    return [round(c / top * brightness / 255 * 100) for c in rgbw]


def levels_now(status: dict, now: datetime) -> list[int] | None:
    """What a running Pro schedule is driving right now, or None if not applicable. Estimated.
    """
    if status.get("mode") != "pro":
        return None
    pts = status.get("pro_schedule") or []
    if len(pts) < 2:
        return None
    day = 24 * 60
    minutes = now.hour * 60 + now.minute + now.second / 60
    times = [_minutes(p["at"]) for p in pts]
    for i, start in enumerate(times):
        nxt = (i + 1) % len(pts)
        # Segments wrap past the last point of the day into the first of the next.
        span = (times[nxt] - start) % day or day
        offset = (minutes - start) % day
        if offset < span:
            here, there = pts[i]["levels"], pts[nxt]["levels"]
            return [round(a + (b - a) * offset / span) for a, b in zip(here, there)]
    return None
