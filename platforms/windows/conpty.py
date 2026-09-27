"""Interactive console sessions via the Windows ConPTY API (stdlib ctypes only).

Windows 10 version 1809 (build 17763) and later, including Windows 11, ship
CreatePseudoConsole in kernel32. No pywinpty (or other third-party package) is
required. Importing this module on other systems raises ImportError; callers
must check os.name first. py_compile still accepts the file everywhere.
"""
import ctypes
import os
import subprocess
from ctypes import wintypes

if os.name != 'nt':
    raise ImportError('platforms.windows.conpty 只能在 Windows 上导入')

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
STILL_ACTIVE = 259
WAIT_TIMEOUT = 0x102
ERROR_BROKEN_PIPE = 109
ERROR_NO_DATA = 232
ERROR_OPERATION_ABORTED = 995


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ('hProcess', wintypes.HANDLE),
        ('hThread', wintypes.HANDLE),
        ('dwProcessId', wintypes.DWORD),
        ('dwThreadId', wintypes.DWORD),
    ]


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ('cb', wintypes.DWORD),
        ('lpReserved', wintypes.LPWSTR),
        ('lpDesktop', wintypes.LPWSTR),
        ('lpTitle', wintypes.LPWSTR),
        ('dwX', wintypes.DWORD),
        ('dwY', wintypes.DWORD),
        ('dwXSize', wintypes.DWORD),
        ('dwYSize', wintypes.DWORD),
        ('dwXCountChars', wintypes.DWORD),
        ('dwYCountChars', wintypes.DWORD),
        ('dwFillAttribute', wintypes.DWORD),
        ('dwFlags', wintypes.DWORD),
        ('wShowWindow', wintypes.WORD),
        ('cbReserved2', wintypes.WORD),
        ('lpReserved2', ctypes.POINTER(wintypes.BYTE)),
        ('hStdInput', wintypes.HANDLE),
        ('hStdOutput', wintypes.HANDLE),
        ('hStdError', wintypes.HANDLE),
    ]


class STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [('StartupInfo', STARTUPINFOW), ('lpAttributeList', ctypes.c_void_p)]


def _coord(cols, rows):
    # COORD is {SHORT X; SHORT Y}. Pass it as a DWORD so ctypes does not drop
    # the struct when the function takes it by value.
    return wintypes.DWORD((cols & 0xFFFF) | ((rows & 0xFFFF) << 16))


def _winerror(message):
    return OSError(ctypes.get_last_error(), message)


def _configure():
    if not hasattr(kernel32, 'CreatePseudoConsole'):
        raise OSError('CreatePseudoConsole 不存在。本地终端需要 Windows 10 1809 或更高版本。')
    kernel32.CreatePseudoConsole.argtypes = [
        wintypes.DWORD, wintypes.HANDLE, wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    kernel32.CreatePseudoConsole.restype = ctypes.c_long
    kernel32.ResizePseudoConsole.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.ResizePseudoConsole.restype = ctypes.c_long
    kernel32.ClosePseudoConsole.argtypes = [wintypes.HANDLE]
    kernel32.ClosePseudoConsole.restype = None
    kernel32.InitializeProcThreadAttributeList.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t)]
    kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    kernel32.UpdateProcThreadAttribute.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t,
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
    kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
    kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    kernel32.DeleteProcThreadAttributeList.restype = None
    kernel32.CreatePipe.argtypes = [
        ctypes.POINTER(wintypes.HANDLE), ctypes.POINTER(wintypes.HANDLE), ctypes.c_void_p, wintypes.DWORD]
    kernel32.CreatePipe.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.ReadFile.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    kernel32.ReadFile.restype = wintypes.BOOL
    kernel32.WriteFile.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    kernel32.WriteFile.restype = wintypes.BOOL
    kernel32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL,
        wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(STARTUPINFOEXW),
        ctypes.POINTER(PROCESS_INFORMATION)]
    kernel32.CreateProcessW.restype = wintypes.BOOL
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD


_configured = False


def _ensure():
    global _configured
    if not _configured:
        _configure()
        _configured = True


def _env_block(env):
    if env is None:
        return None
    parts = ['%s=%s\0' % (key, value) for key, value in env.items()]
    parts.append('\0')
    return ctypes.create_unicode_buffer(''.join(parts))


def _close(handle):
    if handle:
        kernel32.CloseHandle(handle)


