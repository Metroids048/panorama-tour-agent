"""Bounded retention for frozen tour HTML only; never models, media or ZIPs."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

REVISION = re.compile(r'[0-9a-f]{32}')
KEEP = 1
GRACE = 600


def signature(path):
    s = path.stat()
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        stream.write(json.dumps(data, ensure_ascii=False).encode())
    try:
        Path(stream.name).replace(path)
    finally:
        Path(stream.name).unlink(missing_ok=True)


def journal(workspace, row):
    path = workspace / 'retention-log.jsonl'
    # Keep this maintenance log bounded too.
    if path.exists() and path.stat().st_size > 1024 * 1024:
        path.replace(path.with_suffix('.previous.jsonl'))
    with path.open('a') as stream:
        stream.write(json.dumps(dict(time=time.time(), **row), ensure_ascii=False) + '\n')


def opened_files():
    if Path('/proc/self/fd').exists():
        out = set()
        for directory in Path('/proc').glob('[0-9]*/fd'):
            try:
                for fd in directory.iterdir():
                    try:
                        out.add(os.readlink(fd))
                    except OSError:
                        pass
            except OSError:
                pass
        return out
    run = subprocess.run(['lsof', '-nP', '-Fn'], capture_output=True, text=True)
    # A failed inspection must not enable deletion.
    if run.returncode not in (0, 1) or not run.stdout:
        raise OSError('Cannot inspect open files')
    return {str(Path(line[1:]).resolve()) for line in run.stdout.splitlines() if line.startswith('n/')}


def records(root):
    rows = []
    for base in (root / 'versions').glob('*'):
        if not REVISION.fullmatch(base.name) or base.is_symlink():
            continue
        try:
            receipt = json.loads((base / 'receipt.json').read_text())
            if receipt['project'] != root.name or receipt['revision'] != base.name:
                continue
            if not re.fullmatch('[0-9a-f]{64}', receipt['sha256']):
                continue
            rows.append((base, receipt, (base / 'receipt.json').stat().st_mtime_ns))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sorted(rows, key=lambda row: row[2], reverse=True)


def pins(root, rows):
    latest = (root / 'latest').read_text().strip()
    if not REVISION.fullmatch(latest) or not (root / 'versions' / latest / 'tour.html').is_file():
        raise ValueError('Current export is missing; skip retention')
    kept = {base.name for base, _, _ in rows[:KEEP]}
    kept.add(latest)
    file = root / '.retention-pins.json'
    if file.exists():
        values = json.loads(file.read_text())
        if not isinstance(values, list) or not all(isinstance(v, str) and REVISION.fullmatch(v) for v in values):
            raise ValueError('Invalid retention pins')
        kept.update(values)
    # Explicit protections may temporarily exceed one version.
    kept.update(base.name for base, _, _ in rows if (base / '.keep').exists())
    return kept


def unlink_export(path, expected, grace, opened, result, apply):
    if path.is_symlink() or not path.is_file() or str(path.resolve()) in opened:
        return False
    before = signature(path)
    s = path.stat()
    if time.time() - max(s.st_mtime, s.st_ctime) < grace or digest(path) != expected:
        return False
    if signature(path) != before:
        return False
    result['candidates'] += 1
    if apply:
        path.unlink()
        result['deleted_files'] += 1
        result['logical_bytes'] += before[2]
        journal(Path(result['workspace']), {'deleted': str(path), 'sha256': expected,
                                           'logical_bytes': before[2], 'reason': 'obsolete-tour-html'})
    return True


def prune_locked(root, downloads, *, grace=GRACE, apply=True, opened=None):
    """Caller holds the same .publish.lock as Project.publish."""
    root = Path(root)
    rows = records(root)
    kept = pins(root, rows)
    opened = opened_files() if opened is None else opened
    result = dict(project=root.name, workspace=str(root.parent.parent), status='ok',
                  kept=sorted(kept), deleted_files=0, logical_bytes=0, candidates=0,
                  obsolete_sha256=[], kept_sha256=[r['sha256'] for b, r, _ in rows if b.name in kept])
    obsolete = {}
    for base, receipt, _ in rows:
        if base.name in kept:
            continue
        path = base / 'tour.html'
        if (not path.exists() and not path.is_symlink()) or unlink_export(
                path, receipt['sha256'], grace, opened, result, apply):
            obsolete[base.name] = receipt['sha256']
    # Only indexed HTML downloads for this task/revision are eligible.
    for meta in Path(downloads).glob('*.json'):
        if not REVISION.fullmatch(meta.stem) or meta.is_symlink():
            continue
        try:
            item = json.loads(meta.read_text())
            if item.get('project') != root.name or obsolete.get(item.get('revision')) != item.get('sha256'):
                continue
            if unlink_export(meta.with_suffix('.html'), item['sha256'], grace, opened, result, apply) and apply:
                meta.unlink()
        except (OSError, ValueError, KeyError, TypeError):
            continue
    result['obsolete_sha256'] = sorted(set(obsolete.values()))
    return result


def prune_project(root, downloads, *, grace=GRACE, apply=True, opened=None):
    root = Path(root)
    if root.is_symlink():
        return dict(project=root.name, status='symlink', deleted_files=0, logical_bytes=0)
    with (root / '.publish.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return dict(project=root.name, status='busy', deleted_files=0, logical_bytes=0)
        return prune_locked(root, downloads, grace=grace, apply=apply, opened=opened)


def ensure_download(source, downloads):
    """Same saved version gets one stable URL, across retries and resumes."""
    source, downloads = Path(source), Path(downloads)
    receipt = json.loads(source.with_name('receipt.json').read_text())
    if source.name != 'tour.html' or source.parent.name != receipt['revision'] or source.parents[2].name != receipt['project']:
        raise ValueError('Download must belong to its frozen version')
    if digest(source) != receipt['sha256']:
        raise ValueError('Frozen HTML checksum mismatch')
    token = hashlib.sha256(('%s/%s/%s' % (receipt['project'], receipt['revision'], receipt['sha256'])).encode()).hexdigest()[:32]
    downloads.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = downloads / (token + '.html')
    with (downloads / '.retention.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if target.is_symlink():
            raise ValueError('Unexpected download symlink')
        if not target.exists():
            try:
                os.link(source, target)
            except OSError:
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(dir=downloads, delete=False) as stream:
                        temporary = Path(stream.name)
                        with source.open('rb') as incoming:
                            for block in iter(lambda: incoming.read(4 * 1024 * 1024), b''):
                                stream.write(block)
                    temporary.replace(target)
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
        elif digest(target) != receipt['sha256']:
            raise ValueError('Existing download checksum mismatch')
        write_json(target.with_suffix('.json'), {k: receipt[k] for k in ('project', 'revision', 'sha256')})
    return target


def index_legacy(workspace, downloads):
    """One-time content check for pre-policy random-token downloads."""
    by_sha = {}
    for root in (workspace / 'projects').iterdir():
        for _, receipt, _ in records(root):
            by_sha.setdefault(receipt['sha256'], []).append(receipt)
    for html in downloads.glob('*.html'):
        if html.is_symlink() or not REVISION.fullmatch(html.stem) or html.with_suffix('.json').exists():
            continue
        stamp = signature(html)
        candidates = by_sha.get(digest(html), [])
        if len(candidates) == 1 and signature(html) == stamp:
            write_json(html.with_suffix('.json'), {k: candidates[0][k] for k in ('project', 'revision', 'sha256')})


def clean_copies(workspace, roots, obsolete, kept, grace, apply, protected_files=()):
    opened = opened_files() | set(protected_files)
    cache_file = workspace / 'retention-copy-index.json'
    try:
        cache = json.loads(cache_file.read_text())
    except (OSError, ValueError):
        cache = {}
    current = {}
    result = dict(workspace=str(workspace), deleted_files=0, logical_bytes=0, candidates=0)
    for root in roots:
        root = Path(root)
        if root.is_symlink() or not root.is_dir():
            continue
        for path in root.rglob('*.html'):
            if path.is_symlink() or not path.is_file() or str(path.resolve()) in opened:
                continue
            stamp = list(signature(path))
            entry = cache.get(str(path), {})
            value = entry.get('sha256') if entry.get('signature') == stamp else digest(path)
            current[str(path)] = dict(signature=stamp, sha256=value)
            if value in obsolete and value not in kept and unlink_export(path, value, grace, opened, result, apply):
                if apply:
                    current.pop(str(path))
    if apply:
        write_json(cache_file, current)
    return result


def sweep(workspace, *, grace=GRACE, apply=True, copy_roots=(), protected_files=()):
    workspace = Path(workspace)
    downloads = workspace / 'openclaw-workspace' / '.downloads'
    if apply:
        index_legacy(workspace, downloads)
    opened = opened_files() | set(protected_files)
    reports = []
    for root in (workspace / 'projects').iterdir():
        if not root.is_dir() or not (root / 'latest').is_file():
            continue
        try:
            reports.append(prune_project(root, downloads, grace=grace, apply=apply, opened=opened))
        except (OSError, ValueError, KeyError, TypeError) as error:
            reports.append(dict(project=root.name, status='skipped', error=type(error).__name__, deleted_files=0, logical_bytes=0))
    obsolete = set(x for r in reports for x in r.get('obsolete_sha256', []))
    kept = set(x for r in reports for x in r.get('kept_sha256', []))
    # Busy/invalid projects do not authorize deletion of their evidence copies.
    if any(r['status'] != 'ok' for r in reports):
        for root in (workspace / 'projects').iterdir():
            for _, receipt, _ in records(root):
                if any(r['project'] == root.name and r['status'] != 'ok' for r in reports):
                    kept.add(receipt['sha256'])
    copies = clean_copies(workspace, copy_roots, obsolete, kept, grace, apply, opened) if copy_roots else {}
    result = dict(time=time.time(), policy='latest-one-tour-html', grace_seconds=grace, apply=apply,
                  obsolete_copy_sha256=sorted(obsolete - kept),
                  deleted_files=sum(r['deleted_files'] for r in reports) + copies.get('deleted_files', 0),
                  logical_bytes=sum(r['logical_bytes'] for r in reports) + copies.get('logical_bytes', 0),
                  projects=reports, copies=copies)
    if apply:
        write_json(workspace / 'retention-last-run.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--grace-seconds', type=int, default=GRACE)
    parser.add_argument('--copy-root', action='append', default=[], type=Path)
    parser.add_argument('--protect-open-file', action='append', default=[])
    args = parser.parse_args()
    report = sweep(args.workspace, grace=args.grace_seconds, apply=args.apply, copy_roots=args.copy_root,
                   protected_files=args.protect_open_file)
    print(json.dumps({k: v for k, v in report.items() if k not in ('projects', 'copies', 'obsolete_copy_sha256')}))
