"""Four climate entities backed by verified per-room command addresses."""

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    PRESET_AWAY, PRESET_NONE, ClimateEntityFeature, HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.exceptions import HomeAssistantError

from .const import CONF_MAX_TEMP, CONF_MIN_TEMP, DEFAULT_MAX_TEMP, DEFAULT_MIN_TEMP
from .entity import EW41Entity
from .protocol import ROOM_ORDER, Room, encode_temperature

PARALLEL_UPDATES = 0  # The shared coordinator serializes every platform.


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([EW41RoomClimate(entry.runtime_data, room) for room in ROOM_ORDER])


class EW41RoomClimate(EW41Entity, ClimateEntity):
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 0.5
    _attr_precision = 0.5
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_preset_modes = [PRESET_NONE, PRESET_AWAY]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF | ClimateEntityFeature.PRESET_MODE
    )

    def __init__(self, coordinator, room):
        self.room = room
        super().__init__(coordinator, f"{room.name.lower()}_climate", room.label)
        self._attr_min_temp = coordinator.settings.get(CONF_MIN_TEMP, DEFAULT_MIN_TEMP)
        self._attr_max_temp = coordinator.settings.get(CONF_MAX_TEMP, DEFAULT_MAX_TEMP)

    @property
    def current_temperature(self):
        status = self.coordinator.data
        return status.current_temperature(self.room) if status is not None else None

    @property
    def target_temperature(self):
        status = self.coordinator.data
        return status.target_temperature(self.room) if status is not None else None

    @property
    def hvac_mode(self):
        status = self.coordinator.data
        if status is None:
            return None
        return HVACMode.HEAT if status.is_on(self.room) else HVACMode.OFF

    # hvac_action is deliberately absent: the protocol does not confirm flame/pump activity.

    @property
    def preset_mode(self):
        status = self.coordinator.data
        if status is None:
            return None
        return PRESET_AWAY if status.is_away(self.room) else PRESET_NONE

    async def async_set_preset_mode(self, preset_mode):
        if preset_mode == PRESET_AWAY:
            await self.coordinator.async_control(self.coordinator.client.set_away, self.room)
        elif preset_mode == PRESET_NONE:
            await self.async_set_hvac_mode(HVACMode.HEAT)
        else:
            raise HomeAssistantError("일반 또는 외출 모드만 지원합니다.")

    async def async_set_temperature(self, **kwargs):
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            raise HomeAssistantError("목표 온도를 지정하세요.")
        try:
            encode_temperature(temperature)
        except ValueError as exc:
            raise HomeAssistantError(str(exc)) from exc
        if not self._attr_min_temp <= temperature <= self._attr_max_temp:
            raise HomeAssistantError(f"설정 온도는 {self._attr_min_temp:g}~{self._attr_max_temp:g}℃ 범위입니다.")
        hvac_mode = kwargs.get("hvac_mode")
        if hvac_mode is not None and hvac_mode not in self._attr_hvac_modes:
            raise HomeAssistantError("난방 또는 OFF 모드만 지원합니다.")
        await self.coordinator.async_control(self.coordinator.client.set_temperature, self.room, temperature)
        if hvac_mode is not None:
            await self.async_set_hvac_mode(hvac_mode)

    async def async_set_hvac_mode(self, hvac_mode):
        if hvac_mode not in self._attr_hvac_modes:
            raise HomeAssistantError("난방 또는 OFF 모드만 지원합니다.")
        await self.coordinator.async_control(self.coordinator.client.set_room, self.room, hvac_mode == HVACMode.HEAT)

    async def async_turn_on(self):
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self):
        await self.async_set_hvac_mode(HVACMode.OFF)


class EW41LivingClimate(EW41RoomClimate):
    """Compatibility name for existing adapter callers."""
    def __init__(self, coordinator):
        super().__init__(coordinator, Room.LIVING)
