"""Deterministic local records for the wholly synthetic release."""
from __future__ import annotations

import hashlib
from importlib import resources
import json
from pathlib import Path
from typing import Any

from . import __version__
from .config import load_defaults


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def package_fingerprint() -> str:
    root = resources.files("agentmatch")
    records: list[tuple[str, bytes]] = []

    def walk(item: Any, prefix: str = "") -> None:
        for path in sorted(item.iterdir(), key=lambda x: x.name):
            name = prefix + path.name
            if path.is_dir() and path.name != "__pycache__":
                walk(path, name + "/")
            elif path.is_file() and path.name.endswith((".py", ".json", ".md")):
                records.append((name, path.read_bytes()))
    walk(root)
    digest = hashlib.sha256()
    for name, payload in records:
        digest.update(name.encode("utf-8") + b"\0" + hashlib.sha256(payload).digest())
    return digest.hexdigest()


def run_manifest(*, as_of: str, seed: int) -> dict[str, Any]:
    config = load_defaults()
    return {"software_version": __version__, "release": "R0", "plan_version": config["plan_version"],
            "contract_version": config["contract_version"], "code_and_data_sha256": package_fingerprint(),
            "as_of": as_of, "seed": seed, "synthetic_only": True,
            "real_inference_performed": False, "network_transport_implemented": False,
            "encrypted_storage_implemented": False,
            "reproducibility": "Fixed inputs and clock determine semantic output. Measurements are separate."}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json_text(value), encoding="utf-8")


def write_jsonl(path: Path, values: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(v, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
                            for v in values), encoding="utf-8")
