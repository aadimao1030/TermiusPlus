"""Validated SSH connection arguments shared by all transports."""
import os
import re
import tempfile
from pathlib import Path

_PASSWORDS = {}
_ASKPASS = None


def _identity(route):
    return (route['host'], route['port'], route['jump'], route['key'])


def _askpass_path():
    global _ASKPASS
    if _ASKPASS is None:
        directory = Path(tempfile.mkdtemp(prefix='termiusplus-askpass-'))
        os.chmod(directory, 0o700)
        helper = directory / 'askpass'
        helper.write_text('#!/bin/sh\nprintf "%s\\n" "$TERMIUSPLUS_SSH_PASSWORD"\n')
        os.chmod(helper, 0o700)
        _ASKPASS = str(helper)
    return _ASKPASS

def validate_route(route):
    if not isinstance(route, dict):
        raise ValueError('线路格式不正确')
    host = str(route.get('host', ''))
    # Hosts may be SSH aliases, DNS names, IPv4 or bracket-free IPv6.
    if not re.fullmatch(r'(?:[A-Za-z0-9_.-]+@)?[A-Za-z0-9_][A-Za-z0-9_.:-]*', host):
        raise ValueError('主机请填 SSH 配置别名或 user@hostname，不能包含空格和命令')
    port = int(route.get('port') or 22)
    if not 1 <= port <= 65535:
        raise ValueError('端口必须在 1–65535 之间')
    jump = str(route.get('jump', ''))
    if jump and not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@:,\[\]-]*', jump):
        raise ValueError('跳板请填 user@host:port，可用逗号分隔')
    key = str(route.get('key', ''))
    if '\0' in key:
        raise ValueError('密钥路径不正确')
    clean = dict(name=str(route.get('name') or host)[:100], host=host, port=port, jump=jump, key=key)
    password = route.get('password')
    if password is not None:
        if not isinstance(password, str) or '\0' in password or len(password) > 4096:
            raise ValueError('SSH 密码不正确')
        if password:
            _PASSWORDS[_identity(clean)] = password
        else:
            _PASSWORDS.pop(_identity(clean), None)
    return clean


def ssh_env(route, extra=None):
    """Keep credentials in this process and the individual SSH child only."""
    password = _PASSWORDS.get(_identity(validate_route(route))) if route else None
    if not password:
        return dict(os.environ, **(extra or {})) if extra else None
    return dict(os.environ, **(extra or {}), SSH_ASKPASS=_askpass_path(),
                SSH_ASKPASS_REQUIRE='force', DISPLAY=os.environ.get('DISPLAY') or 'termiusplus:0',
                TERMIUSPLUS_SSH_PASSWORD=password)

def ssh_args(route):
    r = validate_route(route)
    password = bool(_PASSWORDS.get(_identity(r)))
    args = ['ssh', '-o', 'BatchMode=no' if password else 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ConnectTimeout=8', '-o', 'ServerAliveInterval=10',
            '-o', 'ServerAliveCountMax=2', '-o', 'Compression=no', '-p', str(r['port'])]
    if password:
        args += ['-o', 'NumberOfPasswordPrompts=1', '-o',
                 'PreferredAuthentications=password,keyboard-interactive',
                 '-o', 'PubkeyAuthentication=no']
    if r['jump']:
        args += ['-J', r['jump']]
    if r['key']:
        args += ['-i', os.path.expanduser(r['key'])]
    return args
