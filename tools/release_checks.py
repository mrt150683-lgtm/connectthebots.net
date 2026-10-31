"""Reproduce the Linux R0 installation and offline-execution evidence.

The pinned build-only tools must already be installed. This runner buys,
downloads and deploys nothing. It builds a local wheel and uses a disposable
venv to check the installed package away from the source checkout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import socket
import subprocess
import sys
import tempfile
import time


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("artifacts/r0/release"))
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    repository = Path(__file__).resolve().parents[1]
    harness = repository / "tools" / "isolated_exec.py"
    records = []

    def execute(name, command, *, cwd=repository, isolated=True):
        invocation = ([sys.executable, str(harness), "--"] if isolated else []) + [str(x) for x in command]
        began = time.perf_counter()
        result = subprocess.run(invocation, cwd=cwd, text=True, capture_output=True, timeout=120)
        record = {"check": name, "command": [str(x) for x in command], "working_directory": str(cwd),
                  "external_isolation": isolated, "exit_code": result.returncode,
                  "elapsed_seconds": round(time.perf_counter() - began, 4),
                  "stdout": result.stdout, "stderr": result.stderr}
        records.append(record)
        (out / f"{name}.log").write_text(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError(f"{name} failed; inspect {out / (name + '.log')}")
        return result

    success = False
    comparison = {}
    inherited = {}
    try:
        execute("source_verification", [sys.executable, "run_agentmatch.py", "verify", "--offline", "--out", out / "source-verification"])
        execute("source_demo", [sys.executable, "run_agentmatch.py", "demo", "--out", out / "source-demo"])
        execute("mutation_checks", [sys.executable, "tools/mutation_checks.py", "--out", out / "mutation_results.json"])
        execute("build_wheel", [sys.executable, "-m", "pip", "wheel", "--no-index", "--no-deps", "--no-build-isolation", ".", "--wheel-dir", "dist"])
        wheel = repository / "dist" / "agentmatch-0.1.0-py3-none-any.whl"
        if not wheel.is_file():
            raise RuntimeError("Expected wheel not produced")
        with tempfile.TemporaryDirectory(prefix="agentmatch-clean-install-") as temporary:
            clean = Path(temporary)
            execute("create_venv", [sys.executable, "-m", "venv", clean / "venv"], cwd=clean)
            python = clean / "venv" / "bin" / "python"
            binary = clean / "venv" / "bin" / "agentmatch"
            execute("install_wheel", [python, "-m", "pip", "install", "--no-index", "--no-deps", wheel], cwd=clean)
            execute("installed_verification", [binary, "verify", "--offline", "--out", out / "installed-verification"], cwd=clean)
            execute("installed_demo", [binary, "demo", "--out", out / "installed-demo"], cwd=clean)
            execute("installed_metadata", [python, "-c", "from importlib.metadata import distribution; import json; d=distribution('agentmatch'); print(json.dumps({'version':d.version,'runtime_requirements':d.requires or []}))"], cwd=clean)
        source = (out / "source-demo" / "demo.json").read_bytes()
        installed = (out / "installed-demo" / "demo.json").read_bytes()
        comparison = {"source_installed_demo_identical": source == installed,
                      "demo_sha256": hashlib.sha256(source).hexdigest(),
                      "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}
        if source != installed:
            raise RuntimeError("Installed and source demo semantics differ")
        # Deliberately pass an already-connected local socket descriptor. The
        # harness must close it, not merely prohibit new socket() calls.
        left, right = socket.socketpair()
        try:
            descriptor = right.fileno()
            code = ("import os,errno\ntry:\n os.write(" + str(descriptor)
                    + ",b'probe')\nexcept OSError as e:\n assert e.errno==errno.EBADF\n print('inherited_socket_closed')\nelse:\n raise SystemExit(9)\n")
            checked = subprocess.run([sys.executable, str(harness), "--", sys.executable, "-c", code],
                                     pass_fds=(descriptor,), text=True, capture_output=True, timeout=15)
            rejected = subprocess.run([sys.executable, str(harness), "--", sys.executable, "-c", "print('unreachable')"],
                                      stdin=right, text=True, capture_output=True, timeout=15)
            inherited = {"inherited_socket_closed": checked.returncode == 0 and "inherited_socket_closed" in checked.stdout,
                         "socket_stdio_rejected": rejected.returncode == 2 and "Socket standard" in rejected.stderr}
            if not all(inherited.values()):
                raise RuntimeError("Inherited-descriptor control failed")
        finally:
            left.close()
            right.close()
        success = True
    finally:
        result = {"success": success, "release": "R0", "date": "2026-10-26",
                  "environment": {"python": platform.python_version(), "platform": platform.platform(),
                                  "maximum_child_peak_rss_kib_linux": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss},
                  "execution_boundary": "libseccomp denial of socket-related and io_uring_setup syscalls; inherited nonstdio descriptors closed; socket stdio refused",
                  "positive_controls": "Every isolated command first checks IPv4, IPv6 and Unix socket creation returns EPERM.",
                  "inherited_descriptor_controls": inherited, "comparison": comparison, "commands": records,
                  "limits": ["Local Linux reference run, not the owner's Mac Studio or target Ryzen benchmark.",
                             "No private-model isolation, network encryption, persistent-queue or human-utility claim.",
                             "Ordinary captured standard streams remain an intentional test-harness communication path."]}
        (out / "offline_run.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"success": success, "commands": len(records), "comparison": comparison, "report": str(out / "offline_run.json")}))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
