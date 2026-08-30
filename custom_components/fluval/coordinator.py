from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .protocol import (
    CHAR_NOTIFY, CHAR_WRITE, MODES, READ_ALL, encode, encode_channels, encode_clock,
    parse_status, status_map,
)

_LOGGER = logging.getLogger(__name__)

READ_TIMEOUT = 15


class FluvalCoordinator(DataUpdateCoordinator[dict]):
    """Owns the BLE link and the last known state of one light."""

    def __init__(self, hass: HomeAssistant, address: str, name: str,
                 keep_connected: bool, poll_interval: int) -> None:
        super().__init__(hass, _LOGGER, name=name,
                         update_interval=timedelta(seconds=int(poll_interval)))
        self.address = address
        self.device_name = name
        self.keep_connected = keep_connected
        self._client: BleakClientWithServiceCache | None = None
        self._lock = asyncio.Lock()
        self._pending: asyncio.Future[dict] | None = None
        self._raw: dict = {}
        # Mode to put back when the light is switched on again
        self.resume_mode: str | None = None
        self.last_levels: list[int] | None = None

    async def _connect(self) -> BleakClientWithServiceCache:
        if self._client is not None and self._client.is_connected:
            return self._client
        device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True)
        if device is None:
            raise UpdateFailed(
                f"{self.address} not in range of any Bluetooth adapter "
                "(or the phone app is holding the connection)")
        client = await establish_connection(
            BleakClientWithServiceCache, device, self.device_name,
            self._on_disconnect, use_services_cache=True)
        await client.start_notify(CHAR_NOTIFY, self._on_notify)
        # The app resyncs the clock on every connect rather than trusting the RTC,
        # and there is no way to read it back to check. Do the same.
        await self._write(client, encode_clock(dt_util.now()))
        self._client = client
        return client

    def _on_disconnect(self, _client) -> None:
        self._client = None

    def _on_notify(self, _char, data: bytearray) -> None:
        try:
            self._raw.update(status_map(bytes(data)))
            state = parse_status(dict(self._raw))
        except Exception:  # noqa: BLE001 - a bad frame must not kill the notify loop
            _LOGGER.debug("undecodable frame from %s: %s", self.address, bytes(data).hex())
            return
        if any(state.get("levels") or []):
            self.last_levels = state["levels"]
        if self._pending is not None and not self._pending.done():
            self._pending.set_result(state)
        else:
            self.async_set_updated_data(state)

    async def _write(self, client, frame: bytes) -> None:
        """Write one frame, asking for a response only when the frame needs it."""
        _LOGGER.debug("-> %s", frame.hex())
        await client.write_gatt_char(
            CHAR_WRITE, frame, response=len(frame) > client.mtu_size - 3)

    async def _read(self, client) -> dict:
        self._pending = self.hass.loop.create_future()
        try:
            await self._write(client, READ_ALL)
            return await asyncio.wait_for(self._pending, READ_TIMEOUT)
        finally:
            self._pending = None

    async def _async_update_data(self) -> dict:
        async with self._lock:
            try:
                client = await self._connect()
                state = await self._read(client)
            except UpdateFailed:
                raise
            except Exception as err:  # noqa: BLE001 - surfaced to HA as unavailable
                await self._drop()
                raise UpdateFailed(f"{self.address}: {err}") from err
            if not self.keep_connected:
                await self._drop()
            return state

    async def send(self, frame: bytes) -> None:
        """Write one command frame, then refresh state from the light."""
        async with self._lock:
            try:
                client = await self._connect()
                await self._write(client, frame)
                state = await self._read(client)
            except Exception as err:  # noqa: BLE001
                await self._drop()
                raise UpdateFailed(f"{self.address}: {err}") from err
            if not self.keep_connected:
                await self._drop()
        _LOGGER.debug("<- mode=%s power=%s levels=%s weather=%s",
                      state.get("mode"), state.get("power"),
                      state.get("levels"), state.get("weather"))
        self.async_set_updated_data(state)

    async def set_levels(self, levels) -> None:
        """Write a channel mix, and make sure it will actually reach the LEDs.

        Channel levels are only driven in Manual, so a mix set while a schedule is
        running would otherwise do nothing at all.
        """
        state = self.data or {}
        if state.get("mode") != "manual":
            await self.send(encode(mode=MODES.index("manual")))
        self.resume_mode = None
        if not (self.data or {}).get("power"):
            await self.send(encode(power=True))
        await self.send(encode_channels(levels))

    async def _drop(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001 - already gone is the common case
                pass

    async def async_shutdown(self) -> None:
        await super().async_shutdown()
        await self._drop()

    @property
    def device_info(self) -> dict:
        return {
            "identifiers": {(DOMAIN, self.address)},
            "connections": {("bluetooth", self.address)},
            "name": self.device_name,
            "manufacturer": "Fluval",
            "model": self.device_name.rsplit("_", 1)[0],
            "sw_version": str((self.data or {}).get("firmware", "")),
        }
