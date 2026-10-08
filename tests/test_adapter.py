"""Adapter tests with explicit HA API doubles, not an installed HA runtime."""

import asyncio
from enum import Enum, IntFlag
import importlib
import json
from pathlib import Path
import sys
import threading
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def module(name, **attributes):
    result = ModuleType(name)
    result.__dict__.update(attributes)
    sys.modules[name] = result
    return result


class HAError(Exception):
    pass


class UpdateFailed(Exception):
    pass


class Coordinator:
    def __init__(self, hass, logger, **kwargs):
        self.hass, self.data, self.last_update_success = hass, None, True
        self.update_interval = kwargs["update_interval"]

    async def async_config_entry_first_refresh(self):
        self.data = await self._async_update_data()

    async def async_refresh(self):
        try:
            self.async_set_updated_data(await self._async_update_data())
        except UpdateFailed as exc:
            self.async_set_update_error(exc)

    def async_set_updated_data(self, data):
        self.data, self.last_update_success = data, True

    def async_set_update_error(self, error):
        self.last_exception, self.last_update_success = error, False


class CoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator

    @property
    def available(self):
        return self.coordinator.last_update_success


class Flow:
    def __init_subclass__(cls, domain=None, **kwargs):
        super().__init_subclass__(**kwargs)

    async def async_set_unique_id(self, value):
        self.unique_id = value

    def _abort_if_unique_id_configured(self):
        pass

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}


class HVACMode(str, Enum):
    OFF = "off"
    HEAT = "heat"


class Features(IntFlag):
    TARGET_TEMPERATURE = 1
    TURN_ON = 128
    TURN_OFF = 256


class Platform(str, Enum):
    CLIMATE = "climate"
    SWITCH = "switch"
    SELECT = "select"
    SENSOR = "sensor"
    BUTTON = "button"
    BINARY_SENSOR = "binary_sensor"


module("homeassistant", config_entries=module("homeassistant.config_entries", ConfigFlow=Flow, OptionsFlow=Flow))
module("homeassistant.core", callback=lambda function: function)
module("homeassistant.const", Platform=Platform, CONF_HOST="host", CONF_PORT="port",
       CONF_TIMEOUT="timeout", CONF_SCAN_INTERVAL="scan_interval", ATTR_TEMPERATURE="temperature",
       UnitOfTemperature=SimpleNamespace(CELSIUS="°C"), EntityCategory=SimpleNamespace(DIAGNOSTIC="diagnostic"))
module("homeassistant.exceptions", HomeAssistantError=HAError)
module("homeassistant.helpers")
module("homeassistant.helpers.update_coordinator", DataUpdateCoordinator=Coordinator, CoordinatorEntity=CoordinatorEntity, UpdateFailed=UpdateFailed)
module("homeassistant.components")
module("homeassistant.components.climate", ClimateEntity=type("ClimateEntity", (), {}))
module("homeassistant.components.climate.const", HVACMode=HVACMode, ClimateEntityFeature=Features)
for platform, name in (("switch", "SwitchEntity"), ("select", "SelectEntity"), ("sensor", "SensorEntity"), ("button", "ButtonEntity"), ("binary_sensor", "BinarySensorEntity")):
    module(f"homeassistant.components.{platform}", **{name: type(name, (), {})})
# Schema construction is mocked. These tests do not claim voluptuous validation coverage.
module("voluptuous", Required=lambda key, **kwargs: key, Schema=lambda fields: fields,
       Coerce=lambda value: value, All=lambda *args: args, Range=lambda **kwargs: kwargs)

integration = importlib.import_module("custom_components.ew41_boiler")
protocol = importlib.import_module("custom_components.ew41_boiler.protocol")
from custom_components.ew41_boiler.coordinator import EW41Coordinator
from custom_components.ew41_boiler.climate import EW41LivingClimate
from custom_components.ew41_boiler.select import EW41ModeSelect
from custom_components.ew41_boiler.switch import EW41RoomSwitch, EW41AllSwitch
from custom_components.ew41_boiler.sensor import EW41DiagnosticSensor
from custom_components.ew41_boiler.button import EW41RefreshButton
from custom_components.ew41_boiler.config_flow import EW41ConfigFlow, EW41OptionsFlow, valid_temperature_range


def status(mask=15, mode=0, target=21):
    payload = list(bytes.fromhex("F7 36 0F 81 0D 00 0F 00 00 00 15 18 0A 98 0A 18 0A 18"))
    payload[6], payload[9], payload[10] = mask, mode, protocol.encode_temperature(target)
    return protocol.BoilerStatus.from_response(protocol.make_packet(payload))


class Entry:
    def __init__(self):
        self.data = {"host": "192.168.0.22", "port": 8899, "timeout": 2,
                     "scan_interval": 10, "min_temperature": 10, "max_temperature": 30}
        self.options, self.unique_id, self.entry_id = {}, "192.168.0.22:8899", "entry1"
        self.callbacks = []

    def async_on_unload(self, callback):
        self.callbacks.append(callback)

    def add_update_listener(self, callback):
        return lambda: None


