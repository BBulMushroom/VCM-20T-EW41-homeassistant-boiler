"""Use only the EW41 packet forms and status fields confirmed by the user.

All control operations query the final state. Heating commands do not wait for
an acknowledgement. No network connection is made when this module is imported.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import logging
import math
import socket
import threading
import time
from typing import Iterable


LOGGER = logging.getLogger(__name__)
STATUS_HEADER = bytes.fromhex("F7 36 0F 81 0D")
MODE_HEADER = bytes.fromhex("F7 36 11 C7 0D")
STATUS_FRAME_SIZE = 20  # Size of the three confirmed response examples.


def make_packet(data: Iterable[int]) -> bytes:
    """Append XOR and (SUM + XOR) & 0xFF to the supplied bytes."""
    payload = bytes(data)
    xor_value = 0
    for value in payload:
        xor_value ^= value
    sum_value = (sum(payload) + xor_value) & 0xFF
    return payload + bytes((xor_value, sum_value))


def decode_temperature(value: int) -> float:
    """Low seven bits: whole degrees; bit 7: an additional 0.5 C."""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255:
        raise ValueError("온도 바이트는 0~255 정수여야 합니다.")
    return (value & 0x7F) + (0.5 if value & 0x80 else 0.0)


def encode_temperature(temperature: float) -> int:
    """Validate the encoding, without assuming the thermostat's allowed range."""
    if (isinstance(temperature, bool) or not isinstance(temperature, (int, float))
            or not math.isfinite(temperature) or not 0 <= temperature <= 127.5
            or temperature * 2 != int(temperature * 2)):
        raise ValueError("온도는 0~127.5 사이의 유한한 숫자이며 0.5℃ 단위여야 합니다.")
    whole = int(temperature)
    return whole | (0x80 if temperature - whole == 0.5 else 0)


STATUS_PACKET = make_packet([0xF7, 0x36, 0x0F, 0x01, 0x00])


class Room(IntEnum):
    LIVING = 0x01
    ROOM1 = 0x02
    ROOM2 = 0x04
    ROOM3 = 0x08

    @property
    def label(self) -> str:
        return ROOM_LABELS[self]


ROOM_LABELS = {
    Room.LIVING: "거실",
    Room.ROOM1: "방1",
    Room.ROOM2: "방2",
    Room.ROOM3: "방3",
}
ROOM_ORDER = (Room.LIVING, Room.ROOM1, Room.ROOM2, Room.ROOM3)
# Device tests: packet address differs from the heating status bitmask.
ROOM_ADDRESSES = {Room.LIVING: 0x11, Room.ROOM1: 0x12, Room.ROOM2: 0x13, Room.ROOM3: 0x14}
ROOM_TEMPERATURE_INDICES = {Room.LIVING: 10, Room.ROOM1: 12, Room.ROOM2: 14, Room.ROOM3: 16}


class OperatingMode(IntEnum):
    HEATING_HOTWATER = 0x01
    HOTWATER_ONLY = 0x0F

    @property
    def label(self) -> str:
        return "난방 + 온수" if self == self.HEATING_HOTWATER else "온수전용"


class EW41Error(Exception):
    """Base class for communication or response validation errors."""


class CommunicationError(EW41Error):
    pass


class InvalidResponse(EW41Error):
    pass


class _AcknowledgementError(EW41Error):
    """The command was sent, but its optional response could not be read."""


