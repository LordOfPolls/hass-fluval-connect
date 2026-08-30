"""Fluval Connect aquarium light, over the Home Assistant host's own Bluetooth."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_NAME, Platform
from homeassistant.core import HomeAssistant

from .const import (
    CONF_KEEP_CONNECTED, CONF_POLL_INTERVAL, DEFAULT_KEEP_CONNECTED, DOMAIN,
    default_poll_interval,
)
from .coordinator import FluvalCoordinator

PLATFORMS = [Platform.LIGHT, Platform.SELECT, Platform.SENSOR, Platform.BUTTON]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    keep_connected = entry.options.get(CONF_KEEP_CONNECTED, DEFAULT_KEEP_CONNECTED)
    coordinator = FluvalCoordinator(
        hass,
        entry.data[CONF_ADDRESS],
        entry.data.get(CONF_NAME, entry.title),
        keep_connected,
        entry.options.get(CONF_POLL_INTERVAL, default_poll_interval(keep_connected)),
    )
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
    return unloaded


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
