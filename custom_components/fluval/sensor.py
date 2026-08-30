from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass, SensorEntity, SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .entity import FluvalEntity
from .protocol import next_transitions


@dataclass(frozen=True, kw_only=True)
class FluvalSensorDescription(SensorEntityDescription):
    value: Callable[[dict, datetime], datetime | str | None]


SENSORS: tuple[FluvalSensorDescription, ...] = (
    FluvalSensorDescription(
        key="next_on",
        translation_key="next_on",
        device_class=SensorDeviceClass.TIMESTAMP,
        value=lambda s, now: next_transitions(s, now)[0],
    ),
    FluvalSensorDescription(
        key="next_off",
        translation_key="next_off",
        device_class=SensorDeviceClass.TIMESTAMP,
        value=lambda s, now: next_transitions(s, now)[1],
    ),
    FluvalSensorDescription(
        key="firmware",
        translation_key="firmware",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=lambda s, now: s.get("firmware"),
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(FluvalSensor(coordinator, d) for d in SENSORS)


class FluvalSensor(FluvalEntity, SensorEntity):
    entity_description: FluvalSensorDescription

    def __init__(self, coordinator, description: FluvalSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self):
        # dt_util.now() is local time, which is what the light's own clock is set to.
        return self.entity_description.value(self.status, dt_util.now())
