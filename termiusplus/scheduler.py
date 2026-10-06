"""Reserve physical read/write paths; overlapping writers wait in submission order."""
import os
import posixpath
import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class Resource:
    namespace: str
    path: str
    write: bool


def conflicts(left, right):
    for a in left:
        for b in right:
            same = a.namespace == b.namespace or (a.namespace == 'remote:*' and b.namespace.startswith('remote:')) or (b.namespace == 'remote:*' and a.namespace.startswith('remote:'))
            if same and (a.write or b.write):
                if a.path == b.path or a.path.startswith(b.path.rstrip('/') + '/') or b.path.startswith(a.path.rstrip('/') + '/'):
                    return True
    return False


def resources(options, remote_resolver):
    """Selections reserve their own subtree; packing reserves its output directory."""
    direction = options['direction']
    local = lambda path: dict(kind='local', path=path)
    remote = lambda path: dict(kind='remote', path=path, routes=options['routes'])
    if direction in ('relay', 'remote'):
        source, target = options['source'], options['destination']
    elif direction == 'upload':
        source, target = local(options['local']), remote(options['remote'])
    elif direction == 'download':
        source, target = remote(options['remote']), local(options['local'])
    elif direction == 'copy':
        source, target = local(options['local']), local(options['destination'])
    else:
        source = remote(options['remote']) if options.get('side') == 'remote' else local(options['local'])
        target = source
    items = options.get('items') or ['']
    packed = options.get('pack') or direction == 'pack'
    writes = [''] if packed else [posixpath.basename(item) if options.get('flattenItems') else item for item in items]

    def resolve(endpoint, selected, write):
        if endpoint['kind'] == 'local':
            root = os.path.abspath(os.path.expanduser(endpoint['path']))
            return [Resource('local', path, write) for item in selected
                    for path in {os.path.normpath(os.path.join(root, item)), os.path.realpath(os.path.join(root, item))}]
        try:
            machine, paths = remote_resolver(endpoint, selected)
            return [Resource('remote:' + machine, path, write) for path in paths]
        except Exception:
            # An unavailable/old endpoint cannot prove it is independent of another IP.
            return [Resource('remote:*', '/', write)]

    result = resolve(source, items, False) + resolve(target, writes, True)
    if packed:
        result += resolve(source, [''], True)
    if options.get('_relayDirectory'):
        result.append(Resource('local', os.path.realpath(options['_relayDirectory']), True))
    if options.get('stage'):
        result.append(Resource('local', os.path.realpath(options['local']), True))
    return result


class TransferScheduler:
    def __init__(self, resolver, checkpoint, lock=None):
        self.resolver, self.checkpoint = resolver, checkpoint
        self.condition = threading.Condition(lock or threading.RLock())
        self.entries = []
        self.closing = False

    def submit(self, job, attached=False):
        with self.condition:
            entry = dict(job=job, resources=None, running=attached)
            self.entries.append(entry)
            if not attached:
                job.state = 'queued'
                job.queue_reason = '正在核对读写路径'
            self.checkpoint()
        thread = threading.Thread(target=self._run, args=(entry, attached), daemon=True)
        thread.start()
        return thread

    def _run(self, entry, attached):
        job = entry['job']
        try:
            reservation = self.resolver(job.options)
            waited = recheck = False
            while True:
                if recheck:
                    reservation = self.resolver(job.options)
                    recheck = False
                with self.condition:
                    entry['resources'] = reservation
                    self.condition.notify_all()
                    if attached:
                        break
                    if self.closing or job.cancel.is_set():
                        return
                    earlier = [e for e in self.entries[:self.entries.index(entry)] if e['running'] or not e['job'].cancel.is_set()]
                    blockers = [e for e in earlier if e['resources'] is None or conflicts(reservation, e['resources'])]
                    # Attached sessions are already running and cannot wait for younger jobs.
                    blockers += [e for e in self.entries[self.entries.index(entry)+1:] if e['running'] and (e['resources'] is None or conflicts(reservation, e['resources']))]
                    if not blockers:
                        if waited:
                            # A preceding writer may have replaced a directory symlink.
                            waited = False
                            recheck = True
                            entry['resources'] = None
                            continue
                        entry['running'] = True
                        job.state = 'checking'
                        job.queue_reason = ''
                        break
                    ids = list(dict.fromkeys(e['job'].id for e in blockers))
                    reason = '等待冲突任务：' + '、'.join(ids)
                    if reason != job.queue_reason:
                        job.queue_reason = reason
                        job.event(reason)
                        self.checkpoint()
                    waited = True
                    self.condition.wait(.25)
            if not self.closing:
                job.run()
        except Exception as exc:
            job.state = 'failed'
            job.error = str(exc)
            job.event(job.error)
        finally:
            with self.condition:
                if job.cancel.is_set() and not entry['running']:
                    job.state = 'cancelled'
                job.queue_reason = ''
                self.entries.remove(entry)
                self.checkpoint()
                self.condition.notify_all()

    def cancel(self, job):
        with self.condition:
            job.cancel.set()
            entry = next((e for e in self.entries if e['job'] is job), None)
            if entry and not entry['running']:
                job.state = 'cancelled'
                job.queue_reason = ''
            self.condition.notify_all()

    def close(self):
        with self.condition:
            self.closing = True
            self.condition.notify_all()
