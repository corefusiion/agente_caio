import concurrent.futures
import os
import subprocess
import sys

from caiocore.task_queue import Queue


def test_competing_processes_claim_once(tmp_path):
    path = tmp_path / 'queue'
    path.mkdir(mode=0o700)
    q = Queue(path, 'owner')
    expected = {q.create(f'task {i}')['id'] for i in range(30)}
    code = '''import sys\nfrom caiocore.task_queue import Queue\nq=Queue(sys.argv[1], 'owner')\nwhile True:\n result=q.claim()\n if result is None: break\n print(result[0]['id'], flush=True)\n'''
    def run(_):
        return subprocess.run([sys.executable, '-c', code, str(path)], capture_output=True, text=True,
                              env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), timeout=30)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(run, range(8)))
    assert all(p.returncode == 0 for p in results), [p.stderr for p in results]
    claimed = [task_id for p in results for task_id in p.stdout.splitlines()]
    assert len(claimed) == len(expected) and set(claimed) == expected
    events = q.audit(limit=200)
    assert len([e for e in events if e['action'] == 'reivindicada']) == 30


def test_audit_sequence_is_per_owner(tmp_path):
    path = tmp_path / 'queue'
    path.mkdir(mode=0o700)
    a, b = Queue(path, 'a'), Queue(path, 'b')
    a.create('first'); a.create('second'); b.create('private'); a.create('third')
    assert [e['seq'] for e in a.audit()] == [1, 2, 3]
    assert [e['seq'] for e in b.audit()] == [1]
    assert [e['seq'] for e in a.audit(after=1, limit=1)] == [2]
    assert all('seq' not in row for row in a.list())
