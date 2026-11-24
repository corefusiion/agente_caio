"""Cross-process directory lock: recovery must wait for live writers."""
import os
import select
import subprocess
import sys
from pathlib import Path

import pytest

from caiocore.server.google_oauth_local import PrivateTokenStore

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='POSIX only')
ROOT = str(Path(__file__).resolve().parents[1])


def test_process_recovery_waits_for_writer(tmp_path):
    tmp_path.chmod(0o700)
    writer = '''
import sys
from caiocore.server import google_oauth_local as m
s = m.PrivateTokenStore(sys.argv[1])
replace = m.os.replace
def wait(*a, **k):
    print('writing', flush=True)
    assert sys.stdin.readline() == 'go\\n'
    return replace(*a, **k)
m.os.replace = wait
s.save({'refresh_token':'synthetic', 'scopes':[m.SCOPES['calendar_read']]})
print('saved', flush=True)
'''
    reader = '''
import sys
from caiocore.server.google_oauth_local import PrivateTokenStore
print('starting', flush=True)
with PrivateTokenStore(sys.argv[1]) as s:
    print('loaded:' + s.load()['refresh_token'], flush=True)
'''
    a = subprocess.Popen([sys.executable, '-u', '-c', writer, str(tmp_path)], cwd=ROOT,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, bufsize=1)
    b = None
    try:
        assert a.stdout.readline().strip() == 'writing'
        b = subprocess.Popen([sys.executable, '-u', '-c', reader, str(tmp_path)], cwd=ROOT,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        assert b.stdout.readline().strip() == 'starting'
        assert not select.select([b.stdout], [], [], 0.2)[0]
        assert len(list(tmp_path.glob('.google-token-*'))) == 1
        a.stdin.write('go\n')
        a.stdin.flush()
        out_a, err_a = a.communicate(timeout=5)
        out_b, err_b = b.communicate(timeout=5)
        assert a.returncode == 0, err_a
        assert b.returncode == 0, err_b
        assert 'saved' in out_a
        assert 'loaded:synthetic' in out_b
        assert not list(tmp_path.glob('.google-token-*'))
        with PrivateTokenStore(tmp_path) as s:
            s.delete_local()
    finally:
        for p in (a, b):
            if p is not None and p.poll() is None:
                p.kill()
                p.communicate(timeout=5)
