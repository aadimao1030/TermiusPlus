"""Local filesystem browsing, bounded previews and recoverable mutations."""
import os
from pathlib import Path

def file_preview(path):
    """Read only regular files, with bounded text and image payloads."""
    import os, stat, base64, codecs
    text_limit, image_limit = 256 * 1024, 8 * 1024 * 1024
    path = os.path.realpath(os.path.expanduser(path))
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        st = os.fstat(stream.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise ValueError('只能预览普通文件')
        data = stream.read(text_limit + 1)
        mime = None
        if data.startswith(b'\x89PNG\r\n\x1a\n'):
            mime = 'image/png'
        elif data.startswith(b'\xff\xd8\xff'):
            mime = 'image/jpeg'
        elif data.startswith((b'GIF87a', b'GIF89a')):
            mime = 'image/gif'
        elif data.startswith(b'RIFF') and data[8:12] == b'WEBP':
            mime = 'image/webp'
        result = dict(path=path, size=st.st_size, name=os.path.basename(path))
        if mime:
            if st.st_size > image_limit:
                return dict(result, kind='unsupported', message='图片超过 8 MiB，请传到本地后查看')
            data += stream.read(image_limit + 1 - len(data))
            if len(data) > image_limit:
                return dict(result, kind='unsupported', message='图片超过 8 MiB，请传到本地后查看')
            return dict(result, kind='image', mime=mime, content=base64.b64encode(data).decode('ascii'))
        truncated = len(data) > text_limit
        data = data[:text_limit]
        encoding = 'utf-8-sig'
        if data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
            encoding = 'utf-32'
        elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            encoding = 'utf-16'
        try:
            text = codecs.getincrementaldecoder(encoding)().decode(data, final=not truncated)
            if '\x00' in text or any(ord(c) < 32 and c not in '\t\r\n' for c in text):
                raise ValueError('binary')
        except (UnicodeError, ValueError):
            return dict(result, kind='unsupported', message='暂不支持该二进制文件或文本编码。支持 UTF-8 和带 BOM 的 UTF-16/32 文本，以及 PNG、JPEG、GIF、WebP 图片。')
        return dict(result, kind='text', content=text, encoding=encoding, truncated=truncated)

def file_operation(root, path, operation, name=None, destination=None):
    """Create, rename or move without replacement, or use recoverable trash."""
    import os, uuid, ctypes, sys, errno, stat
    root = os.path.abspath(os.path.expanduser(root))
    path = os.path.abspath(os.path.expanduser(path))
    creating = operation in ('create_file', 'create_folder')
    if (path == root and not creating) or os.path.commonpath([root, path]) != root:
        raise ValueError('只能操作当前目录内的项目')
    if creating or operation == 'rename':
        if not isinstance(name, str) or name in ('', '.', '..') or '/' in name or '\0' in name:
            raise ValueError('名称不能为空，也不能包含 / 或空字符')
    if creating:
        if not os.path.isdir(path):
            raise ValueError('目标文件夹已不存在，请刷新目录')
        target = os.path.join(path, name)
        try:
            if operation == 'create_folder':
                os.mkdir(target)
            else:
                # Exclusive creation also rejects existing or dangling symlinks.
                with open(target, 'xb'):
                    pass
        except FileExistsError:
            raise ValueError('同名项目已存在，不会覆盖')
        return dict(path=target)
    if not os.path.lexists(path):
        raise ValueError('项目已不存在，请刷新目录')
    parent, basename = os.path.split(path)
    if operation in ('rename', 'move'):
        if operation == 'move':
            if not isinstance(destination, str) or not destination:
                raise ValueError('请选择目标文件夹')
            destination = os.path.realpath(os.path.expanduser(destination))
            if not os.path.isdir(destination):
                raise ValueError('目标必须是已存在的文件夹')
            if stat.S_ISDIR(os.lstat(path).st_mode):
                source = os.path.realpath(path)
                if os.path.commonpath([source, destination]) == source:
                    raise ValueError('不能把文件夹移动到自身或其子目录中')
            target = os.path.join(destination, basename)
        else:
            target = os.path.join(parent, name)
        if target == path:
            return dict(path=target)
        if os.path.lexists(target):
            raise ValueError('同名项目已存在，不会覆盖')
        # Prefer atomic no-replace rename. Some shared filesystems reject its
        # flags even when libc and the kernel provide the function.
        libc = ctypes.CDLL(None, use_errno=True)
        error = errno.ENOSYS
        if sys.platform == 'darwin' and hasattr(libc, 'renamex_np'):
            result = libc.renamex_np(os.fsencode(path), os.fsencode(target), 4)
            error = ctypes.get_errno()
        elif sys.platform.startswith('linux') and hasattr(libc, 'renameat2'):
            result = libc.renameat2(-100, os.fsencode(path), -100, os.fsencode(target), 1)
            error = ctypes.get_errno()
        else:
            result = -1
        if result == 0:
            return dict(path=target)
        if error == errno.EXDEV:
            raise ValueError('跨文件系统请先传输，确认成功后再删除源项目')
        if error not in (errno.ENOSYS, errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP):
            raise OSError(error, os.strerror(error))
        if not stat.S_ISDIR(os.lstat(path).st_mode):
            # link() claims the destination exclusively, including symlinks.
            os.link(path, target, follow_symlinks=False)
            linked = os.lstat(target)
            try:
                os.unlink(path)
            except Exception:
                try:
                    current = os.lstat(target)
                    if (current.st_dev, current.st_ino) == (linked.st_dev, linked.st_ino):
                        os.unlink(target)
                except OSError:
                    pass
                raise
        else:
            # Directories cannot be hard-linked. Reserve an empty destination
            # before ordinary rename so competing creates fail rather than
            # overwriting a pre-existing directory on NFS/parallel storage.
            os.mkdir(target, 0o700)
            reserved = os.lstat(target)
            try:
                os.rename(path, target)
            except Exception:
                try:
                    current = os.lstat(target)
                    if (current.st_dev, current.st_ino) == (reserved.st_dev, reserved.st_ino):
                        os.rmdir(target)
                except OSError:
                    pass
                raise
        return dict(path=target)
    if operation == 'trash':
        trash = os.path.join(parent, '.termiusplus-trash')
        if os.path.lexists(trash) and (os.path.islink(trash) or not os.path.isdir(trash)):
            raise ValueError('回收目录不是普通文件夹，无法安全删除')
        os.makedirs(trash, mode=0o700, exist_ok=True)
        container = os.path.join(trash, uuid.uuid4().hex)
        os.mkdir(container, 0o700)
        target = os.path.join(container, basename)
        try:
            os.rename(path, target)
        except Exception:
            os.rmdir(container)
            raise
        return dict(path=target, original=path)
    raise ValueError('不支持的文件操作')

def trash_entries(root, paths):
    """Validate a selection before recoverable deletion; report partial failures."""
    import os
    root = os.path.abspath(os.path.expanduser(root))
    if not isinstance(paths, list) or not 1 <= len(paths) <= 500:
        raise ValueError('请选择 1–500 个项目删除')
    selected = set()
    for path in paths:
        if not isinstance(path, str) or not path:
            raise ValueError('删除路径不正确')
        path = os.path.abspath(os.path.expanduser(path))
        if path == root or os.path.commonpath([root, path]) != root:
            raise ValueError('只能操作当前目录内的项目')
        if not os.path.lexists(path):
            raise ValueError('项目已不存在，请刷新目录')
        selected.add(path)
    selected = sorted(path for path in selected if not any(
        path.startswith(parent.rstrip('/') + '/') for parent in selected if parent != path))
    for path in selected:
        trash = os.path.join(os.path.dirname(path), '.termiusplus-trash')
        if os.path.lexists(trash) and (os.path.islink(trash) or not os.path.isdir(trash)):
            raise ValueError('回收目录不是普通文件夹，无法安全删除')
    trashed = []
    for path in selected:
        try:
            trashed.append(file_operation(root, path, 'trash'))
        except Exception as error:
            return dict(trashed=trashed, failed=path, error=str(error))
    return dict(trashed=trashed)

def move_entries(root, paths, destination):
    """Preflight an entire selection before moving; report any partial failure."""
    import os, stat
    root = os.path.abspath(os.path.expanduser(root))
    if not isinstance(paths, list) or not 1 <= len(paths) <= 500:
        raise ValueError('请选择 1–500 个项目移动')
    if not isinstance(destination, str) or not destination:
        raise ValueError('请选择目标文件夹')
    destination = os.path.realpath(os.path.expanduser(destination))
    if not os.path.isdir(destination):
        raise ValueError('目标必须是已存在的文件夹')
    selected = set()
    for path in paths:
        if not isinstance(path, str) or not path:
            raise ValueError('移动路径不正确')
        path = os.path.abspath(os.path.expanduser(path))
        if path == root or os.path.commonpath([root, path]) != root:
            raise ValueError('只能操作当前目录内的项目')
        if not os.path.lexists(path):
            raise ValueError('项目已不存在，请刷新目录')
        selected.add(path)
    # Moving a selected parent also moves its selected children.
    selected = sorted(path for path in selected if not any(
        path.startswith(parent.rstrip('/') + '/') for parent in selected if parent != path))
    targets = set()
    plan = []
    for path in selected:
        target = os.path.join(destination, os.path.basename(path))
        if target in targets:
            raise ValueError('所选项目有同名项，无法移动到同一文件夹')
        targets.add(target)
        if stat.S_ISDIR(os.lstat(path).st_mode) and os.path.commonpath([os.path.realpath(path), destination]) == os.path.realpath(path):
            raise ValueError('不能把文件夹移动到自身或其子目录中')
        if os.path.realpath(os.path.dirname(path)) == destination:
            continue
        if os.path.lexists(target):
            raise ValueError('同名项目已存在，不会覆盖：' + os.path.basename(path))
        if os.lstat(path).st_dev != os.stat(destination).st_dev:
            raise ValueError('跨文件系统请先传输，确认成功后再删除源项目')
        plan.append(path)
    moved = []
    for path in plan:
        try:
            result = file_operation(root, path, 'move', destination=destination)
            moved.append(dict(original=path, path=result['path']))
        except Exception as error:
            return dict(moved=moved, error=str(error), failed=path)
    return dict(moved=moved)

def local_info(path):
    root = Path(path).expanduser().resolve(strict=True)
    entries = []
    with os.scandir(root) as scan:
        for e in scan:
            try:
                entries.append(dict(name=e.name, path=e.path, directory=e.is_dir(follow_symlinks=True), symlink=e.is_symlink(), size=e.stat(follow_symlinks=False).st_size))
            except OSError:
                continue
    entries.sort(key=lambda e: (not e['directory'], e['name'].casefold()))
    import platform, hashlib
    try:
        machine = Path('/etc/machine-id').read_text().strip()
    except OSError:
        machine = platform.node()
    account = hashlib.sha256((machine + '\0' + str(os.geteuid())).encode()).hexdigest()
    return dict(path=str(root), entries=entries, machine=hashlib.sha256(machine.encode()).hexdigest(), account=account)
