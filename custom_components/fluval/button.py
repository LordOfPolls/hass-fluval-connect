from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .entity import FluvalEntity
from .protocol import encode_clock, encode_find


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        FluvalButton(coordinator, ButtonEntityDescription(
            key="sync_clock", translation_key="sync_clock",
            entity_category=EntityCategory.CONFIG)),
        FluvalButton(coordinator, ButtonEntityDescription(
            key="identify", translation_key="identify",
            entity_category=EntityCategory.CONFIG)),
    ])


class FluvalButton(FluvalEntity, ButtonEntity):
    def __init__(self, coordinator, description: ButtonEntityDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        # The clock is already pushed on every connect; this is for when you have
        # changed the schedule and want to be sure the RTC agrees before it runs.
        if self.entity_description.key == "sync_clock":
            await self.coordinator.send(encode_clock(dt_util.now()))
        else:
            await self.coordinator.send(encode_find())
