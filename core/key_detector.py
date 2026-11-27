"""
Musical key detection in-process: a Python/numpy port of libkeyfinder
2.2.8 (https://github.com/mixxxdj/libkeyfinder, GPL-3.0-or-later,
Copyright 2011-2015 Ibrahim Sha'ath) as driven by keyfinder-cli 1.2.0,
the external tool this replaces (core.keyfinder_runner still runs that
tool as a fallback while the port is being checked against it; see
tools/compare_keyfinder.py).

The port follows the C++ step by step, constants included, so that it
gives the same key as keyfinder-cli:

1. Mix to mono (mean of the channels).
2. Low-pass filter (a 160th-order FIR built from an ideal response by an
   inverse FFT, Hamming-windowed, corner = top band * 1.012) and keep
   every Nth sample, N = floor(rate / 2 / (top band * 1.10)), 10 at
   44.1 kHz. libkeyfinder sets the last (sample count % N) samples aside
   and never uses them; so does this port.
3. Blackman-windowed 16384-point FFT frames, hop 4096, over the
   downsampled signal; the last partial frames are zero-padded the way
   KeyFinder::finalChromagram() pads them.
4. Each frame's magnitude spectrum -> 72 bands (6 octaves of semitones
   from C1) through libkeyfinder's direct spectral kernel.
5. The band vectors are averaged over the whole track and compared with
   the 24 major/minor tone profiles (octave-weighted) by cosine
   similarity. No positive score at all means silence: no key.

The analysis streams: audio is fed in blocks (KeyAnalyser.feed), so a
multi-hour audiobook never has to be held in memory whole.

Decoding: aubio (already used in-process for BPM) first; when aubio
cannot open or decode the file (its Linux wheel has no MP3 decoder),
ffmpeg decodes it to a pipe instead.
"""

from __future__ import annotations

import math
import struct
import subprocess
from pathlib import Path
from typing import Iterable

from core.mp3_file import STATUS_OK, STATUS_TOOL_ERROR, STATUS_TOOL_MISSING

SEMITONES = 12
OCTAVES = 6
BANDS = SEMITONES * OCTAVES
FFT_FRAME_SIZE = 16384
HOP_SIZE = FFT_FRAME_SIZE // 4
DIRECT_SK_STRETCH = 0.8
LPF_ORDER = 160
LPF_FFT_FRAME_SIZE = 2048  # KeyFinder::preprocess designs the filter with a 2048-point inverse FFT
DECODE_BLOCK = 65536  # samples per aubio read / ffmpeg pipe read

# Band centre frequencies, C1 (32.7 Hz) up to B6, copied from
# libkeyfinder's constants.cpp (not recomputed: the port must match it).
FREQUENCIES = (
    32.7031956625748, 34.647828872109, 36.708095989676, 38.8908729652601,
    41.2034446141088, 43.6535289291255, 46.2493028389543, 48.9994294977187,
    51.9130871974932, 55, 58.2704701897613, 61.7354126570155,
    65.4063913251497, 69.2956577442181, 73.4161919793519, 77.7817459305203,
    82.4068892282175, 87.307057858251, 92.4986056779087, 97.9988589954374,
    103.826174394986, 110, 116.540940379523, 123.470825314031,
    130.812782650299, 138.591315488436, 146.832383958704, 155.563491861041,
    164.813778456435, 174.614115716502, 184.997211355817, 195.997717990875,
    207.652348789973, 220, 233.081880759045, 246.941650628062,
    261.625565300599, 277.182630976872, 293.664767917408, 311.126983722081,
    329.62755691287, 349.228231433004, 369.994422711635, 391.99543598175,
    415.304697579946, 440.000000000001, 466.163761518091, 493.883301256125,
    523.251130601198, 554.365261953745, 587.329535834816, 622.253967444163,
    659.255113825741, 698.456462866009, 739.98884542327, 783.9908719635,
    830.609395159892, 880.000000000002, 932.327523036182, 987.76660251225,
    1046.5022612024, 1108.73052390749, 1174.65907166963, 1244.50793488833,
    1318.51022765148, 1396.91292573202, 1479.97769084654, 1567.981743927,
    1661.21879031978, 1760, 1864.65504607236, 1975.5332050245,
)

