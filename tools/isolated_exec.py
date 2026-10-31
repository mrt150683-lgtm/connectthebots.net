"""Linux test harness: deny socket-related syscalls, prove it, then exec.

This is an external *R0 test* boundary, not an R1 private-model sandbox.
Requires the operating system's libseccomp. Does not alter the host firewall.
The inherited process filter ends with this process tree. No credentials are
passed through. Usage: python tools/isolated_exec.py -- COMMAND ARG ...
"""
from __future__ import annotations

import ctypes
import ctypes.util
import errno
import json
import os
from pathlib import Path
import socket
import stat
import sys


def deny_network() -> list[str]:
    library = ctypes.util.find_library("seccomp")
    if sys.platform != "linux" or library is None:
        raise RuntimeError("This verification harness needs Linux and libseccomp; no isolation claim made.")
    lib = ctypes.CDLL(library, use_errno=True)
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_syscall_resolve_name.restype = ctypes.c_int
    lib.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    lib.seccomp_rule_add.restype = ctypes.c_int
    lib.seccomp_load.argtypes = [ctypes.c_void_p]
    lib.seccomp_load.restype = ctypes.c_int
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    lib.seccomp_release.restype = None
    context = lib.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW
    if not context:
        raise RuntimeError("seccomp_init failed")
    names = ["socket", "socketpair", "socketcall", "connect", "accept", "accept4",
             "bind", "listen", "sendto", "sendmsg", "sendmmsg", "recvfrom",
             "recvmsg", "recvmmsg", "io_uring_setup"]
    installed = []
    try:
        for name in names:
            number = lib.seccomp_syscall_resolve_name(name.encode("ascii"))
            if number < 0:  # Not every architecture has multiplexed socketcall.
                continue
            if lib.seccomp_rule_add(context, 0x00050000 | errno.EPERM, number, 0) != 0:
                raise RuntimeError(f"Cannot install required syscall rule: {name}")
            installed.append(name)
        if "socket" not in installed or "connect" not in installed:
            raise RuntimeError("Required socket denial could not be expressed")
        if lib.seccomp_load(context) != 0:
            raise RuntimeError("Cannot enforce process filter; no isolation claim made")
    finally:
        lib.seccomp_release(context)
    return installed


def close_inherited_descriptors() -> None:
    """Deny an existing socket's write/read path, which socket() rules miss."""
    for descriptor in (0, 1, 2):
        try:
            mode = os.fstat(descriptor).st_mode
        except OSError:
            continue
        if stat.S_ISSOCK(mode):
            raise RuntimeError("Socket standard input/output is outside this harness boundary")
    # Linux /proc enumerates inherited descriptors without guessing a maximum.
    descriptors = [int(name) for name in os.listdir("/proc/self/fd") if name.isdigit()]
    for descriptor in descriptors:
        if descriptor > 2:
            try:
                os.close(descriptor)
            except OSError as error:
                if error.errno != errno.EBADF:
                    raise


def main() -> int:
    command = sys.argv[1:]
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        print("Usage: isolated_exec.py -- COMMAND ARG ...", file=sys.stderr)
        return 2
    # No API keys, tokens, credentials or model configuration are inherited.
    environment = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8",
                   "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1"}
    if "PYTHONPATH" in os.environ:
        environment["PYTHONPATH"] = os.environ["PYTHONPATH"]
    try:
        close_inherited_descriptors()
        denied = deny_network()
        for family in (socket.AF_INET, socket.AF_INET6, socket.AF_UNIX):
            try:
                probe = socket.socket(family, socket.SOCK_STREAM)
            except OSError as error:
                if error.errno != errno.EPERM:
                    raise RuntimeError("Positive-control failure has the wrong errno") from error
            else:
                probe.close()
                raise RuntimeError("Positive control created a socket; network isolation failed")
        print(json.dumps({"external_harness": "linux_libseccomp",
                          "socket_positive_controls": "IPv4 IPv6 Unix denied EPERM",
                          "denied_syscalls": denied, "credentials_inherited": False,
                          "nonstdio_inherited_descriptors_closed": True,
                          "socket_stdio_rejected": True}),
              file=sys.stderr, flush=True)
        os.execvpe(command[0], command, environment)
    except (OSError, RuntimeError) as error:
        print(f"ISOLATION_FAILED: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
