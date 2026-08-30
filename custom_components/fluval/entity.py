from __future__ import annotations

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import FluvalCoordinator


class FluvalEntity(CoordinatorEntity[FluvalCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: FluvalCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_{key}"
        self._attr_device_info = coordinator.device_info

    @property
    def status(self) -> dict:
        return self.coordinator.data or {}
