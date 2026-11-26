"""
MP3 integrity check and fix in-process: a Python port of mp3val 0.1.8
(https://mp3val.sourceforge.net, GPL-2.0-or-later, Copyright 2005-2009
Alexey Kuznetsov (ring0) and Eugen Tikhonov (jetsys)), the external tool
this replaces. core.mp3val_runner still runs mp3val.exe as a fallback
while the port is checked against it; see tools/compare_mp3val.py.

The port follows the C++ (mpegparse.cpp, report.cpp, out.cpp, crc.cpp,
main.cpp) function by function, quirks included, so that it prints the
same WARNING/ERROR lines and writes a byte-identical fixed file. Some of
those quirks, kept on purpose:

- In a check (no fix) the "last frame was MPEG" flag is only ever set in
  fix mode, so the check never subtracts a dropped frame; the fix does.
- ValidateMPEGFrame() counts the frame's version/layer and CRC flag
  before it rejects a bad bitrate or sample rate, so rejected headers
  still show up in the counts.
- A resync after an error starts one byte after the last good frame,
  not at the error.
- Before the final truncation step the fix steps back over whatever it
  wrote last, even an ID3v2 tag.
- 32-bit C int arithmetic (Xing counts, APE sizes) is reproduced.

Reads past the end of the file read zeros, as they do in mp3val's
memory-mapped view (the rest of the last page is zero-filled).

The file is read with Python's own open(), so any file name works,
unlike mp3val.exe, which cannot open a path with letters outside the
Windows ANSI code page.
"""

from __future__ import annotations

import os
import struct
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

# mpegparse.cpp bitrate tables (kbit/s), copied as they are.
MPEG1_LAYER1_BITRATES = (-1, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448, -1)
MPEG1_LAYER2_BITRATES = (-1, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384, -1)
MPEG1_LAYER3_BITRATES = (-1, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, -1)
MPEG2_LAYER1_BITRATES = (-1, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256, -1)
MPEG2_LAYERS23_BITRATES = (-1, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, -1)

# (version, layer) for header bits 19-17 ((byte1 >> 1) & 0x0F), as in ValidateMPEGFrame().
_VERSION_LAYER = {
    0x0F: (1, 1), 0x0E: (1, 2), 0x0D: (1, 3),
    0x0B: (2, 1), 0x0A: (2, 2), 0x09: (2, 3),
    0x03: (25, 1), 0x02: (25, 2), 0x01: (25, 3),
}
_COUNTER = {
    (1, 1): "mpeg1layer1", (1, 2): "mpeg1layer2", (1, 3): "mpeg1layer3",
    (2, 1): "mpeg2layer1", (2, 2): "mpeg2layer2", (2, 3): "mpeg2layer3",
    (25, 1): "mpeg25layer1", (25, 2): "mpeg25layer2", (25, 3): "mpeg25layer3",
}

# Reads past the end of the data read zeros (mp3val's mapped view); the
# farthest such read is the Xing/VBRI parse, at most 53 bytes past a
# frame start.
_PAD = 64


class ValidatorAbort(RuntimeError):
    """A file mp3val.exe cannot finish: it hangs or crashes on it. The
    port stops and says so instead (the app reports TOOL ERROR, as it
    did for the exe's timeout or crash)."""


class ValidatorLoopError(ValidatorAbort):
    """The file sends mp3val's parser round in circles (for example an
    APEv2 tag whose size field is -32): mp3val.exe hangs on such a file."""


class ValidatorCrashError(ValidatorAbort):
    """A size field in the file points before its start: mp3val.exe reads
    outside its memory there and crashes (access violation)."""


def _int32(value: int) -> int:
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value & 0x80000000 else value


