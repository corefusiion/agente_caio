"""Synthetic media only. No filesystem media, network or user data."""
import os
import shutil
import subprocess
import sys

import pytest

from caiocore.video_analysis import experimental_extract as exp


def test_public_api_fails_closed_without_attempting_io(monkeypatch):
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('process started'))
    for data in (b'nut/multimedia container\0', b'', '/etc/passwd', None):
        with pytest.raises(exp.ExtractionError, match='disabled'):
            exp.extract_local(data, consent=True)


@pytest.mark.parametrize('data,consent', [
    (b'', True), (b'wrong', True), (b'nut/multimedia container\0', False),
    (b'nut/multimedia container\0', 1), ('/tmp/example', True),
    (b'nut/multimedia container\0' + b'x' * exp.MAX_INPUT, True),
])
def test_worker_rejects_bad_data_before_spawning(monkeypatch, data, consent):
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('process started'))
    with pytest.raises(exp.ExtractionError):
        exp._extract_in_preisolated_worker(data, consent=consent)


def test_output_cap_and_wall_timeout(monkeypatch):
    python = shutil.which('python3')
    if not python or sys.platform != 'linux':
        pytest.skip('Linux Python required')
    with pytest.raises(exp.ExtractionError, match='output exceeds'):
        exp._run([python, '-c', 'import sys; sys.stdout.buffer.write(b"x"*100000)'], b'x', 10)
    monkeypatch.setattr(exp, '_TIMEOUT', 0.3)
    with pytest.raises(exp.ExtractionError, match='timeout'):
        exp._run([python, '-c', 'import time; time.sleep(10)'], b'x', 10)


# Child gets its own user/network namespaces. Landlock denies filesystem writes
# and reads except dynamic libraries, loader cache and the two executables. Only
# fixed lavfi-generated data enters the private worker. No security attestation.
_CHILD = r'''
import ctypes, json, os, resource, socket, subprocess, sys
from caiocore.video_analysis import experimental_extract as exp
assert os.readlink('/proc/self/ns/net') != sys.argv[1]
libc = ctypes.CDLL(None, use_errno=True)
def call(n, *a):
    r = libc.syscall(n, *a)
    if r < 0:
        e = ctypes.get_errno(); raise OSError(e, os.strerror(e))
    return r
abi = call(444, 0, 0, 1)
if abi < 3: sys.exit(77)
class Ruleset(ctypes.Structure):
    _fields_ = [('fs', ctypes.c_uint64)]
class Rule(ctypes.Structure):
    _layout_ = 'ms'
    _pack_ = 1
    _fields_ = [('access', ctypes.c_uint64), ('fd', ctypes.c_int32)]
handled = (1 << 15) - 1
if abi >= 5: handled |= 1 << 15
rsattr = Ruleset(handled)
rs = call(444, ctypes.byref(rsattr), ctypes.sizeof(rsattr), 0)
for path, access in [('/usr/bin/ffmpeg', 5), ('/usr/bin/ffprobe', 5),
                     ('/usr/lib/x86_64-linux-gnu', 12),
                     ('/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2', 5),
                     ('/etc/ld.so.cache', 4), ('/dev/null', 6)]:
    fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
    try:
        rule = Rule(access, fd); call(445, rs, 1, ctypes.byref(rule), 0)
    finally: os.close(fd)
assert libc.prctl(38, 1, 0, 0, 0) == 0
call(446, rs, 0); os.close(rs)
try:
    open('/etc/passwd', 'rb')
    raise AssertionError('outside file readable')
except PermissionError: pass
# No socket connections are made. Namespace has no host network interfaces.
gen = subprocess.run(['/usr/bin/ffmpeg', '-v', 'error', '-f', 'lavfi',
    '-i', 'color=blue:s=64x48:r=5:d=1', '-f', 'lavfi',
    '-i', 'sine=frequency=440:sample_rate=16000:duration=1',
    '-c:v', 'mpeg4', '-c:a', 'pcm_s16le', '-f', 'nut', 'pipe:1'],
    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    env={'PATH':'/usr/bin:/bin', 'LANG':'C', 'HOME':'/nonexistent'}, timeout=15, check=True)
try:
    exp._extract_in_preisolated_worker(b'nut/multimedia container\x00broken', consent=True)
    raise AssertionError('malformed media was accepted')
except exp.ExtractionError:
    pass
s = exp._extract_in_preisolated_worker(gen.stdout, consent=True)
# Verify actual blue RGB pixels and non-silent, full-length PCM samples.
assert (s.width, s.height, len(s.rgb24)) == (64, 48, 64*48*3)
assert s.rgb24[2] > 180 and s.rgb24[0] < 20 and s.rgb24[1] < 20
assert len(s.pcm_s16le) == 32000 and any(s.pcm_s16le)
print(json.dumps({'input_bytes':len(gen.stdout), 'frame_bytes':len(s.rgb24),
                  'audio_bytes':len(s.pcm_s16le), 'landlock_abi':abi}))
'''


def test_real_synthetic_samples_in_os_sandbox():
    if sys.platform != 'linux' or os.uname().machine != 'x86_64':
        pytest.skip('probe implemented for Linux x86_64 only')
    if not all(shutil.which(x) for x in ('unshare', 'ffmpeg', 'ffprobe')):
        pytest.skip('required tools unavailable')
    available = subprocess.run(['unshare', '-Urn', '/usr/bin/true'], capture_output=True, timeout=5)
    if available.returncode:
        pytest.skip('user/network namespace unavailable')
    result = subprocess.run(['unshare', '-Urn', sys.executable, '-c', _CHILD,
                             os.readlink('/proc/self/ns/net')],
                            stdin=subprocess.DEVNULL, capture_output=True, timeout=40,
                            env={'PATH':'/usr/bin:/bin', 'LANG':'C', 'HOME':'/nonexistent',
                                 'PYTHONPATH':os.getcwd(), 'PYTHONDONTWRITEBYTECODE':'1'})
    if result.returncode == 77:
        pytest.skip('Landlock ABI 3+ unavailable')
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    assert b'"audio_bytes": 32000' in result.stdout
