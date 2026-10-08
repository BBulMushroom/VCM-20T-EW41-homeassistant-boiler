"""A single shared client and serialized executor calls for all entities."""

import asyncio
from datetime import timedelta
import logging

from homeassistant.const import CONF_HOST, CONF_PORT, CONF_SCAN_INTERVAL, CONF_TIMEOUT
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_SCAN_INTERVAL, DEFAULT_TIMEOUT, DOMAIN
from .protocol import EW41Client, EW41Error

_LOGGER = logging.getLogger(__name__)


class EW41Coordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        self.settings = {**entry.data, **entry.options}
        self.client = EW41Client(
            host=self.settings[CONF_HOST],
            port=self.settings[CONF_PORT],
            timeout=self.settings.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
        )
        self.device_id = entry.unique_id or f"{self.client.host}:{self.client.port}"
        self._command_lock = asyncio.Lock()
        super().__init__(
            hass, _LOGGER, name=DOMAIN, config_entry=entry,
            update_interval=timedelta(seconds=self.settings.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
            always_update=False,
        )

    async def _async_update_data(self):
        async with self._command_lock:
            try:
                return await self.hass.async_add_executor_job(self.client.get_status)
            except EW41Error as exc:
                raise UpdateFailed(str(exc)) from exc

    async def async_control(self, method, *args):
        """Publish verified state, including partial failures; never set optimistically."""
        async with self._command_lock:
            try:
                result = await self.hass.async_add_executor_job(method, *args)
            except (EW41Error, ValueError) as exc:
                raise HomeAssistantError(str(exc)) from exc
            for warning in result.warnings:
                _LOGGER.warning("%s: %s", result.requested, warning)
            if result.status is not None:
                self.async_set_updated_data(result.status)
            else:
                self.async_set_update_error(UpdateFailed("최종 EW41 상태를 확인할 수 없습니다."))
            if not result.success:
                raise HomeAssistantError("; ".join(result.errors) or "명령 적용을 확인할 수 없습니다.")
            return result
