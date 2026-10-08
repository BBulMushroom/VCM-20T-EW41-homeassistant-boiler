"""Manual status refresh."""

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError

from .entity import EW41Entity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([EW41RefreshButton(entry.runtime_data)])


class EW41RefreshButton(EW41Entity, ButtonEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator):
        super().__init__(coordinator, "refresh", "상태 새로고침")

    async def async_press(self):
        await self.coordinator.async_refresh()
        if not self.coordinator.last_update_success:
            raise HomeAssistantError("EW41 상태 조회에 실패했습니다.")
