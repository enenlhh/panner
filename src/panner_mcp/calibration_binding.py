"""Input identity shared by calibration, protocol freezing, and screening."""
from __future__ import annotations

import json
from pathlib import Path

from .workspace import sha256_file

BINDING_FORMAT = "panner-calibration-binding-v1"


def judgment_config(config: dict) -> dict:
    """Only settings that affect answers; scheduling and prices are independent."""
    return {
        "backend": config["backend"], "provider": config["provider"],
        "model": config["model"], "endpoint": config.get("endpoint"),
        "temperature": config.get("temperature", 0),
        "confidence_floor": config.get("confidence_floor"),
        "judge_channel": config.get("judge_channel", "calibrated-decision-model"),
    }


def optional_hash(path: Path) -> str | None:
    return sha256_file(path) if path.is_file() else None


def included_labels(path: Path) -> set[str]:
    return {line.strip() for line in path.read_text(encoding="utf-8").split("\n") if line.strip()}


def verify_frozen_calibration(protocol_file: Path, protocol: dict, manifest: dict) -> None:
    """Check the immutable calibration evidence attached to a protocol version."""
    if protocol.get("calibration") is None:
        return
    report = protocol_file.parent / "calibration.json"
    if (not report.is_file() or manifest.get("calibration_report_sha256") != sha256_file(report)
            or json.loads(report.read_text(encoding="utf-8")).get("binding") != protocol["calibration"]):
        raise ValueError("frozen calibration evidence is missing or changed; recalibrate and freeze again")


def validate_binding(binding: dict | None, workspace: Path, state: dict, criteria_sha256: str,
                     *, draft_sha256: str | None = None, config: dict | None = None) -> None:
    """Require the exact labeled inputs and judgment configuration that were calibrated."""
    error = "calibration inputs changed or evidence is incomplete; run panner_compile_calibration and freeze again"
    required = {"format", "criteria_sha256", "draft_sha256", "pool_path", "pool_sha256",
                "pool_manifest_sha256", "labels_path", "labels_sha256", "workspace_labels_sha256",
                "run_config_path", "judgment_config"}
    if (not isinstance(binding, dict) or not required <= binding.keys()
            or binding.get("format") != BINDING_FORMAT or not isinstance(binding.get("judgment_config"), dict)):
        raise ValueError(error)
    try:
        pool = Path(state["active_pool"]).resolve(strict=True)
        labels = Path(binding["labels_path"])
        current = {
            "criteria_sha256": criteria_sha256,
            "pool_path": str(pool), "pool_sha256": sha256_file(pool),
            "pool_manifest_sha256": sha256_file(pool.parent / "dedup-manifest.json"),
            "labels_sha256": sha256_file(labels),
            "workspace_labels_sha256": optional_hash(workspace / "labels/included.txt"),
        }
        if any(binding.get(key) != value for key, value in current.items()):
            raise ValueError(error)
        if draft_sha256 is not None and binding.get("draft_sha256") != draft_sha256:
            raise ValueError(error)
        if config is None:
            # Freezing checks the calibration input file. Screening supplies its
            # confirmed snapshot, so continuation is independent of that file.
            from .screening import load_run_config
            _, config = load_run_config(binding["run_config_path"])
        if binding.get("judgment_config") != judgment_config(config):
            raise ValueError("screening judgment configuration differs from calibration; recalibrate and freeze again")
        declared = workspace / "labels/included.txt"
        labels_set = included_labels(labels)
        if not labels_set or (declared.is_file() and not included_labels(declared) <= labels_set):
            raise ValueError(error)
    except (OSError, KeyError, TypeError) as exc:
        raise ValueError(error) from exc