class ConPTY:
    """A pseudoconsole attached to one child process."""

    def __init__(self, command, cols, rows, cwd=None, env=None):
        _ensure()
        self.command = list(command)
        self.pid = None
        self._hpcon = None
        self._input_write = None
        self._output_read = None
        self._process = None
        self._closed = False
        self._finished = False
        self._exit = None
        self._spawn(cols, rows, cwd, env)

    def _spawn(self, cols, rows, cwd, env):
        input_read = wintypes.HANDLE()
        input_write = wintypes.HANDLE()
        output_read = wintypes.HANDLE()
        output_write = wintypes.HANDLE()
        hpcon = wintypes.HANDLE()
        attr = None
        if not kernel32.CreatePipe(ctypes.byref(input_read), ctypes.byref(input_write), None, 0):
            raise _winerror('CreatePipe')
        if not kernel32.CreatePipe(ctypes.byref(output_read), ctypes.byref(output_write), None, 0):
            _close(input_read)
            _close(input_write)
            raise _winerror('CreatePipe')
        hr = kernel32.CreatePseudoConsole(_coord(cols, rows), input_read, output_write, 0, ctypes.byref(hpcon))
        _close(input_read)
        _close(output_write)
        if hr < 0:
            _close(input_write)
            _close(output_read)
            raise OSError('CreatePseudoConsole 失败 (HRESULT %s)。本地终端需要 Windows 10 1809 或更高版本。' % hr)
        try:
            size = ctypes.c_size_t(0)
            kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
            if not size.value:
                raise _winerror('InitializeProcThreadAttributeList')
            attr = ctypes.create_string_buffer(size.value)
            attr_ptr = ctypes.cast(attr, ctypes.c_void_p)
            if not kernel32.InitializeProcThreadAttributeList(attr_ptr, 1, 0, ctypes.byref(size)):
                raise _winerror('InitializeProcThreadAttributeList')
            if not kernel32.UpdateProcThreadAttribute(
                    attr_ptr, 0, PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
                    ctypes.byref(hpcon), ctypes.sizeof(hpcon), None, None):
                raise _winerror('UpdateProcThreadAttribute')
            si = STARTUPINFOEXW()
            si.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
            si.lpAttributeList = attr_ptr
            cmdline = ctypes.create_unicode_buffer(subprocess.list2cmdline(self.command))
            env_block = _env_block(env)
            directory = os.fspath(cwd) if cwd else None
            info = PROCESS_INFORMATION()
            flags = EXTENDED_STARTUPINFO_PRESENT
            if env_block is not None:
                flags |= CREATE_UNICODE_ENVIRONMENT
            ok = kernel32.CreateProcessW(
                None, cmdline, None, None, False, flags,
                ctypes.cast(env_block, ctypes.c_void_p) if env_block is not None else None,
                directory, ctypes.byref(si), ctypes.byref(info))
            if not ok:
                raise _winerror('CreateProcessW')
            _close(info.hThread)
            self._hpcon = hpcon
            self._input_write = input_write
            self._output_read = output_read
            self._process = info.hProcess
            self.pid = int(info.dwProcessId)
            hpcon = input_write = output_read = None
        finally:
            if attr is not None:
                kernel32.DeleteProcThreadAttributeList(ctypes.cast(attr, ctypes.c_void_p))
            if hpcon:
                kernel32.ClosePseudoConsole(hpcon)
            _close(input_write)
            _close(output_read)

    def read(self, size):
        if not self._output_read:
            return b''
        buf = ctypes.create_string_buffer(size)
        read = wintypes.DWORD()
        ok = kernel32.ReadFile(self._output_read, buf, size, ctypes.byref(read), None)
        if not ok:
            err = ctypes.get_last_error()
            if err in (ERROR_BROKEN_PIPE, ERROR_NO_DATA, ERROR_OPERATION_ABORTED):
                return b''
            raise OSError(err, 'ReadFile')
        return buf.raw[:read.value]

    def write(self, data):
        if not self._input_write:
            raise OSError('终端已关闭')
        view = memoryview(data)
        while view:
            chunk = bytes(view[:4096])
            buf = ctypes.create_string_buffer(chunk)
            written = wintypes.DWORD()
            ok = kernel32.WriteFile(self._input_write, buf, len(chunk), ctypes.byref(written), None)
            if not ok or not written.value:
                raise OSError(ctypes.get_last_error(), 'WriteFile')
            view = view[written.value:]

    def resize(self, cols, rows):
        if not self._hpcon:
            return
        hr = kernel32.ResizePseudoConsole(self._hpcon, _coord(cols, rows))
        if hr < 0:
            raise OSError('ResizePseudoConsole 失败 (HRESULT %s)' % hr)

    def poll(self):
        if self._finished:
            return self._exit
        if not self._process:
            return None
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(self._process, ctypes.byref(code)):
            return None
        if code.value == STILL_ACTIVE:
            return None
        self._exit = int(code.value)
        self._finished = True
        return self._exit

    def wait(self, timeout=None):
        if self._finished:
            return self._exit
        if not self._process:
            return self.poll()
        ms = 0xFFFFFFFF if timeout is None else max(0, int(timeout * 1000))
        result = kernel32.WaitForSingleObject(self._process, ms)
        if result == WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired(self.command, timeout)
        return self.poll()

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._hpcon:
            kernel32.ClosePseudoConsole(self._hpcon)
            self._hpcon = None
        if self.poll() is None and self._process:
            kernel32.TerminateProcess(self._process, 1)
            kernel32.WaitForSingleObject(self._process, 3000)
            self.poll()
        if not self._finished:
            self._exit = 1
            self._finished = True
        _close(self._input_write)
        _close(self._output_read)
        _close(self._process)
        self._input_write = None
        self._output_read = None
        self._process = None


class ConPTYProcess:
    """Small Popen-shaped view so session code can call poll/wait/pid."""

    def __init__(self, console):
        self._console = console
        self.pid = console.pid

    def poll(self):
        return self._console.poll()

    def wait(self, timeout=None):
        return self._console.wait(timeout)
