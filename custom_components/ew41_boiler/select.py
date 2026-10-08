"""Main-controller operating modes, decoded from verified status queries."""

from homeassistant.components.select import SelectEntity
from homeassistant.exceptions import HomeAssistantError

from .const import MODE_HEATING_HOTWATER, MODE_HOTWATER_ONLY
from .entity import EW41Entity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([EW41ModeSelect(entry.runtime_data)])


class EW41ModeSelect(EW41Entity, SelectEntity):
    _attr_options = [MODE_HEATING_HOTWATER, MODE_HOTWATER_ONLY]
    _attr_icon = "mdi:water-boiler"

    def __init__(self, coordinator):
        super().__init__(coordinator, "operating_mode", "운전모드")

    @property
    def current_option(self):
        status = self.coordinator.data
        return status.mode_label if status is not None and status.mode is not None else None

    async def async_select_option(self, option):
        if option == MODE_HEATING_HOTWATER:
            method = self.coordinator.client.set_heating_hotwater_mode
        elif option == MODE_HOTWATER_ONLY:
            method = self.coordinator.client.set_hotwater_only
        else:
            raise HomeAssistantError("지원하지 않는 운전모드입니다.")
        await self.coordinator.async_control(method)
