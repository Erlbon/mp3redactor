"""Deterministic synthetic MPEG files for tests/test_mp3_validator.py.

The expected lines and fixed-file hashes in that test were taken from
mp3val.exe 0.1.8 run on exactly these bytes (2026-11-26)."""

import struct


def frame(index: int, br_index: int = 9, crc: bool | None = None, mono: bool = False) -> bytes:
    """One MPEG-1 Layer III 44.1 kHz frame; payload is a fixed pattern
    without 0xFF bytes. crc: None = unprotected, True = right CRC,
    False = wrong CRC."""
    from core.mp3_validator import calculate_crc16

    bitrate = (-1, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, -1)[br_index]
    size = 144 * bitrate * 1000 // 44100
    b1 = 0xFB if crc is None else 0xFA
    header = bytes([0xFF, b1, br_index << 4, 0xC4 if mono else 0x44])
    payload = bytearray((index * 31 + k * 7) % 250 for k in range(size - 4))
    data = bytearray(header) + payload
    if crc is not None:
        side = 17 if mono else 32
        value = calculate_crc16(0xFFFF, 0x8005, data, 2, 2)
        value = calculate_crc16(value, 0x8005, data, 6, side)
        if crc is False:
            value ^= 0x0101
        data[4], data[5] = value >> 8, value & 0xFF
    return bytes(data)


def frames(count: int, start: int = 0, **kw) -> bytes:
    return b"".join(frame(start + i, **kw) for i in range(count))


def id3v2(size: int = 100) -> bytes:
    syncsafe = bytes([(size >> 21) & 0x7F, (size >> 14) & 0x7F, (size >> 7) & 0x7F, size & 0x7F])
    return b"ID3\x04\x00\x00" + syncsafe + b"TIT2\x00\x00\x00\x03\x00\x00\x00ab".ljust(size, b"\x00")


def id3v1() -> bytes:
    return b"TAG" + b"Title".ljust(125, b"\x00")


def xing_frame(total_frames: int, total_bytes: int) -> bytes:
    data = bytearray(frame(999))
    data[36:52] = b"Xing" + struct.pack(">III", 3, total_frames, total_bytes)
    return bytes(data)


def samples() -> dict[str, bytes]:
    clean = id3v2() + frames(40) + id3v1()
    vbr_body = b"".join(frame(i, br_index=(9, 11, 5)[i % 3]) for i in range(30))
    return {
        "clean": clean,
        "garbage_mid": id3v2() + frames(20) + bytes(500) + frames(20, start=20) + id3v1(),
        "truncated": id3v2() + frames(30)[:-150],
        "junk_start": bytes(range(1, 200)) + frames(30),
        "crc_wrong": id3v2() + b"".join(frame(i, crc=(i % 5 != 2)) for i in range(30)),
        "xing_wrong": id3v2() + xing_frame(99, 1234) + vbr_body,
        "xing_right": xing_frame(31, len(xing_frame(0, 0)) + len(vbr_body)) + vbr_body + id3v1(),
        "trailing_bytes": frames(30) + b"\x01\x02",
        "few_frames_no_tags": frames(5),
        "unknown": bytes(range(1, 250)) * 4,
    }
