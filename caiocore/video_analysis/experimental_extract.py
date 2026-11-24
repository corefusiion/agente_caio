"""Experimental decoder component. NOT a security boundary or app entry point.

The private worker must run *inside* a separately verified OS sandbox before it
receives data. Its existence does not authorize decoding untrusted media.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import selectors
import signal
import subprocess
import time

MAX_INPUT = 2 * 1024 * 1024
MAX_PIXELS = 640 * 360
MAX_AUDIO = 32_000  # one second, mono, 16 kHz, signed little-endian 16-bit
MAX_PROBE = 16_384
_TIMEOUT = 8
_MAGIC = b'nut/multimedia container\x00'


class ExtractionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Sample:
    width: int
    height: int
    rgb24: bytes
    pcm_s16le: bytes
    # Container duration is not known/verified. Samples are not transcription.


def extract_local(data: bytes, *, consent: bool) -> Sample:
    """Disabled: caller consent cannot establish isolation or enable this API."""
    raise ExtractionError('safe decoder sandbox not implemented; extraction disabled')


def _limits() -> None:
    # Called in a single-threaded dedicated worker only. Not sufficient isolation.
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    resource.setrlimit(resource.RLIMIT_AS, (1024 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))


def _run(argv: list[str], data: bytes, max_output: int) -> bytes:
    """Feed and drain concurrently, cap output before buffering, kill on timeout."""
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, close_fds=True,
                                start_new_session=True, preexec_fn=_limits,
                                env={'PATH': '/usr/bin:/bin', 'LANG': 'C',
                                     'HOME': '/nonexistent', 'OMP_NUM_THREADS': '1'})
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExtractionError('worker unavailable') from exc
    out = bytearray()
    position = 0
    deadline = time.monotonic() + _TIMEOUT
    selector = selectors.DefaultSelector()
    try:
        assert proc.stdin is not None and proc.stdout is not None
        os.set_blocking(proc.stdin.fileno(), False)
        os.set_blocking(proc.stdout.fileno(), False)
        selector.register(proc.stdin, selectors.EVENT_WRITE)
        selector.register(proc.stdout, selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ExtractionError('worker timeout')
            for key, _ in selector.select(remaining):
                if key.fileobj is proc.stdin:
                    try:
                        sent = os.write(proc.stdin.fileno(), data[position:position + 65536])
                    except BrokenPipeError:
                        sent = 0
                        position = len(data)
                    position += sent
                    if position == len(data):
                        selector.unregister(proc.stdin)
                        proc.stdin.close()
                else:
                    chunk = os.read(proc.stdout.fileno(), min(65536, max_output + 1 - len(out)))
                    if not chunk:
                        selector.unregister(proc.stdout)
                        proc.stdout.close()
                    else:
                        out.extend(chunk)
                        if len(out) > max_output:
                            raise ExtractionError('output exceeds limit')
        remaining = deadline - time.monotonic()
        if remaining <= 0 or proc.wait(timeout=max(remaining, 0.001)) != 0:
            raise ExtractionError('decoder failed')
        return bytes(out)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ExtractionError('decoder failed or timed out') from exc
    finally:
        selector.close()
        # Kill the whole group even after the leader exits: descendants cannot linger.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.close()
        if proc.stdout and not proc.stdout.closed:
            proc.stdout.close()


def _extract_in_preisolated_worker(data: bytes, *, consent: bool) -> Sample:
    """TEST ONLY: requires a pre-isolated disposable single-thread worker.

    Does not verify or create isolation. Never call from production/chat. Only
    synthetic data may be used until the sandbox and duration policy are proven.
    """
    if consent is not True or type(data) is not bytes or not (len(_MAGIC) <= len(data) <= MAX_INPUT):
        raise ExtractionError('explicit consent and bounded bytes required')
    if not data.startswith(_MAGIC):
        raise ExtractionError('only NUT is accepted')
    common = ['-v', 'error', '-protocol_whitelist', 'pipe',
              '-codec_whitelist', 'mpeg4,pcm_s16le', '-f', 'nut']
    raw = _run(['/usr/bin/ffprobe', '-max_pixels', str(MAX_PIXELS), *common, '-show_entries',
                'stream=index,codec_type,codec_name,width,height', '-of', 'json', 'pipe:0'], data, MAX_PROBE)
    try:
        streams = json.loads(raw)['streams']
        if (len(streams) != 2 or [s['codec_type'] for s in streams] != ['video', 'audio']
                or [s['codec_name'] for s in streams] != ['mpeg4', 'pcm_s16le']):
            raise ValueError('exactly one MPEG-4 video and PCM audio stream required')
        w, h = streams[0]['width'], streams[0]['height']
        if type(w) is not int or type(h) is not int or not (1 <= w <= 640 and 1 <= h <= 360 and w*h <= MAX_PIXELS):
            raise ValueError('dimensions outside policy')
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ExtractionError('invalid metadata') from exc
    base = ['/usr/bin/ffmpeg', '-nostdin', '-threads', '1', '-filter_threads', '1',
            '-max_pixels', str(MAX_PIXELS), *common, '-i', 'pipe:0']
    frame = _run([*base, '-map', '0:v:0', '-an', '-frames:v', '1', '-threads', '1',
                  '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1'], data, w*h*3)
    audio = _run([*base, '-map', '0:a:0', '-vn', '-t', '1', '-ac', '1', '-ar', '16000',
                  '-c:a', 'pcm_s16le', '-f', 's16le', 'pipe:1'], data, MAX_AUDIO)
    if len(frame) != w*h*3 or not audio or len(audio) % 2:
        raise ExtractionError('incomplete samples')
    return Sample(w, h, frame, audio)
