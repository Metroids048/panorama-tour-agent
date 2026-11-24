"""Maintain existing sandbox exports without starting or restarting any task."""
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import time


def run(workspace, evidence):
    spec = importlib.util.spec_from_file_location('tour_retention', workspace / 'retention.py')
    retention = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(retention)
    with (workspace / '.retention-runner.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'busy'}
        docker = next((str(p) for p in [Path.home() / '.docker/bin/docker',
                                      Path('/opt/homebrew/bin/docker'), Path('/usr/local/bin/docker')]
                       if p.is_file()), shutil.which('docker'))
        if not docker:
            raise OSError('Docker executable unavailable')
        env = dict(os.environ)
        socket = Path.home() / '.docker/run/docker.sock'
        if socket.exists():
            env.setdefault('DOCKER_HOST', 'unix://' + str(socket))

        def command(arguments, timeout=20):
            return subprocess.run([docker, *arguments], check=True, capture_output=True,
                                  text=True, timeout=timeout, env=env).stdout

        containers = command(['ps', '--filter', 'ancestor=space-preview-sandbox:1', '--format', '{{.ID}}']).split()
        found = []
        for container in containers:
            mounts = json.loads(command(['inspect', '--format', '{{json .Mounts}}', container]))
            if any(m.get('Destination') == '/workspace' and
                   Path(m.get('Source', '')).resolve() == workspace.resolve() for m in mounts):
                found.append(container)
        if len(found) != 1:
            result = dict(time=time.time(), status='skipped', reason='existing sandbox not uniquely available')
        else:
            # The publish lock lives in this Linux kernel. Never substitute a host flock.
            protect = []
            for path in retention.opened_files():
                try:
                    relative = Path(path).relative_to(workspace.resolve())
                except ValueError:
                    continue
                if relative.suffix == '.html':
                    protect.extend(['--protect-open-file', '/workspace/' + relative.as_posix()])
            command(['exec', found[0], 'python3', '/workspace/retention.py', '--workspace', '/workspace',
                     '--apply', '--copy-root', '/workspace/evidence', *protect], timeout=240)
            sandbox = json.loads((workspace / 'retention-last-run.json').read_text())
            obsolete = set(sandbox['obsolete_copy_sha256'])
            # Recheck latest/pins before touching host-only QA copies.
            protected = set()
            for root in (workspace / 'projects').iterdir():
                if not root.is_dir():
                    continue
                rows = retention.records(root)
                try:
                    pins = retention.pins(root, rows)
                except (OSError, ValueError, KeyError, TypeError):
                    pins = {b.name for b, _, _ in rows}
                protected.update(r['sha256'] for b, r, _ in rows if b.name in pins)
            copies = retention.clean_copies(workspace, evidence, obsolete, protected, retention.GRACE, True)
            result = dict(time=time.time(), status='ok', container=found[0],
                          interval_seconds=300, grace_seconds=retention.GRACE,
                          projects=len(sandbox['projects']),
                          busy_projects=[p['project'] for p in sandbox['projects'] if p['status'] == 'busy'],
                          skipped_projects=[p['project'] for p in sandbox['projects'] if p['status'] not in ('ok', 'busy')],
                          deleted_files=sandbox['deleted_files'] + copies['deleted_files'],
                          logical_bytes=sandbox['logical_bytes'] + copies['logical_bytes'],
                          host_copies=copies)
        retention.write_json(workspace / 'retention-maintenance-last-run.json', result)
        return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--evidence-root', action='append', default=[], type=Path)
    args = parser.parse_args()
    # launchd opens these afresh each invocation; bound both logs to two files.
    for name in ('retention-maintenance.log', 'retention-maintenance.error.log'):
        log = args.workspace / name
        if log.exists() and log.stat().st_size > 1024 * 1024:
            log.replace(log.with_suffix('.previous.log'))
    try:
        report = run(args.workspace, args.evidence_root)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        report = dict(time=time.time(), status='error', error=type(error).__name__)
        # Never emit docker stderr, environment or credentials into maintenance logs.
        path = args.workspace / 'retention-maintenance-last-run.json'
        path.write_text(json.dumps(report))
        print(json.dumps(report))
        raise SystemExit(1)
    print(json.dumps(report, ensure_ascii=False))
