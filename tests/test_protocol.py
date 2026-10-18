"""Regression evidence from real-device room tests; no LAN connection."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

path = Path(__file__).resolve().parents[1] / "custom_components/ew41_boiler/protocol.py"
spec = importlib.util.spec_from_file_location("standalone_protocol", path)
p = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = p
spec.loader.exec_module(p)

BASE = bytes.fromhex("F7 36 0F 81 0D 00 0F 00 00 00 14 18 14 98 14 18 14 18 CD D6")
CAPTURES = (
    (p.Room.LIVING, "F7 36 11 44 01 15 80 18", "F7 36 0F 81 0D 00 0F 00 00 00 15 18 14 98 14 18 14 18 CC D6"),
    (p.Room.ROOM1, "F7 36 12 44 01 15 83 1C", "F7 36 0F 81 0D 00 0F 00 00 00 14 18 15 98 14 18 14 18 CC D6"),
    (p.Room.ROOM2, "F7 36 13 44 01 15 82 1C", "F7 36 0F 81 0D 00 0F 00 00 00 14 18 14 98 15 18 14 18 CC D6"),
    (p.Room.ROOM3, "F7 36 14 44 01 15 85 20", "F7 36 0F 81 0D 00 0F 00 00 00 14 18 14 98 14 18 15 18 CC D6"),
)

class ProtocolTests(unittest.TestCase):
    def test_repeat_ignores_only_its_own_incidental_main_demand(self):
        for owned in (False, True):
            raw = bytearray(BASE[:-2])
            raw[6:10] = bytes([3, 0, 0, 0])
            before = p.BoilerStatus.from_response(p.make_packet(raw))
            raw[6:8] = bytes([1, 2])
            paused = p.BoilerStatus.from_response(p.make_packet(raw))
            raw[6], raw[9] = 0, 1
            water = p.BoilerStatus.from_response(p.make_packet(raw))
            states = [before, paused, water, water] if owned else [before, paused, paused]
            client = p.EW41Client()
            client.repeat_main_owned = owned
            with self.subTest(owned=owned), patch.object(client, '_send', return_value=b'ack') as send, patch.object(client, 'get_status', side_effect=states), patch.object(p.time, 'sleep'):
                result = client.set_repeat_heating(p.Room.ROOM1, False)
                self.assertTrue(result.success)
                self.assertEqual(result.status.room_mask, 0 if owned else 1)
                self.assertEqual([c.args[0][3] for c in send.call_args_list], [0x43, 0x47] if owned else [0x43])

    def test_repeat_last_main_room_goes_directly_to_hotwater_only(self):
        def state(mask, mode=0, away=0, reserved=0):
            raw = bytearray(BASE[:-2])
            raw[6:10] = bytes([mask, away, reserved, mode])
            return p.BoilerStatus.from_response(p.make_packet(raw))
        final = state(0, 1)
        with patch.object(p.EW41Client, '_send', return_value=b'ack') as send, patch.object(p.EW41Client, 'get_status', side_effect=[state(1), final, final]), patch.object(p.time, 'sleep'):
            result = p.EW41Client().set_repeat_heating(p.Room.LIVING, False)
            self.assertTrue(result.success)
            self.assertEqual(result.status.mode, p.OperatingMode.HOTWATER_ONLY)
            self.assertEqual([c.args[0] for c in send.call_args_list], [bytes.fromhex('F7 36 11 47 01 01 97 1E')])

    def test_repeat_keeps_other_rooms_heating_or_reserved(self):
        for reserved in (False, True):
            raw = bytearray(BASE[:-2])
            raw[6:10] = bytes([2 if reserved else 6, 0, 4 if reserved else 0, 0])
            before = p.BoilerStatus.from_response(p.make_packet(raw))
            raw[6] &= ~2
            raw[7] = 2
            after = p.BoilerStatus.from_response(p.make_packet(raw))
            with self.subTest(reserved=reserved), patch.object(p.EW41Client, '_send') as send, patch.object(p.EW41Client, 'get_status', side_effect=[before, after, after]), patch.object(p.time, 'sleep'):
                result = p.EW41Client().set_repeat_heating(p.Room.ROOM1, False)
                self.assertTrue(result.success)
                send.assert_called_once_with(p.make_packet([0xF7, 0x36, 0x12, 0x43, 1, 0]))

    def test_repeat_last_secondary_room_pauses_room_then_keeps_water(self):
        raw = bytearray(BASE[:-2])
        raw[6:10] = bytes([2, 0, 0, 0])
        before = p.BoilerStatus.from_response(p.make_packet(raw))
        raw[6:8] = bytes([0, 2])
        paused = p.BoilerStatus.from_response(p.make_packet(raw))
        raw[9] = 1
        final = p.BoilerStatus.from_response(p.make_packet(raw))
        with patch.object(p.EW41Client, '_send', return_value=b'ack') as send, patch.object(p.EW41Client, 'get_status', side_effect=[before, paused, final, final]), patch.object(p.time, 'sleep'):
            self.assertTrue(p.EW41Client().set_repeat_heating(p.Room.ROOM1, False).success)
            self.assertEqual([c.args[0][3] for c in send.call_args_list], [0x43, 0x47])
            self.assertEqual(send.call_args_list[-1].args[0][5], 1)

    def test_repeat_resume_does_not_leave_incidental_living_heating(self):
        def state(mask, mode, away=0):
            raw = bytearray(BASE[:-2])
            raw[6:10] = bytes([mask, away, 0, mode])
            return p.BoilerStatus.from_response(p.make_packet(raw))
        final = state(2, 0, 1)
        with patch.object(p.EW41Client, '_send', return_value=b'ack') as send, patch.object(p.EW41Client, 'get_status', side_effect=[state(0, 1), state(2, 1), state(3, 0), final, final]), patch.object(p.time, 'sleep'):
            result = p.EW41Client().set_repeat_heating(p.Room.ROOM1, True)
            self.assertTrue(result.success)
            self.assertFalse(result.status.is_on(p.Room.LIVING))
            self.assertEqual([(c.args[0][2], c.args[0][3], c.args[0][5]) for c in send.call_args_list], [(0x12, 0x43, 1), (0x11, 0x47, 0), (0x11, 0x43, 1), (0x11, 0x43, 0)])

    def test_repeat_already_idle_sends_no_commands(self):
        raw = bytearray(BASE[:-2])
        raw[6:10] = bytes([0, 0, 0, 1])
        final = p.BoilerStatus.from_response(p.make_packet(raw))
        with patch.object(p.EW41Client, '_send') as send, patch.object(p.EW41Client, 'get_status', return_value=final), patch.object(p.time, 'sleep'):
            self.assertTrue(p.EW41Client().set_repeat_heating(p.Room.ROOM1, False).success)
            send.assert_not_called()

    def test_repeat_water_mode_not_applied_is_failure(self):
        raw = bytearray(BASE[:-2])
        raw[6:10] = bytes([0, 1, 0, 0])
        unchanged = p.BoilerStatus.from_response(p.make_packet(raw))
        with patch.object(p.EW41Client, '_send', return_value=b'ack'), patch.object(p.EW41Client, 'get_status', return_value=unchanged), patch.object(p.time, 'sleep'):
            self.assertFalse(p.EW41Client().set_repeat_heating(p.Room.LIVING, False).success)

    def test_repeat_pause_uses_away_instead_of_power_off_for_each_room(self):
        for room in p.ROOM_ORDER:
            raw = bytearray(BASE[:-2])
            raw[6] &= ~int(room)
            raw[7] = int(room)
            away = p.BoilerStatus.from_response(p.make_packet(raw))
            with self.subTest(room=room), patch.object(p.EW41Client, "_send") as send, patch.object(p.EW41Client, "get_status", return_value=away), patch.object(p.time, "sleep"):
                result = p.EW41Client().set_away(room)
                self.assertTrue(result.success)
                send.assert_called_once_with(p.make_packet([0xF7, 0x36, p.ROOM_ADDRESSES[room], 0x43, 1, 0]))
                self.assertFalse(result.status.is_off(room))
                self.assertEqual(result.status.raw[10:18], BASE[10:18])

    def test_power_off_or_still_heating_is_not_successful_repeat_pause(self):
        off = bytearray(BASE[:-2])
        off[6] = 0
        for state in (p.BoilerStatus.from_response(BASE), p.BoilerStatus.from_response(p.make_packet(off))):
            with self.subTest(state=state), patch.object(p.EW41Client, "_send"), patch.object(p.EW41Client, "get_status", return_value=state), patch.object(p.time, "sleep"):
                self.assertFalse(p.EW41Client().set_away(p.Room.LIVING).success)

    def test_physical_main_mode_queries_and_ack_have_different_encodings(self):
        cases = (
            ("F7 36 0F 81 0D 00 0F 00 00 00 14 98 14 19 14 97 14 97 CC D4", p.OperatingMode.HEATING_HOTWATER, 0),
            ("F7 36 0F 81 0D 00 0E 00 00 01 14 98 14 19 14 97 14 97 CC D4", p.OperatingMode.HOTWATER_ONLY, 1),
            ("F7 36 11 C7 0D 00 0E 00 00 01 14 98 14 19 14 97 14 97 94 E4", p.OperatingMode.HEATING_HOTWATER, 1),
            ("F7 36 11 C7 0D 00 0E 00 00 0F 14 98 14 19 14 97 14 97 9A F8", p.OperatingMode.HOTWATER_ONLY, 15),
        )
        for raw, mode, value in cases:
            with self.subTest(raw=raw):
                status = p.BoilerStatus.from_response(bytes.fromhex(raw))
                self.assertEqual(status.mode, mode)
                self.assertEqual(status.mode_value, value)

    def test_main_hotwater_ack_is_verified_against_query_value_one(self):
        ack = bytes.fromhex("F7 36 11 C7 0D 00 0E 00 00 0F 14 98 14 19 14 97 14 97 9A F8")
        query = p.BoilerStatus.from_response(bytes.fromhex("F7 36 0F 81 0D 00 0E 00 00 01 14 98 14 19 14 97 14 97 CC D4"))
        with patch.object(p.EW41Client, "_send", return_value=ack) as send, patch.object(p.EW41Client, "get_status", return_value=query), patch.object(p.time, "sleep"):
            result = p.EW41Client().set_hotwater_only()
            self.assertTrue(result.success)
            self.assertEqual(result.status.mode_label, "온수전용")
            send.assert_called_once_with(bytes.fromhex("F7 36 11 47 01 01 97 1E"), p.MODE_HEADER)

    def test_heating_mode_restores_main_heating_and_verifies_zero(self):
        ack = bytes.fromhex("F7 36 11 C7 0D 00 0E 00 00 01 14 98 14 19 14 97 14 97 94 E4")
        with patch.object(p.EW41Client, "_send", return_value=ack) as send, patch.object(p.EW41Client, "get_status", return_value=p.BoilerStatus.from_response(BASE)), patch.object(p.time, "sleep"):
            result = p.EW41Client().set_heating_hotwater_mode()
            self.assertTrue(result.success)
            self.assertEqual(result.status.mode_label, "난방 + 온수")
            self.assertEqual([call.args[0] for call in send.call_args_list], [bytes.fromhex("F7 36 11 47 01 00 96 1C"), bytes.fromhex("F7 36 11 43 01 01 93 16")])

    def test_ack_without_applied_main_mode_remains_failure(self):
        ack = bytes.fromhex("F7 36 11 C7 0D 00 0E 00 00 0F 14 98 14 19 14 97 14 97 9A F8")
        with patch.object(p.EW41Client, "_send", return_value=ack), patch.object(p.EW41Client, "get_status", return_value=p.BoilerStatus.from_response(BASE)), patch.object(p.time, "sleep"):
            self.assertFalse(p.EW41Client().set_hotwater_only().success)

    def test_native_reservation_packets_and_status_against_all_room_live_tests(self):
        packets = ("F7 36 11 46 01 01 96 1C", "F7 36 12 46 01 01 95 1C", "F7 36 13 46 01 01 94 1C", "F7 36 14 46 01 01 93 1C")
        for room, expected in zip(p.ROOM_ORDER, packets):
            raw = bytearray(BASE[:-2])
            raw[6] &= ~int(room)
            raw[8] = int(room)
            with self.subTest(room=room), patch.object(p.EW41Client, "_send") as send, patch.object(p.EW41Client, "get_status", return_value=p.BoilerStatus.from_response(p.make_packet(raw))), patch.object(p.time, "sleep"):
                self.assertTrue(p.EW41Client().set_reservation(room, True).success)
                send.assert_called_once_with(bytes.fromhex(expected))

    def test_reservation_not_applied_is_reported_as_failure(self):
        with patch.object(p.EW41Client, "_send"), patch.object(p.EW41Client, "get_status", return_value=p.BoilerStatus.from_response(BASE)), patch.object(p.time, "sleep"):
            self.assertFalse(p.EW41Client().set_reservation(p.Room.ROOM1, True).success)

    def test_native_reservation_off_returns_to_heating(self):
        with patch.object(p.EW41Client, "_send") as send, patch.object(p.EW41Client, "get_status", return_value=p.BoilerStatus.from_response(BASE)), patch.object(p.time, "sleep"):
            result = p.EW41Client().set_reservation(p.Room.ROOM1, False)
            self.assertTrue(result.success)
            self.assertTrue(result.status.is_on(p.Room.ROOM1))
            send.assert_called_once_with(bytes.fromhex("F7 36 12 46 01 00 94 1A"))
    def test_all_room_write_packets_against_live_captures(self):
        for room, tx, rx in CAPTURES:
            with self.subTest(room=room), patch.object(p.EW41Client, "_send") as send, patch.object(p.EW41Client, "get_status", return_value=p.BoilerStatus.from_response(bytes.fromhex(rx))), patch.object(p.time, "sleep"):
                result = p.EW41Client().set_temperature(room, 21)
                self.assertTrue(result.success)
                send.assert_called_once_with(bytes.fromhex(tx))
                self.assertEqual(result.status.target_temperature(room), 21)
                for other in p.ROOM_ORDER:
                    if other != room:
                        self.assertEqual(result.status.target_temperature(other), 20)

    def test_power_off_uses_addresses_not_status_masks(self):
        packets = ("F7 36 11 50 01 01 80 10", "F7 36 12 50 01 01 83 14", "F7 36 13 50 01 01 82 14", "F7 36 14 50 01 01 85 18")
        for room, expected in zip(p.ROOM_ORDER, packets):
            raw = bytearray(BASE[:-2])
            raw[6] = 15 ^ int(room)
            with self.subTest(room=room), patch.object(p.EW41Client, "_send") as send, patch.object(p.EW41Client, "get_status", return_value=p.BoilerStatus.from_response(p.make_packet(raw))), patch.object(p.time, "sleep"):
                self.assertTrue(p.EW41Client().set_room(room, False).success)
                send.assert_called_once_with(bytes.fromhex(expected))

    def test_physical_room1_power_off_clears_away_without_changing_other_rooms(self):
        before = p.BoilerStatus.from_response(bytes.fromhex("F7 36 0F 81 0D 00 00 0F 00 00 14 98 14 19 14 97 14 97 CC D4"))
        after = p.BoilerStatus.from_response(bytes.fromhex("F7 36 0F 81 0D 00 00 0D 00 00 14 98 14 19 14 97 14 97 CE D4"))
        self.assertFalse(before.is_off(p.Room.ROOM1))
        self.assertTrue(before.is_away(p.Room.ROOM1))
        self.assertTrue(after.is_off(p.Room.ROOM1))
        self.assertFalse(after.is_away(p.Room.ROOM1))
        for room in (p.Room.LIVING, p.Room.ROOM2, p.Room.ROOM3):
            self.assertEqual(after.is_away(room), before.is_away(room))
        self.assertEqual(after.raw[10:18], before.raw[10:18])

    def test_heating_off_with_away_still_on_is_not_power_off_success(self):
        away = p.BoilerStatus.from_response(bytes.fromhex("F7 36 0F 81 0D 00 00 0F 00 00 14 98 14 19 14 97 14 97 CC D4"))
        with patch.object(p.EW41Client, "_send"), patch.object(p.EW41Client, "get_status", return_value=away), patch.object(p.time, "sleep"):
            self.assertFalse(p.EW41Client().set_room(p.Room.ROOM1, False).success)
            self.assertFalse(p.EW41Client().set_all_rooms(False).success)

    def test_native_reservation_still_on_is_not_power_off_success(self):
        raw = bytearray(BASE[:-2])
        raw[6] = 0
        raw[8] = int(p.Room.ROOM1)
        reserved = p.BoilerStatus.from_response(p.make_packet(raw))
        with patch.object(p.EW41Client, "_send"), patch.object(p.EW41Client, "get_status", return_value=reserved), patch.object(p.time, "sleep"):
            self.assertFalse(p.EW41Client().set_room(p.Room.ROOM1, False).success)

    def test_all_room_power_off_clears_all_three_mode_masks(self):
        raw = bytearray(BASE[:-2])
        raw[6:9] = bytes([0, 0, 0])
        off = p.BoilerStatus.from_response(p.make_packet(raw))
        with patch.object(p.EW41Client, "_send") as send, patch.object(p.EW41Client, "get_status", return_value=off), patch.object(p.time, "sleep"):
            self.assertTrue(p.EW41Client().set_all_rooms(False).success)
            self.assertEqual([call.args[0] for call in send.call_args_list],
                             [bytes.fromhex(packet) for packet in ("F7 36 11 50 01 01 80 10", "F7 36 12 50 01 01 83 14", "F7 36 13 50 01 01 82 14", "F7 36 14 50 01 01 85 18")])

    def test_delayed_application_queries_again_without_repeating_write(self):
        changed = p.BoilerStatus.from_response(bytes.fromhex(CAPTURES[1][2]))
        with patch.object(p.EW41Client, "_send") as send, patch.object(p.EW41Client, "get_status", side_effect=[p.BoilerStatus.from_response(BASE), changed]) as query, patch.object(p.time, "sleep"):
            self.assertTrue(p.EW41Client().set_temperature(p.Room.ROOM1, 21).success)
            self.assertEqual(query.call_count, 2)
            self.assertEqual(send.call_count, 1)

    def test_timeout_then_valid_response_can_verify(self):
        changed = p.BoilerStatus.from_response(bytes.fromhex(CAPTURES[1][2]))
        with patch.object(p.EW41Client, "_send"), patch.object(p.EW41Client, "get_status", side_effect=[p.CommunicationError("timeout"), changed]), patch.object(p.time, "sleep"):
            self.assertTrue(p.EW41Client().set_temperature(p.Room.ROOM1, 21).success)

    def test_wrong_room_change_is_not_success(self):
        changed = p.BoilerStatus.from_response(bytes.fromhex(CAPTURES[0][2]))
        with patch.object(p.EW41Client, "_send"), patch.object(p.EW41Client, "get_status", return_value=changed), patch.object(p.time, "sleep"):
            result = p.EW41Client().set_temperature(p.Room.ROOM1, 21)
            self.assertFalse(result.success)
            self.assertEqual(result.status.target_temperature(p.Room.ROOM1), 20)

    def test_final_timeout_does_not_publish_stale_earlier_state(self):
        with patch.object(p.EW41Client, "_send"), patch.object(p.EW41Client, "get_status", side_effect=[p.BoilerStatus.from_response(BASE), p.CommunicationError("timeout"), p.CommunicationError("timeout")]), patch.object(p.time, "sleep"):
            result = p.EW41Client().set_temperature(p.Room.ROOM1, 21)
            self.assertFalse(result.success)
            self.assertIsNone(result.status)