class MpegInfo:
    """struct MPEGINFO (mpegparse.h) with clear()."""

    def __init__(self) -> None:
        self.clear()

    def clear(self) -> None:
        self.mpeg1layer1 = self.mpeg1layer2 = self.mpeg1layer3 = 0
        self.mpeg2layer1 = self.mpeg2layer2 = self.mpeg2layer3 = 0
        self.mpeg25layer1 = self.mpeg25layer2 = self.mpeg25layer3 = 0
        self.id3v1 = 0
        self.id3v2 = 0
        self.apev2 = 0

        self.VBRHeaderPresent = False
        self.IsXingHeader = True
        self.BytesPresent = False
        self.iBytes = -1
        self.FramesPresent = False
        self.iFrames = -1
        self.iFirstMPEGFrameSize = 0  # not cleared in C; set before it is read

        self.riff = -1
        self.unknown_format = -1
        self.mpeg_stream_error = -1
        self.truncated = -1
        self.garbage_at_the_begin = -1
        self.garbage_at_the_end = -1

        self.LastFrameStereo = False
        self.bLastFrameCRC = False
        self.bCRC = False
        self.bCRCError = False
        self.bVariableBitrate = False
        self.iLastBitrate = -2
        self.iLastMPEGLayer = 0
        self.iLastMPEGVersion = 0
        self.iCRCErrors = 0

        self.iTotalMPEGBytes = 0
        self.iErrors = 0
        self.iDeletedFrames = 0

    def mpeg_total(self) -> int:
        return (
            self.mpeg1layer1 + self.mpeg1layer2 + self.mpeg1layer3
            + self.mpeg2layer1 + self.mpeg2layer2 + self.mpeg2layer3
            + self.mpeg25layer1 + self.mpeg25layer2 + self.mpeg25layer3
        )


class _OutFile:
    """The temporary output file of a fix (a Windows file handle in
    mp3val) together with out.cpp's WriteToFile()/GetLastFrameSize() and
    their static iPrevSize. Kept in memory: pos is the file pointer."""

    def __init__(self) -> None:
        self.buf = bytearray()
        self.pos = 0
        self.prev_size = 0  # out.cpp: static int iPrevSize

    def tell(self) -> int:  # CrossAPI_SetFilePointer(h, 0, true)
        return self.pos

    def seek(self, pointer: int) -> None:  # CrossAPI_SetFilePointer(h, p, false)
        if pointer >= 0:
            self.pos = pointer

    def _put(self, data: bytes) -> None:
        end = self.pos + len(data)
        if self.pos > len(self.buf):
            self.buf.extend(bytes(self.pos - len(self.buf)))
        self.buf[self.pos:end] = data
        self.pos = end

    def write(self, src, index: int, nbytes: int, file_size: int) -> int:
        """WriteToFile(hFile, baseptr, index, bytes, iFileSize)."""
        if nbytes < 0:
            if self.prev_size:
                new_pos = self.pos - self.prev_size
                if new_pos >= 0:
                    self.pos = new_pos
            tmp = self.prev_size
            self.prev_size = 0
            return tmp
        if file_size >= 0 and index + nbytes > file_size:
            nbytes = file_size - index
        if nbytes > 0:
            self._put(bytes(src[index:index + nbytes]))
        self.prev_size = nbytes
        return nbytes

    def set_end_of_file(self) -> None:
        if self.pos > len(self.buf):
            self.buf.extend(bytes(self.pos - len(self.buf)))
        del self.buf[self.pos:]


def calculate_crc16(init: int, polynom: int, buf, start: int, count: int) -> int:
    """crc.cpp CalculateCRC16(): byte at a time, MSB first."""
    crc = init & 0xFFFF
    for i in range(start, start + count):
        crc <<= 8
        crc ^= (buf[i] & 0xFF) << 16
        for j in range(8):
            if (crc << j) & 0x00800000:
                crc ^= polynom << (7 - j)
        crc &= 0xFFFF
    return crc