MAJOR_PROFILE = (
    7.23900502618145225142, 3.50351166725158691406, 3.58445177536649417505,
    2.84511816478676315967, 5.81898892118549859731, 4.55865057415321039969,
    2.44778850545506543313, 6.99473192146829525484, 3.39106613673504853068,
    4.55614256655143456953, 4.07392666663523606019, 4.45932757378886890365,
)
MINOR_PROFILE = (
    7.00255045060284420089, 3.14360279015996679775, 4.35904319714962529275,
    5.40418120718934069657, 3.67234420879306133756, 4.08971184917797891956,
    3.90791435991553992579, 6.19960288562316463867, 3.63424625625277419871,
    2.87241191079875557435, 5.35467999794542670600, 3.83242038595048351013,
)
OCTAVE_WEIGHTS = (
    0.39997267549999998559, 0.55634425248300645173, 0.52496636345143543600,
    0.60847548384277727607, 0.59898115679999996974, 0.49072435317960994006,
)

# keyfinder-cli's "standard" notation, indexed by libkeyfinder's key_t
# (A major = 0, A minor = 1, B-flat major = 2, ...).
STANDARD_NOTATION = (
    "A", "Am", "Bb", "Bbm", "B", "Bm", "C", "Cm", "Db", "Dbm", "D", "Dm",
    "Eb", "Ebm", "E", "Em", "F", "Fm", "Gb", "Gbm", "G", "Gm", "Ab", "Abm",
)
SILENCE = 24


FFMPEG_TIMEOUT_SECONDS = 1800  # one file's decode through ffmpeg; the longest audiobook decodes in well under this


class KeyDetectionError(Exception):
    """The audio cannot be analysed (for example a sample rate too low)."""


def downsample_factor(frame_rate: int) -> int:
    ds_cutoff = FREQUENCIES[-1] * 1.10
    return int(math.floor(frame_rate / 2 / ds_cutoff))


def _hamming(np, n: int):
    i = np.arange(n, dtype=np.float64)
    return 0.54 - 0.46 * np.cos((2 * math.pi * i) / (n - 1))


def _blackman(np, n: int):
    i = np.arange(n, dtype=np.float64)
    return 0.42 - 0.5 * np.cos((2 * math.pi * i) / (n - 1)) + 0.08 * np.cos((4 * math.pi * i) / (n - 1))


