"""The only media fixture here is generated from scratch, offline."""
from dataclasses import replace
import builtins
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
from unittest.mock import patch

import pytest

from caiocore.video_analysis import MediaMetadata, PlanError, make_plan
from caiocore.video_analysis.planning import MAX_FILE_BYTES, MAX_FRAMES


def metadata(**kwargs):
    return replace(MediaMetadata(1000, 12.0, 120, 640, 360, True), **kwargs)


def test_pure_plan_never_extracts_or_claims_a_summary():
    with patch.object(builtins, 'open', side_effect=AssertionError('file access')), \
         patch.object(os, 'open', side_effect=AssertionError('file access')), \
         patch.object(socket, 'socket', side_effect=AssertionError('network')), \
         patch.object(subprocess, 'Popen', side_effect=AssertionError('process')):
        p = make_plan(metadata(), consent=True)
    assert p.frame_times_seconds == pytest.approx((1.2, 3.6, 6.0, 8.4, 10.8))
    assert len(p.audio_windows) == 3
    assert all(w.duration_seconds <= 3 and 0 <= w.start_seconds and
               w.start_seconds + w.duration_seconds <= 12 for w in p.audio_windows)
    assert not p.metadata_verified and not p.extraction_performed and not p.inference_performed
    assert p.summary is p.transcript is p.task_suggestions is None


def test_short_video_and_absent_audio():
    p = make_plan(metadata(duration_seconds=0.001, frame_count=1, has_audio=False), consent=True)
    assert p.frame_times_seconds == (0.0005,)
    assert p.audio_windows == ()


@pytest.mark.parametrize('consent', [False, None, 1, 'yes'])
def test_consent_must_be_exact_boolean_true(consent):
    with pytest.raises(PlanError):
        make_plan(metadata(), consent=consent)


@pytest.mark.parametrize('changes', [
    {'file_bytes': 0}, {'file_bytes': MAX_FILE_BYTES + 1}, {'file_bytes': True},
    {'duration_seconds': 0}, {'duration_seconds': .00001},
    {'duration_seconds': 7201}, {'duration_seconds': float('inf')},
    {'duration_seconds': float('nan')}, {'duration_seconds': True},
    {'duration_seconds': '12'}, {'duration_seconds': 10**500},
    {'frame_count': 0}, {'frame_count': MAX_FRAMES + 1}, {'frame_count': 1.2},
    {'width': 0}, {'height': 4097}, {'width': 4096, 'height': 2161},
    {'has_audio': 1},
])
def test_untrusted_values_fail_closed(changes):
    with pytest.raises(PlanError):
        make_plan(metadata(**changes), consent=True)


def test_paths_and_urls_are_not_accepted():
    for invalid in ('/tmp/video.mp4', 'https://example.invalid/video', None, {}):
        with pytest.raises(PlanError):
            make_plan(invalid, consent=True)


def test_generated_synthetic_metadata_can_be_planned_offline():
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        pytest.skip('ffmpeg and ffprobe required for synthetic fixture only')
    # A known test source, never a user file or arbitrary URL. No shell.
    # TemporaryDirectory cleans up on success and exceptions.
    env = {'PATH': os.defpath, 'LANG': 'C', 'HOME': '/nonexistent'}
    with tempfile.TemporaryDirectory(prefix='caio-video-synthetic-') as temp:
        root = Path(temp)
        video = root / 'synthetic.mp4'
        subprocess.run([ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error',
                        '-f', 'lavfi', '-i', 'color=c=blue:s=64x48:r=5:d=1',
                        '-frames:v', '5', '-c:v', 'mpeg4', '-y', str(video)],
                       check=True, timeout=15, env=env, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        result = subprocess.run([ffprobe, '-v', 'error', '-protocol_whitelist', 'file,pipe',
                                 '-f', 'mov', '-count_frames', '-show_entries',
                                 'stream=width,height,nb_read_frames:format=duration,size',
                                 '-of', 'json', str(video)], check=True, timeout=15,
                                env=env, stdin=subprocess.DEVNULL, capture_output=True)
        data = json.loads(result.stdout)
        stream = data['streams'][0]
        p = make_plan(MediaMetadata(int(data['format']['size']), float(data['format']['duration']),
                                    int(stream['nb_read_frames']), int(stream['width']),
                                    int(stream['height']), False), consent=True)
        assert len(p.frame_times_seconds) == 5
        assert all(0 < t < 1 for t in p.frame_times_seconds)
        assert not p.extraction_performed and p.summary is None
    assert not root.exists()