@dataclass(frozen=True)
class BoilerStatus:
    room_mask: int
    mode_value: int
    raw: bytes

    @classmethod
    def from_response(cls, response: bytes) -> BoilerStatus:
        if len(response) != STATUS_FRAME_SIZE:
            raise InvalidResponse(f"상태 응답 길이 오류: {len(response)}바이트 (예상 20)")
        if response[:5] not in (STATUS_HEADER, MODE_HEADER):
            raise InvalidResponse("확인된 상태 응답 헤더와 일치하지 않습니다.")
        if make_packet(response[:-2]) != response:
            raise InvalidResponse("상태 응답 체크섬 오류")
        return cls(room_mask=response[6], mode_value=response[9], raw=response)

    @property
    def mode(self) -> OperatingMode | None:
        # Physical main-controller tests: status-query DATA4 uses 00/01,
        # while the 0x47 acknowledgement uses the earlier confirmed 01/0F.
        # Preserve the raw byte; these two frame types must not share a decoder.
        if self.raw[:5] == STATUS_HEADER:
            return {
                0x00: OperatingMode.HEATING_HOTWATER,
                0x01: OperatingMode.HOTWATER_ONLY,
                0x0F: OperatingMode.HOTWATER_ONLY,
            }.get(self.mode_value)
        try:
            return OperatingMode(self.mode_value)
        except ValueError:
            return None

    @property
    def mode_label(self) -> str:
        mode = self.mode
        return mode.label if mode is not None else f"알 수 없음 (0x{self.mode_value:02X})"

    @property
    def living_target_temperature(self) -> float:
        return self.target_temperature(Room.LIVING)

    @property
    def living_current_temperature(self) -> float:
        """Index 11 according to the linked source; compare with the display."""
        return self.current_temperature(Room.LIVING)

    def target_temperature(self, room: Room | int) -> float:
        """Per-room target indices confirmed by isolated 20 -> 21 -> 20 tests."""
        return decode_temperature(self.raw[ROOM_TEMPERATURE_INDICES[_validate_room(room)]])

    def current_temperature(self, room: Room | int) -> float:
        """Current values per linked source; physical displays still need comparison."""
        return decode_temperature(self.raw[ROOM_TEMPERATURE_INDICES[_validate_room(room)] + 1])

    @property
    def away_mask(self) -> int:
        return self.raw[7]

    def is_away(self, room: Room | int) -> bool:
        return bool(self.away_mask & _validate_room(room).value)

    def is_off(self, room: Room | int) -> bool:
        """Power OFF clears heating, away and native reservation for this room."""
        bit = _validate_room(room).value
        return not ((self.room_mask | self.away_mask | self.reservation_mask) & bit)

    @property
    def reservation_mask(self) -> int:
        """Index 8: source layout, with room1 bit verified by a physical reservation."""
        return self.raw[8]

    def is_reserved(self, room: Room | int) -> bool:
        return bool(self.reservation_mask & _validate_room(room).value)

    def is_on(self, room: Room | int) -> bool:
        room = _validate_room(room)
        return bool(self.room_mask & room.value)

    def to_dict(self) -> dict:
        return {
            "rooms": {room.name.lower(): self.is_on(room) for room in ROOM_ORDER},
            "operating_mode": self.mode.name.lower() if self.mode is not None else "unknown",
            "operating_mode_label": self.mode_label,
            "room_mask": self.room_mask,
            "mode_value": self.mode_value,
            "raw_06": f"{self.room_mask:02X}",
            "raw_07": f"{self.away_mask:02X}",
            "raw_09": f"{self.mode_value:02X}",
            "raw_hex": self.raw.hex(" ").upper(),
            "living_target_temperature_c": self.living_target_temperature,
            "living_current_temperature_c": self.living_current_temperature,
            "reservation_mask": self.reservation_mask,
            "away_mask": self.away_mask,
            "away_rooms": {room.name.lower(): self.is_away(room) for room in ROOM_ORDER},
            "off_rooms": {room.name.lower(): self.is_off(room) for room in ROOM_ORDER},
            "reserved_rooms": {room.name.lower(): self.is_reserved(room) for room in ROOM_ORDER},
            "temperatures": {
                room.name.lower(): {"target_c": self.target_temperature(room),
                                    "current_c": self.current_temperature(room)}
                for room in ROOM_ORDER
            },
        }

    def format(self, include_raw: bool = False) -> str:
        lines = [f"{room.label:<3} : {'ON' if self.is_on(room) else 'OFF'}" for room in ROOM_ORDER]
        lines.append(f"운전모드 : {self.mode_label}")
        for room in ROOM_ORDER:
            lines.append(f"{room.label} 설정온도 : {self.target_temperature(room):g}℃")
            lines.append(f"{room.label} 현재온도 (참고 소스 해석) : {self.current_temperature(room):g}℃")
        if include_raw:
            lines.extend((f"[06]={self.room_mask:02X}", f"[09]={self.mode_value:02X}",
                          f"RAW RX: {self.raw.hex(' ').upper()}"))
        return "\n".join(lines)


@dataclass(frozen=True)
class ControlResult:
    success: bool
    status: BoilerStatus | None
    requested: str
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    command_response: bytes | None = None

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "requested": self.requested,
            "status": self.status.to_dict() if self.status is not None else None,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "command_response_hex": (
                self.command_response.hex(" ").upper() if self.command_response else None
            ),
        }


def _validate_room(room: Room | int) -> Room:
    if isinstance(room, bool) or not isinstance(room, (int, Room)):
        raise ValueError("room은 Room 또는 정수 0x01, 0x02, 0x04, 0x08이어야 합니다.")
    try:
        return Room(room)
    except ValueError as exc:
        raise ValueError("방 값은 01, 02, 04, 08만 허용합니다. 0F 전체방 명령은 지원하지 않습니다.") from exc


def _validate_on(on: bool) -> None:
    if not isinstance(on, bool):
        raise ValueError("on은 True 또는 False여야 합니다.")


