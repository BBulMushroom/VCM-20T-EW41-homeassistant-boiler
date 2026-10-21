"""Main-controller operating modes, decoded from verified status queries."""

from homeassistant.components.select import SelectEntity
from homeassistant.exceptions import HomeAssistantError

from .const import MODE_HEATING_HOTWATER, MODE_HOTWATER_ONLY
from .entity import EW41Entity
from .protocol import OperatingMode, Room

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([EW41ModeSelect(entry.runtime_data)])


class EW41ModeSelect(EW41Entity, SelectEntity):
    _attr_options = ["꺼짐", MODE_HOTWATER_ONLY, MODE_HEATING_HOTWATER]
    _attr_icon = "mdi:water-boiler"

    def __init__(self, coordinator):
        super().__init__(coordinator, "operating_mode", "운전모드")

    @property
    def current_option(self):
        status = self.coordinator.data
        if status is None or status.mode is None:
            return None
        # Hot-water-only clears the living heating bit too. Check it before
        # interpreting the room's three cleared bits as power OFF.
        if status.mode == OperatingMode.HOTWATER_ONLY:
            return MODE_HOTWATER_ONLY
        if status.is_off(Room.LIVING):
            return "꺼짐"
        # Away/native reservation keep the main controller in normal mode.
        # They describe room demand, not a third operating-mode byte value.
        return MODE_HEATING_HOTWATER

    async def async_select_option(self, option):
        if option == "꺼짐":
            await self.coordinator.async_control(self.coordinator.client.set_room, Room.LIVING, False)
            return
        if option == MODE_HEATING_HOTWATER:
            method = self.coordinator.client.set_heating_hotwater_mode
        elif option == MODE_HOTWATER_ONLY:
            method = self.coordinator.client.set_hotwater_only
        else:
            raise HomeAssistantError("지원하지 않는 운전모드입니다.")
        await self.coordinator.async_control(method)
