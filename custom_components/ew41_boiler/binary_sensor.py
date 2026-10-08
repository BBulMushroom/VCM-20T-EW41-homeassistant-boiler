"""Diagnostic native reservation flags."""
from homeassistant.components.binary_sensor import BinarySensorEntity
from .entity import EW41Entity
from .protocol import ROOM_ORDER

PARALLEL_UPDATES = 0

async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([EW41ReservationSensor(entry.runtime_data, room) for room in ROOM_ORDER])

class EW41ReservationSensor(EW41Entity, BinarySensorEntity):
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator, room):
        super().__init__(coordinator, f"{room.name.lower()}_reservation", f"{room.label} 본체 예약 상태")
        self.room = room

    @property
    def is_on(self):
        status = self.coordinator.data
        return status.is_reserved(self.room) if status is not None else None

    @property
    def extra_state_attributes(self):
        values = super().extra_state_attributes
        status = self.coordinator.data
        return {**values, "raw_08": f"{status.reservation_mask:02X}" if status is not None else None,
                "control": "read_only", "interval_hours": None, "run_minutes": None}
