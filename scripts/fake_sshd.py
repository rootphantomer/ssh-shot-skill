#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fake_sshd.py —— 仅用于自测的最小 SSH 服务端

在本地起一个只有密码认证、只支持 exec 的 SSH 服务, 用来端到端验证
ssh_shot.py 的 SSH 采集链路, 不需要任何真实远端主机。

    .venv/bin/python tests/fake_sshd.py --port 2222 --password 123456
    # 另开一个终端:
    .venv/bin/python ssh_shot.py --host 127.0.0.1 --port 2222 --user root \
        -p 123456 -c "uname -a" -o /tmp/evidence
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import threading

import paramiko

HOST_KEY = None


class StubSFTPServer(paramiko.SFTPServerInterface):
    """把 SFTP 直接落到本机文件系统(仅供本地自测, 只监听 127.0.0.1)。"""

    @staticmethod
    def _rw(flags: int) -> str:
        if flags & os.O_RDWR:
            return "r+b"
        if flags & os.O_WRONLY:
            return "ab" if flags & os.O_APPEND else "wb"
        return "rb"

    def canonicalize(self, path: str) -> str:
        return os.path.abspath(path or ".")

    def stat(self, path: str):
        try:
            return paramiko.SFTPAttributes.from_stat(os.stat(path))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def lstat(self, path: str):
        try:
            return paramiko.SFTPAttributes.from_stat(os.lstat(path))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def open(self, path: str, flags: int, attr: object):
        mode = (getattr(attr, "st_mode", None) or 0o644) & 0o7777
        try:
            fd = os.open(path, flags | getattr(os, "O_BINARY", 0), mode)
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)
        if attr is not None and (flags & os.O_CREAT):
            try:
                os.chmod(path, mode)
            except OSError:
                pass
        fobj = os.fdopen(fd, self._rw(flags))
        handle = paramiko.SFTPHandle(flags)
        handle.filename = path
        handle.readfile = fobj
        handle.writefile = fobj
        return handle

    def remove(self, path: str) -> int:
        try:
            os.remove(path)
            return paramiko.SFTP_OK
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def mkdir(self, path: str, attr: object) -> int:
        try:
            os.mkdir(path)
            return paramiko.SFTP_OK
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def rmdir(self, path: str) -> int:
        try:
            os.rmdir(path)
            return paramiko.SFTP_OK
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def chattr(self, path: str, attr: object) -> int:
        try:
            os.chmod(path, getattr(attr, "st_mode", 0o644) & 0o7777)
            return paramiko.SFTP_OK
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)


class Server(paramiko.ServerInterface):
    def __init__(self, password: str) -> None:
        self.password = password
        self.event = threading.Event()

    def check_auth_password(self, username: str, password: str) -> int:
        if password == self.password:
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def get_allowed_auths(self, username: str) -> str:
        return "password"

    def check_channel_request(self, kind: str, chanid: int) -> int:
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_pty_request(self, *args: object, **kwargs: object) -> bool:
        return True

    def check_channel_shell_request(self, channel: paramiko.Channel) -> bool:
        self.event.set()
        return True

    def check_channel_exec_request(self, channel: paramiko.Channel, command: bytes) -> bool:
        def run() -> None:
            cmd = command.decode("utf-8", errors="replace")
            proc = subprocess.run(
                ["bash", "-lc", cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env={**os.environ, "TERM": "xterm-256color"},
            )
            try:
                channel.sendall(proc.stdout)
            except Exception:  # noqa: BLE001
                pass
            channel.send_exit_status(proc.returncode)
            channel.close()

        threading.Thread(target=run, daemon=True).start()
        self.event.set()
        return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=2222)
    ap.add_argument("--password", default="123456")
    ap.add_argument("--host-key", default="", help="主机私钥路径, 默认用内置测试密钥")
    args = ap.parse_args()

    global HOST_KEY
    if args.host_key:
        HOST_KEY = paramiko.RSAKey(filename=args.host_key)
    else:
        HOST_KEY = paramiko.RSAKey.generate(2048)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", args.port))
    sock.listen(8)
    print(f"fake sshd listening on 127.0.0.1:{args.port} (password: {args.password})", flush=True)

    def handle(conn: socket.socket) -> None:
        transport = paramiko.Transport(conn)
        transport.add_server_key(HOST_KEY)
        transport.set_subsystem_handler("sftp", paramiko.SFTPServer, StubSFTPServer)
        srv = Server(args.password)
        try:
            transport.start_server(server=srv)
            chan = transport.accept(20)
            if chan is None:
                transport.close()
                return
            srv.event.wait(20)
            while transport.is_active():
                threading.Event().wait(0.2)
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                transport.close()
            except Exception:  # noqa: BLE001
                pass

    try:
        while True:
            conn, _ = sock.accept()
            threading.Thread(target=handle, args=(conn,), daemon=True).start()
    except KeyboardInterrupt:
        print("\nbye", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
