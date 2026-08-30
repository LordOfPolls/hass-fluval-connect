from __future__ import annotations

import voluptuous as vol
from homeassistant.components.light import (
    ATTR_BRIGHTNESS, ATTR_EFFECT, ATTR_RGBW_COLOR, ColorMode, LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .entity import FluvalEntity
from .protocol import (
    CH_NAMES, MODES, WEATHER, encode, encode_channels, encode_pro, levels_now,
    levels_to_rgbw, mix_to_levels, parse_levels, rgbw_to_levels, simple_schedule,
)

SERVICE_SET_PRO_SCHEDULE = "set_pro_schedule"
SERVICE_SET_SIMPLE_SCHEDULE = "set_simple_schedule"
SERVICE_PREVIEW = "preview"

LEVELS = vol.All(parse_levels, msg="levels must be 4 or 5 numbers from 0 to 100")
PERCENT = vol.All(vol.Coerce(int), vol.Range(min=0, max=100))
COLOR = vol.All(cv.ensure_list, [vol.All(vol.Coerce(int), vol.Range(min=0, max=255))],
                vol.Length(min=3, max=3))

MIX = {
    vol.Optional("color"): COLOR,
    vol.Optional("white", default=0): PERCENT,
    vol.Optional("brightness", default=100): PERCENT,
    vol.Optional("levels"): LEVELS,
}


def _mix(point: dict) -> list[int]:
    if point.get("levels"):
        return point["levels"]
    if not point.get("color"):
        raise vol.Invalid("give either a colour or explicit levels")
    return mix_to_levels(point["color"], point["white"], point["brightness"])


PRO_POINT_SCHEMA = vol.Schema({vol.Required("time"): cv.time, **MIX})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_SET_PRO_SCHEDULE,
        {vol.Required("points"): vol.All(cv.ensure_list, [PRO_POINT_SCHEMA],
                                         vol.Length(min=4, max=12))},
        "async_set_pro_schedule",
    )
    platform.async_register_entity_service(
        SERVICE_SET_SIMPLE_SCHEDULE,
        {
            vol.Required("on_at"): cv.time,
            vol.Required("off_at"): cv.time,
            vol.Optional("ramp_minutes", default=30): vol.All(
                vol.Coerce(int), vol.Range(min=0, max=720)),
            **MIX,
        },
        "async_set_simple_schedule",
    )
    platform.async_register_entity_service(
        SERVICE_PREVIEW,
        {vol.Required("seconds"): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535))},
        "async_preview",
    )
    async_add_entities([FluvalLight(hass.data[DOMAIN][entry.entry_id])])


class FluvalLight(FluvalEntity, LightEntity):
    """Master on/off plus the manual channel mix, exposed as an RGBW light."""

    _attr_name = None
    _attr_color_mode = ColorMode.RGBW
    _attr_supported_color_modes = {ColorMode.RGBW}
    _attr_supported_features = LightEntityFeature.EFFECT
    _attr_effect_list = list(WEATHER.values())

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "light")

    SCHEDULED = ("auto", "pro")

    @property
    def is_on(self) -> bool | None:
        # Only Manual honours the power flag: in Auto and Pro the schedule drives the
        # LEDs and the light happily reports power False while still lit.
        if self.status.get("mode") in self.SCHEDULED:
            return True
        # In Manual the output *is* the channel mix, so an all-zero light is off no
        # matter what the power flag says.
        return bool(self.status.get("power")) and any(self.status.get("levels") or [])

    @property
    def live_levels(self) -> list[int]:
        """What the LEDs are putting out now, schedule included."""
        computed = levels_now(self.status, dt_util.now())
        return computed if computed is not None else (self.status.get("levels") or [])

    @property
    def brightness(self) -> int | None:
        """Brightest channel, since the light has no separate master dimmer."""
        levels = [v for v in self.live_levels if v is not None]
        return round(max(levels) * 255 / 100) if levels else None

    @property
    def rgbw_color(self) -> tuple[int, ...] | None:
        return levels_to_rgbw(self.live_levels)

    @property
    def effect(self) -> str | None:
        return self.status.get("weather")

    @property
    def extra_state_attributes(self) -> dict:
        s = self.status
        return {
            "mode": s.get("mode"),
            "channels": dict(zip(CH_NAMES[4], self.live_levels))
            if len(self.live_levels) == 4 else s.get("channels"),
            "manual_channels": s.get("channels"),
            "pro_schedule": s.get("pro_schedule"),
            "sunrise": s.get("sunrise"),
            "sunset": s.get("sunset"),
            "sleep": s.get("sleep"),
        }

    async def async_turn_on(self, **kwargs) -> None:
        if ATTR_EFFECT in kwargs:
            value = next(k for k, v in WEATHER.items() if v == kwargs[ATTR_EFFECT])
            # An effect modulates live output, so it does nothing on a dark light.
            if not self.is_on:
                await self._power_on()
            await self.coordinator.send(encode(weather=value))
            return

        if ATTR_BRIGHTNESS in kwargs or ATTR_RGBW_COLOR in kwargs:
            await self._set_mix(kwargs)
            return

        if not self.is_on:
            await self._power_on()

    async def _power_on(self) -> None:
        """Back on, and back into whatever schedule was running when it went off."""
        resume = self.coordinator.resume_mode
        self.coordinator.resume_mode = None
        if resume:
            await self.coordinator.send(encode(mode=MODES.index(resume), power=True))
            return
        await self.coordinator.send(encode(power=True))
        # Manual drives the LEDs straight from the channel levels, and they are zero
        # after a turn_off or a switch out of Auto/Pro -- powering on alone is dark.
        if not any(self.status.get("levels") or []):
            await self.coordinator.send(encode_channels(
                self.coordinator.last_levels
                or [100] * self.status.get("channel_count", 4)))

    async def _set_mix(self, kwargs) -> None:
        rgbw = kwargs.get(ATTR_RGBW_COLOR) or self.rgbw_color or (255, 255, 255, 255)
        brightness = kwargs.get(ATTR_BRIGHTNESS, self.brightness or 255)
        levels = rgbw_to_levels(rgbw, brightness)
        await self.coordinator.set_levels(levels)

    async def async_turn_off(self, **kwargs) -> None:
        # The power flag alone leaves a scheduled light lit, so going dark means
        # dropping into Manual as well. Turning it back on restores the mode.
        mode = self.status.get("mode")
        if mode in self.SCHEDULED:
            self.coordinator.resume_mode = mode
            await self.coordinator.send(
                encode(mode=MODES.index("manual"), power=False))
            return
        await self.coordinator.send(encode(power=False))

    async def async_set_pro_schedule(self, points: list[dict]) -> None:
        """Replace the Pro schedule outright -- the light has no per-point edit."""
        await self._write_pro(
            [(p["time"].hour, p["time"].minute, _mix(p)) for p in points])

    async def _write_pro(self, points) -> None:
        await self.coordinator.send(
            encode_pro(points, self.status.get("channel_count", 4)))

    async def async_set_simple_schedule(self, on_at, off_at, ramp_minutes: int,
                                       **mix) -> None:
        """Build the usual dark/ramp-up/hold/ramp-down day and write it as a Pro schedule."""
        await self._write_pro(simple_schedule(
            (on_at.hour, on_at.minute), (off_at.hour, off_at.minute),
            _mix(mix), ramp_minutes))

    async def async_preview(self, seconds: int) -> None:
        await self.coordinator.send(encode(preview=seconds))
