from __future__ import annotations

import voluptuous as vol
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak, async_discovered_service_info
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector, NumberSelectorConfig, NumberSelectorMode,
)

from .const import (
    CONF_KEEP_CONNECTED, CONF_POLL_INTERVAL, DEFAULT_KEEP_CONNECTED, DOMAIN,
    MAX_POLL_INTERVAL, MIN_POLL_INTERVAL, default_poll_interval,
)
from .protocol import NAME_PREFIX


def _is_light(name: str | None) -> bool:
    return bool(name) and name.startswith(NAME_PREFIX)


class FluvalConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle discovery and manual setup."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered: dict[str, str] = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        if not _is_light(discovery_info.name):
            return self.async_abort(reason="not_supported")
        self._discovered = {discovery_info.address: discovery_info.name}
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input=None) -> ConfigFlowResult:
        address, name = next(iter(self._discovered.items()))
        if user_input is not None:
            return self.async_create_entry(
                title=name, data={CONF_ADDRESS: address, CONF_NAME: name})
        self._set_confirm_only()
        return self.async_show_form(
            step_id="confirm", description_placeholders={"name": name})

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=self._discovered[address],
                data={CONF_ADDRESS: address, CONF_NAME: self._discovered[address]})

        configured = self._async_current_ids()
        self._discovered = {
            info.address: info.name
            for info in async_discovered_service_info(self.hass, connectable=True)
            if _is_light(info.name) and info.address not in configured
        }
        if not self._discovered:
            return self.async_abort(reason="no_devices_found")
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): vol.In(self._discovered)}),
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return FluvalOptionsFlow()


INTERVAL = vol.All(
    NumberSelector(NumberSelectorConfig(
        min=MIN_POLL_INTERVAL, max=MAX_POLL_INTERVAL, step=5,
        unit_of_measurement="s", mode=NumberSelectorMode.BOX)),
    vol.Coerce(int),
)


class FluvalOptionsFlow(OptionsFlow):
    """Whether to hold the BLE link open, and how often to poll the light."""

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        options = self.config_entry.options
        keep = options.get(CONF_KEEP_CONNECTED, DEFAULT_KEEP_CONNECTED)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_KEEP_CONNECTED, default=keep): bool,
                vol.Required(
                    CONF_POLL_INTERVAL,
                    default=options.get(
                        CONF_POLL_INTERVAL, default_poll_interval(keep)),
                ): INTERVAL,
            }),
        )
