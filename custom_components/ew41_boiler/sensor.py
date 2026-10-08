"""Raw diagnostics and a readable mode that can also represent unknown values."""

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory

from .entity import EW41Entity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([EW41DiagnosticSensor(entry.runtime_data, key, name) for key, name in (
        ("room_mask", "난방 RAW [06]"),
        ("mode_value", "운전모드 RAW [09]"),
        ("raw", "수신 패킷"),
        ("mode_label", "운전모드 상태"),
    )])


class EW41DiagnosticSensor(EW41Entity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, key, name):
        super().__init__(coordinator, f"diagnostic_{key}", name)
        self.key = key

    @property
    def native_value(self):
        status = self.coordinator.data
        if status is None:
            return None
        value = getattr(status, self.key)
        if self.key == "raw":
            return value.hex(" ").upper()
        if self.key in ("room_mask", "mode_value"):
            return f"{value:02X}"
        return value