def validate_mpeg_frame(d, index: int, info: MpegInfo) -> int:
    """ValidateMPEGFrame(): the frame size, or -1 for a bad header."""
    b1 = d[index + 1]
    if b1 & 0x01:
        info.bLastFrameCRC = False
    else:
        info.bLastFrameCRC = True
        info.bCRC = True

    version_layer = _VERSION_LAYER.get((b1 >> 1) & 0x0F)
    if version_layer is None:
        info.mpeg_stream_error = index
        return -1
    counter = _COUNTER[version_layer]
    setattr(info, counter, getattr(info, counter) + 1)
    mpeg_version, mpeg_layer = version_layer

    b2 = d[index + 2]
    bitrate_index = (b2 >> 4) & 0x0F
    if mpeg_version == 1:
        if mpeg_layer == 1:
            mpeg_bitrate = MPEG1_LAYER1_BITRATES[bitrate_index]
        elif mpeg_layer == 2:
            mpeg_bitrate = MPEG1_LAYER2_BITRATES[bitrate_index]
        else:
            mpeg_bitrate = MPEG1_LAYER3_BITRATES[bitrate_index]
    else:
        if mpeg_layer == 1:
            mpeg_bitrate = MPEG2_LAYER1_BITRATES[bitrate_index]
        else:
            mpeg_bitrate = MPEG2_LAYERS23_BITRATES[bitrate_index]

    if mpeg_bitrate == -1:
        info.mpeg_stream_error = index
        return -1

    if info.iLastBitrate > 0 and info.iLastBitrate != mpeg_bitrate:
        info.bVariableBitrate = True
    info.iLastBitrate = mpeg_bitrate

    sr_bits = (b2 >> 2) & 0x03
    if sr_bits == 0x00:
        sampling_rate = 44100 if mpeg_version == 1 else 22050 if mpeg_version == 2 else 11025
    elif sr_bits == 0x01:
        sampling_rate = 48000 if mpeg_version == 1 else 24000 if mpeg_version == 2 else 12000
    elif sr_bits == 0x02:
        sampling_rate = 32000 if mpeg_version == 1 else 16000 if mpeg_version == 2 else 8000
    else:
        info.mpeg_stream_error = index
        return -1

    padding = 1 if (b2 >> 1) & 0x01 else 0

    info.LastFrameStereo = (d[index + 3] & 0xC0) != 0xC0
    info.iLastMPEGVersion = mpeg_version
    info.iLastMPEGLayer = mpeg_layer

    if mpeg_layer == 1:
        frame_size = (12 * mpeg_bitrate * 1000 // sampling_rate + padding) * 4
    elif mpeg_layer == 2:
        frame_size = 144 * mpeg_bitrate * 1000 // sampling_rate + padding
    elif mpeg_layer == 3 and mpeg_version == 1:
        frame_size = 144 * mpeg_bitrate * 1000 // sampling_rate + padding
    else:
        frame_size = 72 * mpeg_bitrate * 1000 // sampling_rate + padding

    info.iTotalMPEGBytes += frame_size
    return frame_size


def _side_info_size(info: MpegInfo) -> int:
    if info.LastFrameStereo:
        return 32 if info.iLastMPEGVersion == 1 else 17
    return 17 if info.iLastMPEGVersion == 1 else 9


def check_mp3_crc(d, index: int, info: MpegInfo, fix: bool) -> None:
    """CheckMP3CRC(): layer III CRC over header bytes 2-3 and the side
    info; a fix writes the right CRC into the frame."""
    crc = calculate_crc16(0xFFFF, 0x8005, d, index + 2, 2)
    crc = calculate_crc16(crc, 0x8005, d, index + 6, _side_info_size(info))
    stored = (d[index + 4] << 8) | d[index + 5]
    if stored != crc:
        info.bCRCError = True
        info.iCRCErrors += 1
        if fix:
            d[index + 4] = (crc >> 8) & 0xFF
            d[index + 5] = crc & 0xFF


def validate_id3v2_tag(d, index: int, info: MpegInfo) -> int:
    """ValidateID3v2Tag(): the tag's whole size (header, footer)."""
    info.id3v2 += 1
    size = d[index + 9] + 128 * d[index + 8] + 16384 * d[index + 7] + 2097152 * d[index + 6]
    if d[index + 5] & 0x10:
        return size + 20
    return size + 10


def validate_apev2_tag(d, index: int, info: MpegInfo) -> int:
    """ValidateAPEv2Tag(): signed 32-bit little-endian size at +12, plus 32."""
    info.apev2 += 1
    size = struct.unpack_from("<i", d, index + 12)[0]
    return _int32(size + 32)


def mpeg_resync(d, index: int, file_size: int, frames: int) -> int:
    """MPEGResync(): the first offset from index where `frames` valid
    frame headers of one version and layer follow each other, or -1."""
    p = index
    sync_frames = 0
    tmp = MpegInfo()
    mpeg_version = mpeg_layer = 0
    while True:
        if file_size - p - 3 <= 0:
            return -1
        found = d.find(b"\xff", p, p + (file_size - p - 3))
        if found == -1:
            break
        p = found
        if (d[p + 1] & 0xE0) != 0xE0:
            p += 1
            sync_frames = 0
            mpeg_version = mpeg_layer = 0
            continue
        frame_size = validate_mpeg_frame(d, p, tmp)
        if frame_size == -1:
            p += 1
            sync_frames = 0
            mpeg_version = mpeg_layer = 0
            continue
        new_frame = frame_size + p
        sync_frames += 1
        mpeg_version = tmp.iLastMPEGVersion
        mpeg_layer = tmp.iLastMPEGLayer
        while sync_frames < frames:
            if new_frame + 4 > file_size:
                sync_frames = 0
                mpeg_version = mpeg_layer = 0
                p += 1
                break
            frame_size = validate_mpeg_frame(d, new_frame, tmp)
            if frame_size == -1:
                sync_frames = 0
                mpeg_version = mpeg_layer = 0
                p += 1
                break
            new_frame += frame_size
            sync_frames += 1
            if mpeg_version and mpeg_version != tmp.iLastMPEGVersion:
                sync_frames = 0
                mpeg_version = mpeg_layer = 0
                p += 1
                break
            if mpeg_layer and mpeg_layer != tmp.iLastMPEGLayer:
                sync_frames = 0
                mpeg_version = mpeg_layer = 0
                p += 1
                break
        if sync_frames >= frames:
            break
    if sync_frames >= frames:
        return p
    return -1


def _be32(d, i: int) -> int:
    return _int32(16777216 * d[i] + 65536 * d[i + 1] + 256 * d[i + 2] + d[i + 3])


def parse_xing_header(d, index: int, info: MpegInfo) -> None:
    """ParseXingHeader()."""
    tag = bytes(d[index:index + 4])
    if tag != b"Xing" and tag != b"Info":
        return
    info.VBRHeaderPresent = True
    info.IsXingHeader = True
    flags = d[index + 7] & 0x03
    if flags == 0x01:
        info.iFrames = _be32(d, index + 8)
        info.FramesPresent = True
        info.BytesPresent = False
    elif flags == 0x02:
        info.iBytes = _be32(d, index + 8)
        info.FramesPresent = False
        info.BytesPresent = True
    elif flags == 0x03:
        info.iFrames = _be32(d, index + 8)
        info.iBytes = _be32(d, index + 12)
        info.FramesPresent = True
        info.BytesPresent = True


def parse_vbri_header(d, index: int, info: MpegInfo) -> None:
    """ParseVBRIHeader()."""
    if bytes(d[index:index + 4]) != b"VBRI":
        return
    info.VBRHeaderPresent = True
    info.IsXingHeader = False
    info.iBytes = _be32(d, index + 10)
    info.iFrames = _be32(d, index + 14)
    info.FramesPresent = True
    info.BytesPresent = True


def parse_riff_header(d, index: int, file_size: int) -> tuple[int, int]:
    """ParseRIFFHeader(): (offset of the WAVE data chunk's audio or -1,
    new file size = end of that chunk)."""
    if index + 11 > file_size:
        return -1, file_size
    if bytes(d[index:index + 4]) != b"RIFF":
        return -1, file_size
    if bytes(d[index + 8:index + 12]) != b"WAVE":
        return -1, file_size
    index += 12
    guard = 0
    while True:
        guard += 1
        if guard > file_size + 16:
            raise ValidatorLoopError("RIFF chunk sizes loop")
        if index + 7 > file_size:
            return -1, file_size
        length = struct.unpack_from("<i", d, index + 4)[0]
        if bytes(d[index:index + 4]) != b"data":
            index = _int32(index + length + 8)
            if index < 0:
                raise ValidatorCrashError("a RIFF chunk size points before the start of the file")
            continue
        new_size = _int32(index + 8 + length)
        if new_size > file_size:
            new_size = file_size
        return index + 8, new_size


@dataclass
class _Messages:
    """Where ValidateFile()/PrintReport() print: a list of lines, or
    None (the fix pass prints nothing)."""

    lines: list[str] | None
    filename: str

    def message(self, caption: str, message: str, error_frame: int) -> None:
        """report.cpp PrintMessage()."""
        if self.lines is None:
            return
        if error_frame >= 0:
            self.lines.append(f'{caption}: "{self.filename}" (offset 0x{error_frame:x}): {message}')
        else:
            self.lines.append(f'{caption}: "{self.filename}": {message}')


def validate_file(d: bytearray, file_size: int, info: MpegInfo, out: _Messages,
                  fix: bool, hfile: _OutFile | None) -> int:
    """ValidateFile(). d is the file followed by _PAD zero bytes;
    file_size is the real length."""
    frame = 0
    frame_size = 0
    last_mpeg_frame = 0
    was_first_frame = False
    xing_offset = 0
    id3v1_offset = 0
    first_mpeg_frame_offset = 0
    last_frame_was_mpeg = False

    info.clear()

    if file_size >= 128 and bytes(d[file_size - 128:file_size - 125]) == b"TAG":
        info.id3v1 = 1
        file_size -= 128
        id3v1_offset = file_size

    if file_size >= 4 and bytes(d[frame:frame + 3]) == b"ID3":
        if frame + 10 > file_size:
            info.truncated = frame
        else:
            frame += validate_id3v2_tag(d, frame, info)
            if fix:
                hfile.write(d, 0, frame, file_size)
                last_frame_was_mpeg = False

    # mp3val.exe would hang where this guard trips; nothing a real file
    # needs comes near it (each pass moves forward at least a few bytes).
    guard = 0
    guard_limit = 4 * (file_size + 1024)

    while frame != file_size:
        guard += 1
        if guard > guard_limit:
            raise ValidatorLoopError("the frame walk does not move forward")
        if frame < 0:
            raise ValidatorCrashError("an APEv2 tag size points before the start of the file")
        if frame + 4 > file_size:
            info.truncated = frame
            break
        if bytes(d[frame:frame + 4]) == b"RIFF":
            if not was_first_frame:
                info.riff = frame
                new_frame, file_size = parse_riff_header(d, frame, file_size)
                if new_frame != -1:
                    frame = new_frame
                    continue

        if d[frame] == 0xFF and (d[frame + 1] & 0xE0) == 0xE0:
            frame_size = validate_mpeg_frame(d, frame, info)
            if frame_size != -1:
                if frame_size + frame <= file_size and info.iLastMPEGLayer == 3 and info.bLastFrameCRC:
                    check_mp3_crc(d, frame, info, fix)
                if fix and not was_first_frame:
                    first_mpeg_frame_offset = hfile.tell()
                if fix:
                    hfile.write(d, frame, frame_size, file_size)
                    last_frame_was_mpeg = True
                if not was_first_frame:
                    was_first_frame = True
                    info.iFirstMPEGFrameSize = frame_size
                    if info.iLastMPEGVersion == 1:
                        xing_offset = 32 if info.LastFrameStereo else 17
                    else:
                        xing_offset = 17 if info.LastFrameStereo else 9
                    parse_xing_header(d, frame + 4 + xing_offset, info)
                    if not info.VBRHeaderPresent:
                        parse_vbri_header(d, frame + 4 + 32, info)
                last_mpeg_frame = frame
                frame += frame_size
                continue

        if bytes(d[frame:frame + 4]) == b"APET":
            if frame + 16 > file_size:
                info.truncated = frame
                break
            frame_size = validate_apev2_tag(d, frame, info)
            if fix:
                hfile.write(d, frame, frame_size, file_size)
                last_frame_was_mpeg = False
            frame = _int32(frame + frame_size)
            continue

        if fix:
            if last_frame_was_mpeg:
                info.iDeletedFrames += 1
                info.iTotalMPEGBytes -= hfile.write(None, 0, -1, -1)
        elif last_frame_was_mpeg:  # never true in a check: the flag is only set when fixing
            info.iDeletedFrames += 1
            info.iTotalMPEGBytes -= hfile.prev_size if hfile is not None else 0

        if not frame:
            new_frame = mpeg_resync(d, frame, file_size, 8)
            if new_frame == -1:
                info.unknown_format = 0
                break
            info.garbage_at_the_begin = 0
            if not fix:
                out.message("WARNING", "Garbage at the beginning of the file", info.garbage_at_the_begin)
            info.iErrors += 1
            frame = new_frame
        else:
            new_frame = mpeg_resync(d, (last_mpeg_frame + 1) if last_mpeg_frame else frame, file_size, 6)
            if new_frame == -1:
                info.garbage_at_the_end = frame
                if not fix:
                    out.message("WARNING", "Garbage at the end of the file", info.garbage_at_the_end)
                info.iErrors += 1
                break
            info.mpeg_stream_error = frame
            if not fix:
                out.message("WARNING", "MPEG stream error, resynchronized successfully", info.mpeg_stream_error)
            info.iErrors += 1
            frame = new_frame

    mpeg_total = info.mpeg_total()

    if info.truncated >= 0:
        if fix:
            hfile.write(None, 0, -1, -1)
            if last_frame_was_mpeg:
                info.iTotalMPEGBytes -= frame_size
                info.iDeletedFrames += 1
        elif last_frame_was_mpeg:
            info.iTotalMPEGBytes -= frame_size
            info.iDeletedFrames += 1

    if fix and info.id3v1:
        hfile.write(d, id3v1_offset, 128, -1)

    if fix:
        hfile.set_end_of_file()

    if fix and info.VBRHeaderPresent:
        frames_value = struct.pack(">I", (mpeg_total - info.iDeletedFrames) & 0xFFFFFFFF)
        bytes_value = struct.pack(">I", info.iTotalMPEGBytes & 0xFFFFFFFF)
        if info.IsXingHeader:
            if info.BytesPresent and info.FramesPresent:
                hfile.seek(first_mpeg_frame_offset + xing_offset + 12)
                hfile.write(frames_value, 0, 4, -1)
                hfile.write(bytes_value, 0, 4, -1)
            elif info.BytesPresent:
                hfile.seek(first_mpeg_frame_offset + xing_offset + 12)
                hfile.write(bytes_value, 0, 4, -1)
            elif info.FramesPresent:
                hfile.seek(first_mpeg_frame_offset + xing_offset + 12)
                hfile.write(frames_value, 0, 4, -1)
        else:
            hfile.seek(first_mpeg_frame_offset + 46)
            hfile.write(bytes_value, 0, 4, -1)
            hfile.write(frames_value, 0, 4, -1)

    if fix:
        info.iErrors = 1
    return 0


def print_report(out: _Messages, info: MpegInfo) -> None:
    """report.cpp PrintReport(), INFO line included."""
    mpeg_total = info.mpeg_total()

    if info.unknown_format >= 0:
        out.message("ERROR", "Unknown file format", -1)
        return

    if mpeg_total < 10:
        out.message("WARNING", "Too few MPEG frames (it's unlikely that this is a MPEG audio file)", -1)

    if info.riff >= 0:
        out.message("WARNING", "This is a RIFF file, not MPEG stream", -1)
        info.iErrors += 1

    if info.truncated >= 0:
        out.message("WARNING", "It seems that file is truncated or there is garbage at the end of the file",
                    info.truncated)
        info.iErrors += 1

    if info.bCRCError:
        out.message("WARNING", f"Wrong CRC in {info.iCRCErrors} frames", -1)
        info.iErrors += 1

    if info.VBRHeaderPresent:
        if info.IsXingHeader:
            # LAME/Foobar2000 count the frames with or without the Xing
            # frame itself, and the bytes with or without it: both pass.
            if info.FramesPresent and info.iFrames != mpeg_total and info.iFrames + 1 != mpeg_total:
                out.message("WARNING", f"Wrong number of MPEG frames specified in Xing header "
                                       f"({info.iFrames} instead of {mpeg_total})", -1)
                info.iErrors += 1
            if (info.BytesPresent and info.iBytes != info.iTotalMPEGBytes
                    and info.iBytes != info.iTotalMPEGBytes - info.iFirstMPEGFrameSize):
                out.message("WARNING", f"Wrong number of MPEG data bytes specified in Xing header "
                                       f"({info.iBytes} instead of {info.iTotalMPEGBytes})", -1)
                info.iErrors += 1
        else:
            if info.FramesPresent and info.iFrames != mpeg_total:
                out.message("WARNING", f"Wrong number of MPEG frames specified in VBRI header "
                                       f"({info.iFrames} instead of {mpeg_total})", -1)
                info.iErrors += 1
            if info.BytesPresent and info.iBytes != info.iTotalMPEGBytes:
                out.message("WARNING", f"Wrong number of MPEG data bytes specified in VBRI header "
                                       f"({info.iBytes} instead of {info.iTotalMPEGBytes})", -1)
                info.iErrors += 1

    if info.bVariableBitrate and not info.VBRHeaderPresent:
        out.message("WARNING", "VBR detected, but no VBR header is present. Seeking may not work properly.", -1)

    if (info.mpeg1layer1 or info.mpeg1layer2 or info.mpeg2layer1 or info.mpeg2layer2
            or info.mpeg25layer1 or info.mpeg25layer2):
        out.message("WARNING", "Non-layer-III frame encountered. See related INFO message for details.", -1)
        info.iErrors += 1

    counts = (info.mpeg1layer1, info.mpeg1layer2, info.mpeg1layer3,
              info.mpeg2layer1, info.mpeg2layer2, info.mpeg2layer3,
              info.mpeg25layer1, info.mpeg25layer2, info.mpeg25layer3)
    frame_types = sum(1 for c in counts if c)

    if frame_types > 1:
        out.message("WARNING", "Different MPEG versions or layers in one file. See related INFO message for details.",
                    -1)

    if info.apev2 > 1:
        out.message("WARNING", "Several APEv2 tags in one file. See related INFO message for details.", -1)
        info.iErrors += 1

    if not info.id3v1 and not info.id3v2 and not info.apev2:
        out.message("WARNING", "No supported tags in the file", -1)

    if out.lines is None:
        return
    tags_total = info.id3v1 + info.id3v2 + info.apev2
    line = f'INFO: "{out.filename}": '
    if mpeg_total:
        line += f"{mpeg_total} MPEG frames ("
        if frame_types > 1:
            names = ("V1L1", "V1L2", "V1L3", "V2L1", "V2L2", "V2L3", "V2.5L1", "V2.5L2", "V2.5L3")
            line += ", ".join(f"{c} {n}" for c, n in zip(counts, names))
        else:
            labels = ("MPEG 1 Layer I", "MPEG 1 Layer II", "MPEG 1 Layer III",
                      "MPEG 2 Layer I", "MPEG 2 Layer II", "MPEG 2 Layer III",
                      "MPEG 2.5 Layer I", "MPEG 2.5 Layer II", "MPEG 2.5 Layer III")
            line += next(label for c, label in zip(counts, labels) if c)
        line += "), "
    else:
        line += "No MPEG frames, "
    if tags_total:
        if info.id3v1 > 1 or info.id3v2 > 1 or info.apev2 > 1:
            line += f"{tags_total} tags ({info.id3v1} ID3v1, {info.id3v2} ID3v2, {info.apev2} APEv2)"
        else:
            if info.id3v1:
                line += "+ID3v1"
            if info.id3v2:
                line += "+ID3v2"
            if info.apev2:
                line += "+APEv2"
    else:
        line += "no tags"
    line += ", "
    if not info.bVariableBitrate:
        line += "CBR"
    elif info.VBRHeaderPresent:
        line += "Xing header" if info.IsXingHeader else "VBRI header"
    else:
        line += "no VBR header"
    if info.bCRC:
        line += ", CRC"
    out.lines.append(line)


@dataclass
class ValidationResult:
    """What one mp3val run on one file gives: its stdout lines (the same
    text mp3val prints, INFO line included), whether the file needed a
    fix (MPEGINFO.iErrors after the report) and, for a fix, the rebuilt
    file's bytes."""

    lines: list[str]
    needs_fix: bool
    fixed: bytes | None = None


def validate_bytes(data: bytes, filename: str, fix: bool = False) -> ValidationResult:
    """ProcessFile() without the file handling: checks data as mp3val
    does and, with fix=True and something to fix, rebuilds it. filename
    is what the messages quote (mp3val quotes the full path).

    Raises ValueError for empty data (mp3val: "Cannot open input file
    ... or it is empty") and ValidatorAbort where mp3val would hang
    (ValidatorLoopError) or crash (ValidatorCrashError).
    """
    if not data:
        raise ValueError("the file is empty")
    file_size = len(data)
    lines: list[str] = []
    out = _Messages(lines, filename)
    info = MpegInfo()
    d = bytearray(data)
    d.extend(bytes(_PAD))
    validate_file(d, file_size, info, out, False, None)
    print_report(out, info)
    needs_fix = info.iErrors != 0
    fixed = None
    if fix and needs_fix:
        hfile = _OutFile()
        validate_file(d, file_size, info, _Messages(None, filename), True, hfile)
        fixed = bytes(hfile.buf)
    return ValidationResult(lines=lines, needs_fix=needs_fix, fixed=fixed)


def _full_path(path: Path) -> str:
    return os.path.abspath(str(path))


def check_file(path: Path) -> list[str]:
    """mp3val <path>: the WARNING/ERROR/INFO lines for the file. Never
    writes. Raises OSError if the file cannot be read, ValueError if it
    is empty."""
    data = Path(path).read_bytes()
    return validate_bytes(data, _full_path(path)).lines


def fix_file(path: Path, delete_backup: bool = False) -> list[str]:
    """mp3val -f [-nb] <path>. If the check finds something to fix, the
    rebuilt file replaces the original, the original is kept as
    <name>.bak (an old .bak is replaced) unless delete_backup, and the
    file attributes (read-only, hidden) are carried over; timestamps are
    not (mp3val keeps them only with -t, which is not used). Returns the
    lines mp3val prints: the check's findings (from before the fix) and
    a FIXED line."""
    path = Path(path)
    data = path.read_bytes()
    full = _full_path(path)
    result = validate_bytes(data, full, fix=True)
    lines = list(result.lines)
    if result.fixed is None:
        return lines
    attributes = _get_attributes(path)
    backup = path.with_name(path.name + ".bak")
    fd, tmp_name = tempfile.mkstemp(prefix=".mp3val-", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(result.fixed)
        # CrossAPI_MoveFile(): delete the target first, then move.
        if backup.exists():
            _make_writable(backup)
            backup.unlink()
        os.rename(path, backup)
        try:
            os.rename(tmp_path, path)
        except OSError:
            os.rename(backup, path)  # put the original back
            raise
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
    _set_attributes(path, attributes)
    if delete_backup:
        _make_writable(backup)
        backup.unlink()
    lines.append(f'FIXED: "{full}": File was rebuilt')
    return lines


def _get_attributes(path: Path):
    if sys.platform == "win32":
        import ctypes

        value = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        return None if value == 0xFFFFFFFF else value
    return os.stat(path).st_mode


def _set_attributes(path: Path, attributes) -> None:
    if attributes is None:
        return
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.kernel32.SetFileAttributesW(str(path), attributes)
    else:
        os.chmod(path, attributes & 0o7777)


def _make_writable(path: Path) -> None:
    try:
        os.chmod(path, os.stat(path).st_mode | 0o200)
    except OSError:
        pass
