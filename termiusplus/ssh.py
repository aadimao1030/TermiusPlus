"""Validated SSH connection arguments shared by all transports."""
import os
import re

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
    return dict(name=str(route.get('name') or host)[:100], host=host, port=port, jump=jump, key=key)

def ssh_args(route):
    r = validate_route(route)
    args = ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ConnectTimeout=8', '-o', 'ServerAliveInterval=10',
            '-o', 'ServerAliveCountMax=2', '-o', 'Compression=no', '-p', str(r['port'])]
    if r['jump']:
        args += ['-J', r['jump']]
    if r['key']:
        args += ['-i', os.path.expanduser(r['key'])]
    return args