def _lowpass_coefficients(np, frame_rate: int):
    """LowPassFilterPrivate's constructor. Returns (coefficients, gain):
    an ideal brick-wall response, inverse-FFT'd at 2048 points, centred,
    cut to 161 taps and Hamming-windowed."""
    n = LPF_FFT_FRAME_SIZE
    corner = FREQUENCIES[-1] * 1.012
    cutoff_point = corner / frame_rate
    tau = 0.5 / cutoff_point
    spectrum = np.zeros(n // 2 + 1, dtype=np.float64)
    for i in range(n // 2):
        value = tau if (i / n) <= cutoff_point else 0.0
        spectrum[i] = value  # setInput(i, ...)
        mirror = n - i - 1   # setInput(n - i - 1, ...): only index n/2 is read by a c2r transform
        if mirror == n // 2:
            spectrum[mirror] = value
    # FFTW's c2r output divided by n (InverseFftAdapter::getOutput) is numpy's irfft.
    response = np.fft.irfft(spectrum, n)
    impulse_length = LPF_ORDER + 1
    centre = LPF_ORDER // 2
    idx = (n - centre + np.arange(impulse_length)) % n
    coefficients = response[idx] * _hamming(np, impulse_length)
    return coefficients, float(coefficients.sum())


def _spectral_kernel(np, frame_rate: int):
    """ChromaTransform's direct spectral kernel as a dense (72, bins) matrix."""
    if frame_rate < 1:
        raise KeyDetectionError("Frame rate must be > 0")
    if FREQUENCIES[-1] > frame_rate / 2.0:
        raise KeyDetectionError("Analysis frequencies over Nyquist")
    if frame_rate / FFT_FRAME_SIZE > (FREQUENCIES[1] - FREQUENCIES[0]):
        raise KeyDetectionError("Insufficient low-end resolution")
    bins = FFT_FRAME_SIZE // 2 + 1
    kernel = np.zeros((BANDS, bins), dtype=np.float64)
    q_factor = DIRECT_SK_STRETCH * (math.pow(2, 1.0 / SEMITONES) - 1)
    for band in range(BANDS):
        freq = FREQUENCIES[band]
        centre = freq * FFT_FRAME_SIZE / frame_rate
        width = centre * q_factor
        begin = centre - (width / 2)
        end = begin + width
        first = int(math.ceil(begin))
        last = int(math.floor(end))
        fft_bins = np.arange(first, last + 1)
        coeffs = 1.0 - np.cos((2 * math.pi * (fft_bins - begin)) / width)
        kernel[band, first:last + 1] = coeffs / coeffs.sum() * freq
    return kernel


def _classify(np, chroma) -> int:
    """KeyClassifier::classify with ToneProfile::cosineSimilarity."""
    best_score = 0.0  # the all-zero silence profile always scores 0
    best = SILENCE
    for offset in range(SEMITONES):
        for scale, base in ((0, MAJOR_PROFILE), (1, MINOR_PROFILE)):
            intersection = 0.0
            profile_norm = 0.0
            input_norm = 0.0
            for o in range(OCTAVES):
                weight = OCTAVE_WEIGHTS[o]
                for j in range(SEMITONES):
                    p = weight * base[(3 - offset + j) % SEMITONES]
                    x = chroma[o * SEMITONES + j]
                    intersection += x * p
                    profile_norm += p * p
                    input_norm += x * x
            if profile_norm > 0 and input_norm > 0:
                score = intersection / (math.sqrt(profile_norm) * math.sqrt(input_norm))
            else:
                score = 0.0
            if score > best_score:
                best_score = score
                best = offset * 2 + scale
    return best


class KeyAnalyser:
    """Streams mono audio through libkeyfinder's pipeline. feed() blocks of
    float samples (any scale), then finish() returns the key_t index
    (SILENCE = 24 for no key)."""

    def __init__(self, frame_rate: int):
        import numpy as np

        self._np = np
        self.frame_rate = int(frame_rate)
        self.factor = downsample_factor(self.frame_rate)
        if self.factor < 1:
            raise KeyDetectionError(f"sample rate {self.frame_rate} Hz is too low to analyse")
        self.ds_rate = self.frame_rate // self.factor  # AudioData::downsample's integer division
        coefficients, gain = _lowpass_coefficients(np, self.frame_rate)
        self._coefficients = coefficients / gain  # the C++ divides each sample by gain instead
        self._kernel_t = _spectral_kernel(np, self.ds_rate).T.copy()
        self._window = _blackman(np, FFT_FRAME_SIZE)
        self._delay = LPF_ORDER // 2
        # Raw samples not yet filtered, starting at absolute index self._raw_start.
        # The filter looks back `delay` samples; before the start that is zeros.
        self._raw = np.zeros(self._delay, dtype=np.float64)
        self._raw_start = -self._delay
        self._next_out = 0  # next absolute raw index whose filtered value is wanted
        self._total = 0
        self._ds = np.zeros(0, dtype=np.float64)
        self._chroma_sum = np.zeros(BANDS, dtype=np.float64)
        self._hops = 0

    def feed(self, samples) -> None:
        np = self._np
        block = np.asarray(samples, dtype=np.float64).ravel()
        if block.size == 0:
            return
        self._total += block.size
        self._raw = np.concatenate((self._raw, block))
        self._filter_available(final=False)

    def _filter_available(self, final: bool) -> None:
        np = self._np
        taps = LPF_ORDER + 1
        raw_end = self._raw_start + self._raw.size  # exclusive absolute index
        # An output at absolute index n needs raw[n - delay .. n + delay].
        last_wanted = raw_end - 1 - self._delay
        if not final:
            # The last (total % factor) samples, fewer than `factor`, are
            # zeros to libkeyfinder (set aside, never used). Until the end
            # is known, leave every output that could reach them for finish().
            last_wanted -= self.factor
        if last_wanted < self._next_out:
            return
        count = (last_wanted - self._next_out) // self.factor + 1
        first_rel = self._next_out - self._delay - self._raw_start
        windows = np.lib.stride_tricks.sliding_window_view(self._raw[first_rel:], taps)[: (count - 1) * self.factor + 1: self.factor]
        out = windows @ self._coefficients
        self._ds = np.concatenate((self._ds, out))
        self._next_out += count * self.factor
        keep_from = self._next_out - self._delay - self._raw_start
        self._raw = self._raw[keep_from:]
        self._raw_start += keep_from
        self._analyse_frames()

    def _analyse_frames(self) -> None:
        np = self._np
        if self._ds.size < FFT_FRAME_SIZE:
            return
        hops = 1 + (self._ds.size - FFT_FRAME_SIZE) // HOP_SIZE
        frames = np.lib.stride_tricks.sliding_window_view(self._ds, FFT_FRAME_SIZE)[: (hops - 1) * HOP_SIZE + 1: HOP_SIZE]
        # Batches keep the FFT working set small on long files.
        for start in range(0, hops, 64):
            batch = frames[start:start + 64] * self._window
            magnitudes = np.abs(np.fft.rfft(batch, axis=1))
            self._chroma_sum += (magnitudes @ self._kernel_t).sum(axis=0)
        self._hops += hops
        self._ds = self._ds[hops * HOP_SIZE:]

    def finish(self) -> int:
        np = self._np
        # KeyFinder::preprocess sets the last (count % factor) samples aside
        # and finalChromagram never uses them: drop them, then the filter
        # runs off the end into zeros.
        usable = self._total - (self._total % self.factor)
        if self._next_out < usable:
            # The raw buffer never reaches back past the last output's window,
            # so cutting at `usable` is always inside it.
            self._raw = self._raw[: usable - self._raw_start]
            last_out = self._next_out + ((usable - 1 - self._next_out) // self.factor) * self.factor
            needed_end = last_out + self._delay + 1
            pad = needed_end - (self._raw_start + self._raw.size)
            self._raw = np.concatenate((self._raw, np.zeros(pad, dtype=np.float64)))
            self._filter_available(final=True)
        # finalChromagram: pad the remaining downsampled samples to whole frames.
        remaining = self._ds.size
        padded_hops = math.ceil(remaining / HOP_SIZE)
        if padded_hops > 0:
            final_length = FFT_FRAME_SIZE + (padded_hops - 1) * HOP_SIZE
            self._ds = np.concatenate((self._ds, np.zeros(final_length - remaining, dtype=np.float64)))
            self._analyse_frames()
        if self._hops == 0:
            return SILENCE
        chroma = self._chroma_sum / self._hops
        return _classify(np, chroma.tolist())


def key_of_samples(samples, frame_rate: int) -> int:
    """key_t index of a whole mono signal (tests, small inputs)."""
    analyser = KeyAnalyser(frame_rate)
    analyser.feed(samples)
    return analyser.finish()


def key_name(key_index: int) -> str:
    """keyfinder-cli's standard notation; "" for silence."""
    return "" if key_index == SILENCE else STANDARD_NOTATION[key_index]


# ---------------------------------------------------------------- decoding

def _aubio_blocks(path: str) -> tuple[int, Iterable]:
    import aubio

    src = aubio.source(path, 0, DECODE_BLOCK)  # 0 = the file's own rate; reads are mono-mixed

    def blocks():
        try:
            while True:
                samples, read = src()
                if read:
                    yield samples[:read]
                if read < DECODE_BLOCK:
                    break
        finally:
            src.close()

    return int(src.samplerate), blocks()


def _read_exact(stream, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = stream.read(n - len(data))
        if not chunk:
            break
        data += chunk
    return data


def _ffmpeg_blocks(exe: str, path: str):
    """Decodes with ffmpeg to a mono 16-bit WAV on a pipe. Returns
    (rate, blocks, finish) where finish() returns ffmpeg's error text or ""."""
    import numpy as np

    from core.subprocess_utils import popen_tool

    proc = popen_tool(
        [exe, "-v", "error", "-nostdin", "-i", path, "-vn", "-map_metadata", "-1",
         "-ac", "1", "-c:a", "pcm_s16le", "-f", "wav", "-"],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    stderr_parts: list[bytes] = []
    import threading

    def drain():
        stderr_parts.append(proc.stderr.read())

    drainer = threading.Thread(target=drain, daemon=True)
    drainer.start()
    # A stuck decode (a dead network share, a hung codec) must not hold the batch for ever: killing ffmpeg
    # ends the pipe, and the reads below then fail with "no audio".
    watchdog = threading.Timer(FFMPEG_TIMEOUT_SECONDS, proc.kill)
    watchdog.daemon = True
    watchdog.start()

    def fail(message: str):
        watchdog.cancel()
        proc.kill()
        proc.wait()
        drainer.join(timeout=5)
        err = b"".join(stderr_parts).decode("utf-8", "replace").strip()
        raise KeyDetectionError(err or message)

    header = _read_exact(proc.stdout, 12)
    if len(header) < 12 or header[:4] != b"RIFF" or header[8:12] != b"WAVE":
        fail("ffmpeg produced no audio")
    rate = None
    while True:
        chunk_head = _read_exact(proc.stdout, 8)
        if len(chunk_head) < 8:
            fail("ffmpeg produced no audio data")
        chunk_id, size = chunk_head[:4], struct.unpack("<I", chunk_head[4:])[0]
        if chunk_id == b"data":
            break
        body = _read_exact(proc.stdout, size + (size & 1))
        if chunk_id == b"fmt ":
            rate = struct.unpack("<I", body[4:8])[0]
    if not rate:
        fail("ffmpeg produced no audio format")

    def blocks():
        leftover = b""
        while True:
            data = proc.stdout.read(DECODE_BLOCK * 2)
            if not data:
                break
            data = leftover + data
            usable = len(data) - (len(data) % 2)
            leftover = data[usable:]
            yield np.frombuffer(data[:usable], dtype="<i2").astype(np.float64)

    def finish(kill: bool = False) -> str:
        """Closes the pipe and waits for ffmpeg; kill=True (the analysis failed) stops it first, so no
        ffmpeg is left running with the file open."""
        watchdog.cancel()
        if kill:
            proc.kill()
        proc.stdout.close()
        code = proc.wait()
        drainer.join(timeout=5)
        if code != 0:
            return b"".join(stderr_parts).decode("utf-8", "replace").strip() or f"ffmpeg exited with code {code}"
        return ""

    return rate, blocks(), finish


def _analyse_blocks(rate: int, blocks: Iterable) -> int:
    analyser = KeyAnalyser(rate)
    for block in blocks:
        analyser.feed(block)
    return analyser.finish()


def detect_key_native(path: Path, ffmpeg_path: str | None = None) -> tuple[str, str, str]:
    """
    Returns (key, status, message), the same contract as
    core.keyfinder_runner.detect_key(): key in standard notation ("A",
    "Bbm", ...) or "" for silent audio (still STATUS_OK) and on errors.
    STATUS_TOOL_MISSING when numpy is not installed, STATUS_TOOL_ERROR when
    neither aubio nor ffmpeg can decode the file.

    ffmpeg_path: the ffmpeg to fall back to; found with find_tool() when None.
    """
    try:
        import numpy  # noqa: F401
    except ImportError:
        return "", STATUS_TOOL_MISSING, "numpy is not installed"

    errors = []
    try:
        rate, blocks = _aubio_blocks(str(path))
        key = _analyse_blocks(rate, blocks)
        return key_name(key), STATUS_OK, "" if key != SILENCE else "no key detected (silent audio)"
    except KeyDetectionError as e:
        return "", STATUS_TOOL_ERROR, f"key detection failed: {e}"
    except ImportError:
        errors.append("aubio is not installed")
    except Exception as e:  # noqa: BLE001 -- any aubio/decoder failure: try ffmpeg next
        errors.append(f"aubio: {e}")

    from core.ffmpeg_probe import FFMPEG_EXE_NAME
    from core.tool_locator import find_tool

    found = find_tool(FFMPEG_EXE_NAME, override=ffmpeg_path or None)
    exe = str(found) if found else None
    if exe is None:
        return "", STATUS_TOOL_ERROR, f"cannot decode this file ({'; '.join(errors)}; ffmpeg not found)"
    finish = None
    try:
        rate, blocks, finish = _ffmpeg_blocks(exe, str(path))
        key = _analyse_blocks(rate, blocks)
        problem = finish()
        finish = None
    except KeyDetectionError as e:
        return "", STATUS_TOOL_ERROR, f"key detection failed: {e}"
    except OSError as e:
        return "", STATUS_TOOL_ERROR, f"failed to run ffmpeg: {e}"
    finally:
        if finish is not None:  # the analysis raised: ffmpeg is still running, kill and reap it
            try:
                finish(kill=True)
            except Exception:  # noqa: BLE001 -- cleanup must not hide the real error
                pass
    if problem:
        return "", STATUS_TOOL_ERROR, f"ffmpeg could not decode this file: {problem}"
    return key_name(key), STATUS_OK, "" if key != SILENCE else "no key detected (silent audio)"
