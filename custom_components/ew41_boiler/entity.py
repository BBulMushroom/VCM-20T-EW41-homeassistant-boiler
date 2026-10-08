"""Device identity and diagnostic state attributes shared by the entities."""

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


class EW41Entity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, key, name):
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_id}_{key}"
        self._attr_name = name
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.device_id)},
            "name": "EW41 보일러",
            "model": "EW41 TCP boiler bridge",
            "sw_version": "0.1.2",
        }

    @property
    def extra_state_attributes(self):
        if self.coordinator.data is None:
            return {}
        status = self.coordinator.data
        return {"raw_06": f"{status.room_mask:02X}", "raw_09": f"{status.mode_value:02X}"}
