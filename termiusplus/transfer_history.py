"""Atomic local checkpoints for resumable transfers; no remote writes on restore."""
import copy
import hashlib
import json
import os
import time
from pathlib import Path

TERMINAL = ('completed', 'failed', 'cancelled', 'interrupted')


def transfer_key(options):
    direction = options.get('direction')
    keys = ['direction', 'items', 'pack', 'stage', 'executor', 'remoteTarget']
    keys += ['source', 'destination'] if direction in ('relay','remote') else ['local', 'remote', 'destination', 'routes']
    # Route order is meaningful: the first route anchors server identity.
    value = {key: options[key] for key in keys if key in options}
    value['pack'] = bool(options.get('pack', False))
    if direction not in ('relay','remote'):
        value['routes'] = options.get('routes', [])
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def resumable(job):
    return (job.state in ('failed', 'cancelled', 'interrupted') and
            job.options.get('direction') != 'pack')


def resumed_options(job):
    options = copy.deepcopy(job.options)
    for key in ('_remoteId','_remoteConfig','_remoteSubmitted'):
        options.pop(key,None)
    if options.get('stage') and not Path(options['local']).is_dir():
        raise ValueError('拖入文件的暂存已不存在，请重新拖入')
    return options


def save(path, jobs, lock):
    with lock:
        records = []
        for job in jobs.values():
            if job.options.get('_legacy'):
                records.append({'snapshot': job.snapshot(), 'options': job.options})
            else:
                records.append({'snapshot': job.snapshot(), 'options': copy.deepcopy(job.options),
                                'key': job.transfer_key})
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_suffix('.tmp')
        with temporary.open('w') as output:
            os.chmod(temporary, 0o600)
            json.dump(records, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)


def forget(path, identifier):
    """Remove a legacy controller record without touching transfer data."""
    path = Path(path)
    if not path.exists():
        return
    records = json.loads(path.read_text())
    remaining = [r for r in records if r['snapshot']['id'] != identifier]
    if len(remaining) == len(records):
        return
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as output:
        os.chmod(temporary, 0o600)
        json.dump(remaining, output, ensure_ascii=False)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def restore(path, job_class, relay_class, remote_class=None):
    path = Path(path)
    if not path.exists():
        return {}
    records = json.loads(path.read_text())
    jobs = {}
    for record in records:
        options = record['options']; snapshot = record['snapshot']
        job = (remote_class if options['direction']=='remote' and remote_class else relay_class if options['direction'] == 'relay' else job_class)(options)
        job.id = snapshot['id']; job.transfer_key = record.get('key', job.transfer_key)
        for name in ('state', 'route', 'error', 'switches', 'started', 'progress', 'result'):
            if name in snapshot:
                setattr(job, name, snapshot[name])
        job.log.extend(snapshot.get('log', []))
        job.restored_snapshot = snapshot
        if job.state not in TERMINAL and not (options['direction']=='remote' and options.get('_remoteId')):
            job.state = 'interrupted'
            job.event('上次服务中断，未完成数据已保留，可点击续传')
        jobs[job.id] = job
    return jobs