class EW41Client:
    """Synchronous API. Share one instance to serialize concurrent control calls.

    Heating commands: send, briefly hold TCP open, settle, query. Mode commands: optionally read
    the known acknowledgement, then settle and query. A missing acknowledgement
    alone never decides whether a control operation succeeded.
    """

    def __init__(
        self,
        host: str = "192.168.0.22",
        port: int = 8899,
        timeout: float = 2.0,
        room_interval: float = 0.2,
        settle_delay: float = 0.4,
    ) -> None:
        if not isinstance(host, str) or not host.strip():
            raise ValueError("host가 비어 있습니다.")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("port는 1~65535 정수여야 합니다.")
        for name, value, minimum in (
            ("timeout", timeout, 0.0),
            ("room_interval", room_interval, 0.2),
            ("settle_delay", settle_delay, 0.3),
        ):
            if not math.isfinite(value) or value < minimum or (name == "timeout" and value == 0):
                raise ValueError(f"{name} 값이 허용 범위를 벗어났습니다.")
        self.host = host
        self.port = port
        self.timeout = timeout
        self.room_interval = room_interval
        self.settle_delay = settle_delay
        self._lock = threading.RLock()

    def _receive_state(self, sock: socket.socket, header: bytes) -> bytes:
        # TCP recv() may contain a partial frame or several frames together.
        deadline = time.monotonic() + self.timeout
        buffer = bytearray()
        invalid_reason: str | None = None
        while True:
            start = buffer.find(header)
            while start >= 0 and len(buffer) - start >= STATUS_FRAME_SIZE:
                frame = bytes(buffer[start:start + STATUS_FRAME_SIZE])
                try:
                    BoilerStatus.from_response(frame)
                except InvalidResponse as exc:
                    invalid_reason = str(exc)
                    del buffer[:start + 1]
                    start = buffer.find(header)
                    continue
                return frame
            if len(buffer) > 8192:
                raise InvalidResponse("응답에서 확인된 상태 프레임을 찾지 못했습니다 (8192바이트 초과).")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sock.settimeout(remaining)
            try:
                chunk = sock.recv(1024)
            except socket.timeout:
                break
            if not chunk:
                break
            LOGGER.debug("RX: %s", chunk.hex(" ").upper())
            buffer.extend(chunk)
        if invalid_reason:
            raise InvalidResponse(invalid_reason)
        if buffer:
            raise InvalidResponse(f"상태 응답이 불완전하거나 확인된 형식이 아닙니다: {bytes(buffer).hex(' ').upper()}")
        raise CommunicationError(f"상태 응답 없음 (timeout {self.timeout:g}초)")

    def _send(self, packet: bytes, response_header: bytes | None = None) -> bytes | None:
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
                sock.settimeout(self.timeout)
                LOGGER.debug("TX: %s", packet.hex(" ").upper())
                sock.sendall(packet)
                if response_header is None:
                    # Immediate close caused intermittent missed writes in live tests.
                    # Keep TCP open for UART forwarding without requiring an ACK.
                    time.sleep(0.2)
                    return None
                try:
                    return self._receive_state(sock, response_header)
                except (EW41Error, OSError) as exc:
                    if response_header == MODE_HEADER:
                        raise _AcknowledgementError(str(exc)) from exc
                    raise
        except OSError as exc:
            raise CommunicationError(f"EW41 TCP 통신 오류 ({self.host}:{self.port}): {exc}") from exc

    def get_status(self) -> BoilerStatus:
        """Query status; raise EW41Error if no valid confirmed frame is received."""
        with self._lock:
            raw = self._send(STATUS_PACKET, STATUS_HEADER)
            assert raw is not None
            return BoilerStatus.from_response(raw)

    def _verify(self, requested: str, matches, errors: list[str],
                warnings: list[str] | None = None,
                command_response: bytes | None = None) -> ControlResult:
        time.sleep(self.settle_delay)
        status = None
        last_error = None
        for attempt in range(3):
            try:
                status = self.get_status()
                last_error = None
                if matches(status):
                    break
            except EW41Error as exc:
                last_error = exc
            if attempt < 2:
                time.sleep(0.5)
        if last_error is not None or status is None:
            errors.append(f"최종 상태 확인 실패: {last_error}")
            return ControlResult(False, None, requested, tuple(errors), tuple(warnings or ()), command_response)
        if not matches(status):
            errors.append("최종 상태가 요청한 목표와 일치하지 않습니다.")
        return ControlResult(not errors, status, requested, tuple(errors), tuple(warnings or ()), command_response)

    def set_room(self, room: Room | int, on: bool) -> ControlResult:
        room = _validate_room(room)
        _validate_on(on)
        with self._lock:
            errors: list[str] = []
            # 0x43 OFF enters away mode on this controller. The reference
            # implementation's 0x50 / 01 switches off the physical display.
            # Use normal heating ON (0x43 / 01), and actual power OFF (0x50 / 01).
            packet = make_packet([0xF7, 0x36, ROOM_ADDRESSES[room],
                                  0x43 if on else 0x50, 0x01, 0x01])
            try:
                self._send(packet)
            except EW41Error as exc:
                errors.append(f"{room.label} 명령 전송 실패: {exc}")
            return self._verify(f"{room.label} {'ON' if on else 'OFF'}",
                                lambda status: status.is_on(room) if on else status.is_off(room), errors)

    def set_all_rooms(self, on: bool) -> ControlResult:
        _validate_on(on)
        with self._lock:
            errors: list[str] = []
            for index, room in enumerate(ROOM_ORDER):
                if index:
                    time.sleep(self.room_interval)
                packet = make_packet([0xF7, 0x36, ROOM_ADDRESSES[room],
                                      0x43 if on else 0x50, 0x01, 0x01])
                try:
                    self._send(packet)
                except EW41Error as exc:
                    # Continue with the remaining rooms; report partial failures.
                    errors.append(f"{room.label} 명령 전송 실패: {exc}")
            return self._verify(f"전체방 {'ON' if on else 'OFF'}",
                                lambda status: status.room_mask == 0x0F if on else
                                all(status.is_off(room) for room in ROOM_ORDER), errors)

    def set_reservation(self, room: Room | int, on: bool) -> ControlResult:
        """Native saved timer ON/OFF, verified on all four rooms (KS X 4506-9 7.12).

        The native interval and run duration are configured on the thermostat.
        Disabling native reservation returns that room to normal heating.
        """
        room = _validate_room(room)
        _validate_on(on)
        with self._lock:
            errors: list[str] = []
            packet = make_packet([0xF7, 0x36, ROOM_ADDRESSES[room], 0x46, 0x01, int(on)])
            try:
                self._send(packet)
            except EW41Error as exc:
                errors.append(f"{room.label} 예약 명령 전송 실패: {exc}")
            return self._verify(f"{room.label} 본체 예약 {'ON' if on else 'OFF'}",
                                lambda status: status.is_reserved(room) == on, errors)

    def _set_mode(self, mode: OperatingMode, command_value: int) -> ControlResult:
        with self._lock:
            warnings: list[str] = []
            errors: list[str] = []
            response = None
            packet = make_packet([0xF7, 0x36, 0x11, 0x47, 0x01, command_value])
            try:
                response = self._send(packet, MODE_HEADER)
            except _AcknowledgementError as exc:
                # The final query is authoritative, even if the ACK is absent.
                warnings.append(f"운전모드 명령 통신/응답 확인 문제: {exc}")
            except EW41Error as exc:
                errors.append(f"운전모드 명령 전송 실패: {exc}")
            if mode == OperatingMode.HEATING_HOTWATER and not errors:
                # On this controller, 0x47 OFF acknowledges the request but does
                # not restore normal heating. The verified main (living) 0x43 ON
                # command returns query DATA4 to 00. Other rooms are untouched.
                time.sleep(self.room_interval)
                try:
                    self._send(make_packet([0xF7, 0x36, 0x11, 0x43, 0x01, 0x01]))
                except EW41Error as exc:
                    errors.append(f"메인 조절기 난방 복귀 명령 전송 실패: {exc}")
            return self._verify(mode.label, lambda status: status.mode == mode,
                                errors, warnings, response)

    def set_hotwater_only(self) -> ControlResult:
        return self._set_mode(OperatingMode.HOTWATER_ONLY, 0x01)

    def set_heating_hotwater_mode(self) -> ControlResult:
        return self._set_mode(OperatingMode.HEATING_HOTWATER, 0x00)

    def set_living_temperature(self, temperature: float) -> ControlResult:
        """Backward-compatible shortcut for the living room."""
        return self.set_temperature(Room.LIVING, temperature)

    def set_temperature(self, room: Room | int, temperature: float) -> ControlResult:
        """Use the per-room addresses verified on the actual EW41 device."""
        room = _validate_room(room)
        encoded = encode_temperature(temperature)
        with self._lock:
            errors: list[str] = []
            packet = make_packet([0xF7, 0x36, ROOM_ADDRESSES[room], 0x44, 0x01, encoded])
            try:
                self._send(packet)
            except EW41Error as exc:
                errors.append(f"{room.label} 온도 명령 전송 실패: {exc}")
            return self._verify(f"{room.label} 설정온도 {temperature:g}℃",
                                lambda status: status.raw[ROOM_TEMPERATURE_INDICES[room]] == encoded, errors)
