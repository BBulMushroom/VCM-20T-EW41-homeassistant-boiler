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

    def test_heating_uses_addresses_not_status_masks(self):
        packets = ("F7 36 11 43 01 00 92 14", "F7 36 12 43 01 00 91 14", "F7 36 13 43 01 00 90 14", "F7 36 14 43 01 00 97 1C")
        for room, expected in zip(p.ROOM_ORDER, packets):
            raw = bytearray(BASE[:-2])
            raw[6] = 15 ^ int(room)
            with self.subTest(room=room), patch.object(p.EW41Client, "_send") as send, patch.object(p.EW41Client, "get_status", return_value=p.BoilerStatus.from_response(p.make_packet(raw))), patch.object(p.time, "sleep"):
                self.assertTrue(p.EW41Client().set_room(room, False).success)
                send.assert_called_once_with(bytes.fromhex(expected))

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
