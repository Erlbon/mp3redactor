"""
The quality colours of the Bitrate and Sample Rate columns: this app's thin layer over redactor_common's shared audio
profile (core/quality_bands.classify_audio). Qt-free; the table paints the results with
redactor_common.gui.quality_colour.apply_band_colour().

Yellow = poor (a bitrate below the poor limit, a VBR file judged on its average, or a stereo sample rate below 32 kHz),
green = fine, no colour = unknown. The two limits are Preferences > Table colours (poor, default 128 kbps; good,
default 192 kbps, which only shapes the tooltip wording).
"""

from __future__ import annotations

from redactor_common.core.quality_bands import (
    AudioBandSettings,
    Band,
    BandResult,
    classify_audio,
    format_kbps,
)

DEFAULT_POOR_KBPS = 128
DEFAULT_GOOD_KBPS = 192
MIN_KBPS = 32
MAX_KBPS = 320
CODEC = "mp3"  # the app lists only MP3 files

BITRATE_LEGEND = "Bitrate: yellow = poor, green = fine"


def normalise_limits(poor: object, good: object) -> tuple[int, int]:
    """The two limits as ints in 32..320 with poor < good (a bad value gets the default; when poor is not below good,
    good is raised to just above poor, or poor lowered below good at the top of the range)."""
    def to_int(value: object, default: int) -> int:
        try:
            number = int(value)  # type: ignore[call-overload]
        except (TypeError, ValueError):
            return default
        return min(MAX_KBPS, max(MIN_KBPS, number))

    poor_kbps = to_int(poor, DEFAULT_POOR_KBPS)
    good_kbps = to_int(good, DEFAULT_GOOD_KBPS)
    if poor_kbps >= good_kbps:
        good_kbps = min(MAX_KBPS, poor_kbps + 1)
        if poor_kbps >= good_kbps:
            poor_kbps = good_kbps - 1
    return poor_kbps, good_kbps


def band_settings(poor: object = DEFAULT_POOR_KBPS, good: object = DEFAULT_GOOD_KBPS) -> AudioBandSettings:
    poor_kbps, good_kbps = normalise_limits(poor, good)
    return AudioBandSettings(poor_kbps=float(poor_kbps), good_kbps=float(good_kbps))


def is_vbr(mp3) -> bool | None:
    """True for a variable-bitrate stream, False for CBR, None when the mode is unknown. ABR counts as variable."""
    mode = (getattr(mp3, "bitrate_mode", "") or "").upper()
    if mode in ("VBR", "ABR"):
        return True
    if mode == "CBR":
        return False
    return None


def bitrate_result(mp3, settings: AudioBandSettings) -> BandResult:
    """The band of a loaded file's Bitrate cell."""
    return classify_audio(
        mp3.bitrate_kbps, CODEC, vbr=is_vbr(mp3), sample_rate_hz=mp3.sample_rate_hz,
        channels=mp3.channels, settings=settings,
    )


def sample_rate_result(mp3, settings: AudioBandSettings) -> BandResult | None:
    """A yellow result for the Sample Rate cell when the sample rate alone makes the file poor (below 32 kHz for
    stereo music), else None. Asked of the shared profile with a transparent bitrate, so only the sample rate can
    be the reason."""
    if not mp3.sample_rate_hz:
        return None
    probe = classify_audio(320, CODEC, vbr=False, sample_rate_hz=mp3.sample_rate_hz, channels=mp3.channels,
                           settings=settings)
    if probe.band is not Band.POOR:
        return None
    kilohertz = mp3.sample_rate_hz / 1000
    return BandResult(
        Band.POOR, f"{kilohertz:g} kHz (poor)",
        f"{kilohertz:g} kHz is below 32 kHz for stereo music: the high frequencies are cut off, whatever the "
        "bitrate.\nYellow means 'check it', not 'bad': speech and simple material need less.",
    )


def display_bitrate(mp3) -> str:
    """'192 kbps', 'VBR ~190 kbps' for a variable file, '' when unknown."""
    if not mp3.bitrate_kbps:
        return ""
    if is_vbr(mp3):
        return f"VBR ~{format_kbps(mp3.bitrate_kbps)}"
    return format_kbps(mp3.bitrate_kbps)


def convert_hint(kbps: int, settings: AudioBandSettings) -> str:
    """One line for the Convert to MP3 dialog: the band of a chosen CBR bitrate by the table's colours."""
    result = classify_audio(kbps, CODEC, vbr=False, sample_rate_hz=44100, channels=2, settings=settings)
    if result.band is Band.POOR:
        verdict = "is poor by the table's colours (yellow)"
    elif result.band is Band.OK:
        verdict = "is fine by the table's colours (green)"
    else:
        verdict = "has no table colour"
    return f"{kbps} kbps {verdict}; poor is below {format_kbps(settings.poor_kbps)}."
