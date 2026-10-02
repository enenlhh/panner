"""Shared local workspace checks and atomic metadata writes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path


MARKER = "panner-workspace.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def workspace_state(path_text: str) -> tuple[Path, dict]:
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        raise ValueError("workspace_path must be an absolute path")
    path = path.resolve()
    if path.exists() and not path.is_dir():
        raise ValueError("workspace_path is not a directory")
    marker = path / MARKER
    if marker.exists():
        try:
            state = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("PANNER workspace marker is unreadable") from exc
        if not isinstance(state, dict) or state.get("format") != "panner-workspace-v1":
            raise ValueError("PANNER workspace marker has an unsupported format")
        active_criteria = state.get("active_eligibility_criteria")
        if active_criteria is not None and not (
            isinstance(active_criteria, str)
            and re.fullmatch(r"criteria/v[0-9]+/eligibility-criteria\.md", active_criteria)
        ):
            raise ValueError("PANNER workspace marker has an invalid criteria path")
        active_citation = state.get("active_citation")
        if active_citation is not None and not (
            isinstance(active_citation, str) and re.fullmatch(r"citations/v[0-9]+/citation\.jsonl", active_citation)
        ):
            raise ValueError("PANNER workspace marker has an invalid citation path")
        active_protocol = state.get("active_protocol")
        if active_protocol is not None and not (
            isinstance(active_protocol, str) and re.fullmatch(r"protocol/v[0-9]+/protocol\.json", active_protocol)
        ):
            raise ValueError("PANNER workspace marker has an invalid protocol path")
        active_pool = state.get("active_pool")
        if active_pool is not None and not (
            isinstance(active_pool, str) and Path(active_pool).is_absolute()
            and Path(active_pool).name == "citation-pool.jsonl"
        ):
            raise ValueError("PANNER workspace marker has an invalid citation-pool path")
        active_screening = state.get("active_screening")
        if active_screening is not None and not (
            isinstance(active_screening, str)
            and re.fullmatch(r"screening/v[0-9]+/screening-results\.jsonl", active_screening)
        ):
            raise ValueError("PANNER workspace marker has an invalid screening path")
        return path, state
    if path.exists() and any(path.iterdir()):
        raise ValueError("workspace_path must be new, empty, or an existing PANNER workspace")
    return path, {
        "format": "panner-workspace-v1",
        "active_eligibility_criteria": None,
        "active_citation": None,
        "active_pool": None,
        "active_protocol": None,
        "active_screening": None,
    }


def write_json_atomic(path: Path, value: dict) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temp_path = Path(stream.name)
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    os.replace(temp_path, path)
