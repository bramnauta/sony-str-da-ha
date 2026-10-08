"""Sony STR-DA ES serial protocol (RS-232C wrapped in TCP, port 6001).

Frame: STX(02) BC PDC CMD data... CS
  BC = number of bytes after BC up to and including the last data byte
  CS = two's complement of the sum of BC..last data byte
Short commands are answered with a single byte: ACK (FD) or NACK (FE).
Queries are answered with a frame whose PDC is A8 (AB for the sound mode).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

STX = 0x02


class Reply(Enum):
    """Single byte answer to a command."""

    ACK = 0xFD
    NACK = 0xFE


@dataclass(frozen=True)
class Frame:
    """A complete, checksum-verified frame."""

    pdc: int
    data: bytes


PDC_COMMAND = 0xA0
PDC_SOUND = 0xA3
PDC_REPLY = 0xA8
PDC_SOUND_REPLY = 0xAB

ZONE_MAIN = 0x00


def build_frame(payload: bytes) -> bytes:
    """Wrap PDC + CMD + data in STX, byte count and checksum."""
    body = bytes([len(payload)]) + payload
    return bytes([STX]) + body + bytes([(-sum(body)) & 0xFF])


class FrameParser:
    """Splits a TCP byte stream into ACK/NACK bytes and verified frames.

    There is no end marker and 0x02 may occur inside data, so a frame is only
    accepted once BC bytes plus a valid checksum have arrived.
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    @property
    def has_partial(self) -> bool:
        return bool(self._buf)

    def reset(self) -> None:
        self._buf.clear()

    def feed(self, data: bytes) -> list[Reply | Frame]:
        self._buf.extend(data)
        out: list[Reply | Frame] = []
        buf = self._buf
        while buf:
            first = buf[0]
            if first == Reply.ACK.value:
                out.append(Reply.ACK)
                del buf[0]
            elif first == Reply.NACK.value:
                out.append(Reply.NACK)
                del buf[0]
            elif first == STX:
                if len(buf) < 2:
                    break
                count = buf[1]
                if count < 2:
                    del buf[0]
                    continue
                total = 2 + count + 1
                if len(buf) < total:
                    break
                if sum(buf[1:total]) & 0xFF == 0:
                    out.append(Frame(pdc=buf[2], data=bytes(buf[3 : total - 1])))
                    del buf[:total]
                else:
                    del buf[0]
            else:
                del buf[0]
        return out


# --- commands (PDC, CMD, zone, value) -------------------------------------


def cmd_power(on: bool, zone: int = ZONE_MAIN) -> bytes:
    return bytes([PDC_COMMAND, 0x60, zone, 1 if on else 0])


def cmd_mute(on: bool, zone: int = ZONE_MAIN) -> bytes:
    return bytes([PDC_COMMAND, 0x53, zone, 1 if on else 0])


def cmd_volume_step(up: bool, zone: int = ZONE_MAIN) -> bytes:
    """One step is 0.5 dB."""
    return bytes([PDC_COMMAND, 0x52, zone, 0x02]) + (b"\x00\x01" if up else b"\xff\xff")


def cmd_input(code: int, zone: int = ZONE_MAIN) -> bytes:
    return bytes([PDC_COMMAND, 0x42, zone, code])


def cmd_sound_mode(code: int) -> bytes:
    return bytes([PDC_SOUND, 0x42, code])


# --- queries and the reply (pdc, cmd) each one expects ----------------------

Q_MODEL = bytes([PDC_COMMAND, 0xA0, ZONE_MAIN])
Q_INPUT = bytes([PDC_COMMAND, 0x82, ZONE_MAIN])
Q_VOLUME = bytes([PDC_COMMAND, 0x92, ZONE_MAIN, 0x01])
Q_SOUND_MODE = bytes([PDC_SOUND, 0x82])

REPLY_MODEL = (PDC_REPLY, 0xA0)
REPLY_INPUT = (PDC_REPLY, 0x82)
REPLY_VOLUME = (PDC_REPLY, 0x92)
REPLY_SOUND_MODE = (PDC_SOUND_REPLY, 0x82)


def reply_key(frame: Frame) -> tuple[int, int] | None:
    return (frame.pdc, frame.data[0]) if frame.data else None


