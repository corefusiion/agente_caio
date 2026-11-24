"""Pure, bounded planning from *caller-asserted* metadata, not a media decoder.

This module intentionally performs no file, process, model or network I/O.
A plan is not permission to run an extractor: no safe worker is implemented.
"""
from dataclasses import dataclass
import math

MAX_FILE_BYTES = 256 * 1024 * 1024
MIN_DURATION_SECONDS = 0.001
MAX_DURATION_SECONDS = 2 * 60 * 60
# Targets for a future worker, not enforced against any actual extraction here.
TARGET_FRAME_MAX_BYTES = 1024 * 1024
TARGET_AUDIO_MAX_BYTES = 96_000  # 3 s of 16 kHz mono 16-bit PCM
TARGET_WORKER_TIMEOUT_SECONDS = 60
MAX_FRAMES = 360_000
MAX_DIMENSION = 4096
MAX_PIXELS = 4096 * 2160
MAX_FRAME_SAMPLES = 5
MAX_AUDIO_SAMPLES = 3
MAX_AUDIO_SAMPLE_SECONDS = 3.0


class PlanError(ValueError):
    """Consent absent or untrusted input exceeds the planning policy."""


@dataclass(frozen=True, slots=True)
class MediaMetadata:
    """Caller-provided assertions, not verified observations of a file."""
    file_bytes: int
    duration_seconds: float
    frame_count: int
    width: int
    height: int
    has_audio: bool


@dataclass(frozen=True, slots=True)
class AudioWindow:
    start_seconds: float
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class Plan:
    """Proposed positions only; nothing here has been extracted or inferred."""
    frame_times_seconds: tuple[float, ...]
    audio_windows: tuple[AudioWindow, ...]
    metadata_verified: bool = False
    extraction_performed: bool = False
    inference_performed: bool = False
    summary: None = None
    transcript: None = None
    task_suggestions: None = None


def _int_range(value: object, low: int, high: int, field: str) -> int:
    if type(value) is not int or not low <= value <= high:
        raise PlanError(f"{field} outside policy")
    return value


def make_plan(metadata: MediaMetadata, *, consent: bool) -> Plan:
    """Plan sampling from assertions after explicit consent; never open a file.

    Consent here authorizes only making this inert plan. It does not authorize
    extraction, model use, uploading or executing any task.
    """
    if consent is not True:
        raise PlanError("explicit consent is required")
    if type(metadata) is not MediaMetadata:
        raise PlanError("expected MediaMetadata")
    _int_range(metadata.file_bytes, 1, MAX_FILE_BYTES, "file_bytes")
    _int_range(metadata.frame_count, 1, MAX_FRAMES, "frame_count")
    width = _int_range(metadata.width, 1, MAX_DIMENSION, "width")
    height = _int_range(metadata.height, 1, MAX_DIMENSION, "height")
    if width * height > MAX_PIXELS:
        raise PlanError("pixel count outside policy")
    if type(metadata.has_audio) is not bool:
        raise PlanError("has_audio must be boolean")
    duration = metadata.duration_seconds
    if type(duration) not in (int, float):
        raise PlanError("duration outside policy")
    try:
        valid_duration = math.isfinite(duration) and MIN_DURATION_SECONDS <= duration <= MAX_DURATION_SECONDS
    except (OverflowError, TypeError):
        valid_duration = False
    if not valid_duration:
        raise PlanError("duration outside policy")
    duration = float(duration)
    # One midpoint per equal interval. Never advertise more samples than frames.
    count = min(MAX_FRAME_SAMPLES, metadata.frame_count)
    frames = tuple(duration * ((i + 0.5) / count) for i in range(count))
    windows = ()
    if metadata.has_audio:
        clip = min(MAX_AUDIO_SAMPLE_SECONDS, duration / MAX_AUDIO_SAMPLES)
        windows = tuple(
            AudioWindow(duration * ((i + 0.5) / MAX_AUDIO_SAMPLES) - clip / 2, clip)
            for i in range(MAX_AUDIO_SAMPLES)
        )
    return Plan(frames, windows)
