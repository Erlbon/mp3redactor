"""
BPM detection using aubio's Python bindings directly (import aubio),
rather than shelling out to the `aubio tempo` CLI -- aubio ships real
bindings, so there's no need to pay subprocess overhead or parse text
output the way mp3val/keyfinder-cli require.

aubio is treated as an optional dependency: import failures are caught
and reported as STATUS_TOOL_MISSING (same vocabulary mp3val's runner
uses for a missing binary) rather than raising, so the app can still
scan/validate files that have nothing to do with BPM when it isn't
installed.

Installed via the `aubio-ledfx` PyPI package (see requirements.txt) --
a fork providing prebuilt Windows wheels, since plain `aubio` is
source-only and needs MSVC Build Tools to build. Same module name
either way, so `import aubio` below is unaffected by which one is
installed.
"""

from pathlib import Path

from core.mp3_file import STATUS_ERROR, STATUS_OK, STATUS_TOOL_MISSING

WIN_S = 1024  # FFT window size
HOP_S = WIN_S // 2  # hop size (50% overlap)


def detect_bpm(path: Path) -> tuple[float | None, str, str]:
    """
    Returns (bpm, status, message).

    bpm is None unless status is STATUS_OK. status is one of
    STATUS_OK / STATUS_ERROR / STATUS_TOOL_MISSING.
    """
    try:
        import aubio
    except ImportError:
        return None, STATUS_TOOL_MISSING, "aubio is not installed"

    try:
        src = aubio.source(str(path), 0, HOP_S)
        samplerate = src.samplerate
        tempo_detector = aubio.tempo("default", WIN_S, HOP_S, samplerate)

        beat_times = []
        total_frames = 0
        while True:
            samples, read = src()
            if tempo_detector(samples):
                beat_times.append(tempo_detector.get_last_s())
            total_frames += read
            if read < HOP_S:
                break

        if len(beat_times) < 2:
            return None, STATUS_ERROR, "not enough detected beats to estimate a tempo"

        bpm = tempo_detector.get_bpm()
        if not bpm or bpm <= 0:
            # get_bpm() can be unreliable right after a short read; fall
            # back to the median inter-beat interval, same approach the
            # aubio CLI itself uses (see its process_tempo.flush()).
            intervals = [b - a for a, b in zip(beat_times, beat_times[1:])]
            intervals.sort()
            median_interval = intervals[len(intervals) // 2]
            if median_interval <= 0:
                return None, STATUS_ERROR, "could not derive a tempo from detected beats"
            bpm = 60.0 / median_interval

        return round(bpm, 1), STATUS_OK, ""

    except Exception as e:  # noqa: BLE001 -- any aubio/decoder failure is a per-file result, not a crash
        return None, STATUS_ERROR, f"BPM detection failed: {e}"