def decode_frame(frame: Frame) -> dict[str, Any]:
    """Turn a status frame into ReceiverState field updates (may be empty)."""
    key = reply_key(frame)
    data = frame.data
    if key == REPLY_INPUT and len(data) >= 5 and data[1] == ZONE_MAIN:
        # A8 82 zone input ?? flags ??; flags bit 1 = muted. Only seen on a
        # receiver that is on, so an answer also means "powered on".
        return {"power": True, "input_code": data[2], "muted": bool(data[4] & 0x02)}
    if key == REPLY_VOLUME and len(data) >= 5 and data[1] == ZONE_MAIN:
        high = data[3] - 256 if data[3] >= 128 else data[3]
        return {"volume_db": high + data[4] / 256}
    if key == REPLY_SOUND_MODE and len(data) >= 2:
        return {"sound_mode": data[1]}
    if key == REPLY_MODEL and len(data) >= 2:
        return {"model": data[1:].decode("ascii", "ignore").strip()}
    return {}


# --- names -------------------------------------------------------------------

INPUT_NAMES: dict[int, str] = {
    0x00: "Tuner",
    0x01: "Phono",
    0x02: "CD",
    0x03: "DAT",
    0x04: "MD",
    0x05: "Tape 1",
    0x06: "Tape 2",
    0x07: "Digital 1",
    0x08: "Digital 2",
    0x09: "Digital 3",
    0x0A: "Aux 1",
    0x0B: "Aux 2",
    0x0C: "MD-WM",
    0x0D: "MD2",
    0x0E: "MS",
    0x0F: "Source",
    0x10: "Video 1",
    0x11: "Video 2",
    0x12: "Video 3",
    0x13: "Video 4",
    0x14: "Video 5",
    0x15: "LD",
    0x16: "TV",
    0x17: "DBS",
    0x18: "VCD",
    0x19: "DVD",
    0x20: "5.1 Input",
}

# The inputs that make sense on a current receiver; others (and the HDMI
# inputs, which Sony's table leaves out) can be added in the options.
DEFAULT_SOURCE_CODES: tuple[int, ...] = (
    0x00, 0x01, 0x02, 0x07, 0x08, 0x09, 0x0A, 0x0B,
    0x10, 0x11, 0x12, 0x13, 0x14, 0x16, 0x17, 0x19, 0x20,
)  # fmt: skip

SOUND_MODES: dict[int, str] = {
    0x00: "2ch Stereo",
    0x01: "Auto Format Decode",
    0x02: "Analog Direct",
    0x03: "Normal Surround",
    0x04: "Cinema Studio EX A",
    0x05: "Cinema Studio EX B",
    0x06: "Cinema Studio EX C",
    0x07: "Semi-Cinema Studio EX A",
    0x08: "Semi-Cinema Studio EX B",
    0x09: "Semi-Cinema Studio EX C",
    0x0A: "Night Theater",
    0x0B: "Mono Movie",
    0x0C: "Stereo Movie",
    0x0D: "Headphone Theater",
    0x0E: "Virtual Multi Dimension",
    0x0F: "Virtual Multi Rear",
    0x10: "Virtual Semi Multi Dimension",
    0x11: "Virtual Enhanced Surround A",
    0x12: "Virtual Enhanced Surround B",
    0x13: "Small Hall",
    0x14: "Large Hall",
    0x15: "Opera House",
    0x16: "Jazz Club",
    0x17: "Disco / Club",
    0x18: "Church",
    0x19: "Live House",
    0x1A: "Arena",
    0x1B: "Stadium",
    0x1C: "Game",
    0x1D: "Virtual Matrix 6.1ch",
    0x1E: "Concert Hall A",
    0x1F: "Concert Hall B",
    0x20: "Sports",
    0x21: "AFD Auto",
    0x22: "AFD Pro Logic",
    0x23: "AFD PL2 Movie",
    0x24: "AFD PL2 Music",
    0x25: "AFD Neo:6 Cinema",
    0x26: "AFD Neo:6 Music",
    0x27: "AFD Multi Stereo",
    0x28: "Multi Ch In 1 Direct",
    0x29: "Multi Ch In 2 Direct",
    0x2A: "AFD PLII Game",
    0x2B: "AFD PLIIx Movie",
    0x2C: "AFD PLIIx Music",
    0x2D: "AFD PLIIx Game",
    0x7F: "Special (PCM 96k)",
    0x80: "Headphone 2ch",
    0x82: "Headphone Direct",
    0x83: "Headphone Theater (1)",
    0x84: "Headphone Multi 1",
    0x85: "Headphone Multi 2",
}


def parse_sources(text: str) -> dict[int, str]:
    """Parse "Name=HEX" lines (e.g. "HDMI 1=1A") into {code: name}."""
    result: dict[int, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        name, sep, code = line.rpartition("=")
        name = name.strip()
        if not sep or not name:
            raise ValueError(line)
        try:
            value = int(code.strip().removeprefix("0x").removeprefix("0X"), 16)
        except ValueError as err:
            raise ValueError(line) from err
        if not 0 <= value <= 0xFF:
            raise ValueError(line)
        result[value] = name
    return result