class Hass:
    def __init__(self):
        self.config_entries = SimpleNamespace(
            async_forward_entry_setups=AsyncMock(), async_unload_platforms=AsyncMock(return_value=True),
            async_reload=AsyncMock(),
        )

    async def async_add_executor_job(self, function, *args):
        return await asyncio.to_thread(function, *args)


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.hass, self.entry = Hass(), Entry()
        self.coordinator = EW41Coordinator(self.hass, self.entry)
        self.coordinator.data = status()
        self.coordinator.client = Mock()

    async def test_climate_state_uses_actual_query(self):
        entity = EW41LivingClimate(self.coordinator)
        self.assertEqual(entity.target_temperature, 21)
        self.assertEqual(entity.current_temperature, 24)
        self.assertEqual(entity.hvac_mode, HVACMode.HEAT)
        self.coordinator.data = status(mask=14, target=20)
        self.assertEqual(entity.hvac_mode, HVACMode.OFF)
        self.assertEqual(entity.target_temperature, 20)

    async def test_temperature_success_publishes_verified_state(self):
        self.coordinator.client.set_temperature.return_value = protocol.ControlResult(True, status(target=22), "22")
        entity = EW41LivingClimate(self.coordinator)
        await entity.async_set_temperature(temperature=22)
        self.assertEqual(entity.target_temperature, 22)
        self.coordinator.client.set_temperature.assert_called_once_with(protocol.Room.LIVING, 22)

    async def test_target_mismatch_keeps_actual_temperature(self):
        self.coordinator.client.set_temperature.return_value = protocol.ControlResult(False, status(target=20), "22", ("target mismatch",))
        entity = EW41LivingClimate(self.coordinator)
        with self.assertRaises(HAError):
            await entity.async_set_temperature(temperature=22)
        self.assertEqual(entity.target_temperature, 20)
        self.assertTrue(entity.available)

    async def test_missing_final_query_marks_all_entities_unavailable(self):
        self.coordinator.client.set_temperature.return_value = protocol.ControlResult(False, None, "22", ("no query response",))
        with self.assertRaises(HAError):
            await EW41LivingClimate(self.coordinator).async_set_temperature(temperature=22)
        self.assertFalse(EW41LivingClimate(self.coordinator).available)
        self.assertFalse(EW41ModeSelect(self.coordinator).available)
        self.coordinator.client.get_status.return_value = status(target=22)
        await self.coordinator.async_refresh()
        self.assertTrue(EW41LivingClimate(self.coordinator).available)

    async def test_invalid_temperature_never_calls_device(self):
        for temperature in (9, 31, 21.2, float("nan"), True):
            with self.subTest(temperature=temperature), self.assertRaises(HAError):
                await EW41LivingClimate(self.coordinator).async_set_temperature(temperature=temperature)
        self.coordinator.client.set_temperature.assert_not_called()

    async def test_unknown_mode_is_not_assigned_a_known_mode(self):
        self.coordinator.data = status(mode=0x55)
        select = EW41ModeSelect(self.coordinator)
        self.assertIsNone(select.current_option)
        self.assertEqual(select.extra_state_attributes["raw_09"], "55")
        sensor = EW41DiagnosticSensor(self.coordinator, "mode_label", "mode")
        self.assertEqual(sensor.native_value, "알 수 없음 (0x55)")

    async def test_mode_selection_calls_correct_verified_method(self):
        self.coordinator.client.set_hotwater_only.return_value = protocol.ControlResult(True, status(mode=15), "mode")
        select = EW41ModeSelect(self.coordinator)
        await select.async_select_option("온수전용")
        self.assertEqual(select.current_option, "온수전용")
        self.coordinator.client.set_hotwater_only.assert_called_once_with()

    async def test_room_and_all_switches(self):
        self.coordinator.data = status(mask=14)
        self.assertFalse(EW41RoomSwitch(self.coordinator, protocol.Room.LIVING).is_on)
        self.assertTrue(EW41RoomSwitch(self.coordinator, protocol.Room.ROOM1).is_on)
        all_switch = EW41AllSwitch(self.coordinator)
        self.assertTrue(all_switch.is_on)
        self.assertFalse(all_switch.extra_state_attributes["all_rooms_on"])
        self.coordinator.client.set_all_rooms.return_value = protocol.ControlResult(True, status(mask=0), "off")
        await all_switch.async_turn_off()
        self.assertFalse(all_switch.is_on)
        self.coordinator.client.set_all_rooms.assert_called_once_with(False)

    async def test_poll_failure_is_update_failed(self):
        self.coordinator.client.get_status.side_effect = protocol.CommunicationError("timeout")
        with self.assertRaises(UpdateFailed):
            await self.coordinator._async_update_data()

    async def test_poll_and_command_do_not_overlap(self):
        guard = threading.Lock()
        active, maximum = 0, 0

        def work(command=False):
            nonlocal active, maximum
            with guard:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.025)
            with guard:
                active -= 1
            return protocol.ControlResult(True, status(), "ok") if command else status()

        self.coordinator.client.get_status.side_effect = work
        await asyncio.gather(self.coordinator._async_update_data(), self.coordinator.async_control(work, True),
                             self.coordinator._async_update_data())
        self.assertEqual(maximum, 1)

    async def test_setup_and_unload(self):
        with patch("custom_components.ew41_boiler.coordinator.EW41Client") as client:
            client.return_value.get_status.return_value = status()
            self.assertTrue(await integration.async_setup_entry(self.hass, self.entry))
        self.assertIsInstance(self.entry.runtime_data, EW41Coordinator)
        self.hass.config_entries.async_forward_entry_setups.assert_awaited_once()
        self.assertTrue(await integration.async_unload_entry(self.hass, self.entry))

    async def test_configuration_only_queries_status(self):
        flow = EW41ConfigFlow()
        flow.hass = self.hass
        with patch("custom_components.ew41_boiler.config_flow.EW41Client") as client:
            client.return_value.get_status.return_value = status()
            result = await flow.async_step_user(self.entry.data)
        self.assertEqual(result["type"], "create_entry")
        client.return_value.get_status.assert_called_once_with()
        self.assertEqual(flow.unique_id, "192.168.0.22:8899")

    async def test_configuration_connection_failure(self):
        flow = EW41ConfigFlow()
        flow.hass = self.hass
        with patch("custom_components.ew41_boiler.config_flow.EW41Client") as client:
            client.return_value.get_status.side_effect = protocol.CommunicationError("timeout")
            result = await flow.async_step_user(self.entry.data)
        self.assertEqual(result["errors"], {"base": "cannot_connect"})

    async def test_options_validation(self):
        self.assertFalse(valid_temperature_range({"min_temperature": 30, "max_temperature": 10}))
        self.assertFalse(valid_temperature_range({"min_temperature": 10.2, "max_temperature": 30}))
        flow = EW41OptionsFlow()
        flow.config_entry = self.entry
        self.assertEqual((await flow.async_step_init(self.entry.data))["type"], "create_entry")

    async def test_platform_entity_counts_and_unique_ids(self):
        self.entry.runtime_data = self.coordinator
        entities = []
        for platform in ("climate", "switch", "select", "sensor", "button", "binary_sensor"):
            adapter = importlib.import_module(f"custom_components.ew41_boiler.{platform}")
            await adapter.async_setup_entry(self.hass, self.entry, lambda values: entities.extend(values))
        self.assertEqual(len(entities), 23)
        self.assertEqual(len({entity._attr_unique_id for entity in entities}), 23)

    async def test_native_reservation_switch_routes_and_reads_actual_result(self):
        from custom_components.ew41_boiler.switch import EW41ReservationSwitch
        raw = list(status().raw[:-2])
        raw[8] = 2
        self.coordinator.client.set_reservation.return_value = protocol.ControlResult(True, protocol.BoilerStatus.from_response(protocol.make_packet(raw)), "reserve")
        entity = EW41ReservationSwitch(self.coordinator, protocol.Room.ROOM1)
        await entity.async_turn_on()
        self.assertTrue(entity.is_on)
        self.coordinator.client.set_reservation.assert_called_once_with(protocol.Room.ROOM1, True)

    async def test_four_climates_route_each_room_independently(self):
        from custom_components.ew41_boiler.climate import EW41RoomClimate
        for room in protocol.ROOM_ORDER:
            self.coordinator.client.set_temperature.reset_mock()
            raw = list(status().raw[:-2])
            raw[protocol.ROOM_TEMPERATURE_INDICES[room]] = 22
            self.coordinator.client.set_temperature.return_value = protocol.ControlResult(True, protocol.BoilerStatus.from_response(protocol.make_packet(raw)), "22")
            entity = EW41RoomClimate(self.coordinator, room)
            await entity.async_set_temperature(temperature=22)
            self.assertEqual(entity.target_temperature, 22)
            self.coordinator.client.set_temperature.assert_called_once_with(room, 22)

    async def test_physical_room1_reservation_capture(self):
        from custom_components.ew41_boiler.binary_sensor import EW41ReservationSensor
        self.coordinator.data = protocol.BoilerStatus.from_response(bytes.fromhex("F7 36 0F 81 0D 00 0D 00 02 00 14 18 14 98 14 18 14 18 CD D6"))
        self.assertTrue(EW41ReservationSensor(self.coordinator, protocol.Room.ROOM1).is_on)
        self.assertFalse(EW41ReservationSensor(self.coordinator, protocol.Room.LIVING).is_on)

    async def test_manual_refresh_failure(self):
        self.coordinator.client.get_status.side_effect = protocol.CommunicationError("timeout")
        with self.assertRaises(HAError):
            await EW41RefreshButton(self.coordinator).async_press()


if __name__ == "__main__":
    unittest.main(verbosity=2)
