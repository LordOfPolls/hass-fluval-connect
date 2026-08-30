from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import FluvalEntity
from .protocol import MODES, encode


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([FluvalModeSelect(hass.data[DOMAIN][entry.entry_id])])


class FluvalModeSelect(FluvalEntity, SelectEntity):
    _attr_translation_key = "mode"
    _attr_options = list(MODES)

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "mode")

    @property
    def current_option(self) -> str | None:
        mode = self.status.get("mode")
        return mode if mode in MODES else None

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.send(encode(mode=MODES.index(option)))
