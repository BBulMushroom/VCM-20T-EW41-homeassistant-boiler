"""Room switches and all-room control using the user's specified packets."""

from homeassistant.components.switch import SwitchEntity

from .entity import EW41Entity
from .protocol import ROOM_ORDER

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    async_add_entities([EW41RoomSwitch(coordinator, room) for room in ROOM_ORDER]
                       + [EW41AllSwitch(coordinator)]
                       + [EW41ReservationSwitch(coordinator, room) for room in ROOM_ORDER])


class EW41RoomSwitch(EW41Entity, SwitchEntity):
    _attr_icon = "mdi:radiator"

    def __init__(self, coordinator, room):
        super().__init__(coordinator, f"{room.name.lower()}_heating", f"{room.label} 난방")
        self.room = room

    @property
    def is_on(self):
        status = self.coordinator.data
        return status.is_on(self.room) if status is not None else None

    async def async_turn_on(self, **kwargs):
        await self.coordinator.async_control(self.coordinator.client.set_room, self.room, True)

    async def async_turn_off(self, **kwargs):
        await self.coordinator.async_control(self.coordinator.client.set_room, self.room, False)


class EW41AllSwitch(EW41Entity, SwitchEntity):
    _attr_icon = "mdi:radiator"

    def __init__(self, coordinator):
        super().__init__(coordinator, "all_heating", "전체방 난방")

    @property
    def is_on(self):
        status = self.coordinator.data
        return bool(status.room_mask & 0x0F) if status is not None else None

    @property
    def extra_state_attributes(self):
        attrs = super().extra_state_attributes
        if self.coordinator.data is not None:
            attrs["all_rooms_on"] = self.coordinator.data.room_mask == 0x0F
        return attrs

    async def async_turn_on(self, **kwargs):
        await self.coordinator.async_control(self.coordinator.client.set_all_rooms, True)

    async def async_turn_off(self, **kwargs):
        await self.coordinator.async_control(self.coordinator.client.set_all_rooms, False)


class EW41ReservationSwitch(EW41Entity, SwitchEntity):
    """Enable the thermostat's saved native timer, without emulating it in HA."""
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator, room):
        super().__init__(coordinator, f"{room.name.lower()}_native_reservation", f"{room.label} 본체 예약난방")
        self.room = room

    @property
    def is_on(self):
        status = self.coordinator.data
        return status.is_reserved(self.room) if status is not None else None

    @property
    def extra_state_attributes(self):
        attrs = super().extra_state_attributes
        attrs["timing_configuration"] = "thermostat"
        attrs["off_behavior"] = "normal_heating"
        return attrs

    async def async_turn_on(self, **kwargs):
        await self.coordinator.async_control(self.coordinator.client.set_reservation, self.room, True)

    async def async_turn_off(self, **kwargs):
        await self.coordinator.async_control(self.coordinator.client.set_reservation, self.room, False)
