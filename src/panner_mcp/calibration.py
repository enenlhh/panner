"""Compile-time calibration: check a protocol draft against the review's own included set.

Step 03 validates a draft structurally and against agent-written trial scenarios, which is
circular: the same agent writes the expected answers and the expected outcomes. This module
judges the review's labeled included records with the calibrated decision model, evaluates the
draft's rules over those judgments, and reports every labeled record the draft would exclude.
`freeze_compile` refuses to freeze a draft while such a report is missing or non-empty.
"""

from __future__ import annotations

import json
import os
import random
import re
from pathlib import Path

from .citations import _unique_object
from .evaluate import evaluate_rules
from .protocol import LABELS_DIR, LABELS_NAME, calibration_report_path, preview_compile
from .screening import _field_rule_answer, _judge_citation_calibrated, _load_jsonl, load_run_config
from .workspace import sha256_file, workspace_state


def _normalise_title(text: object) -> str:
    return re.sub(r"[^0-9a-z]+", "", str(text or "").lower())


def _match_label(label: str, rows: list[dict]) -> list[dict]:
    """A labeled record is matched by the strongest identifier it carries: pmid, doi, title, or source id."""
    token = label.strip()
    if token.startswith("pmid:"):
        value = token[5:].strip()
        return [row for row in rows if row["pmid"] == value]
    if token.startswith("doi:"):
        value = token[4:].strip().lower()
        return [row for row in rows if (row["doi"] or "").lower() == value]
    if token.startswith("title:"):
        value = _normalise_title(token[6:])
        return [row for row in rows if _normalise_title(row["title"]) == value]
    return [row for row in rows if (row.get("source", {}) or {}).get("id", "") == token]


def _judgment_for(config: dict, draft: dict, citation: dict, api_key: str) -> tuple[dict, dict, list[str]]:
    semantic = [signal for signal in draft["signals"] if signal["evaluation_mode"] == "semantic"]
    field_rules = [signal for signal in draft["signals"] if signal["evaluation_mode"] == "field_rule"]
    if semantic:
        judged = _judge_citation_calibrated(config, semantic, citation, api_key)
    else:
        judged = {"entities": {"arms": []}, "answers": [], "flags": [], "usage": {"cost_usd": 0.0}}
    answers = list(judged["answers"])
    for signal in field_rules:
        answers.append({"signal": signal["id"], "on": "record", "answer": _field_rule_answer(signal, citation)})
    return {"entities": judged.get("entities", {}), "answers": answers}, judged.get("usage", {}), judged.get("flags", [])


def calibrate_compile(
    workspace_path: str,
    criteria_path: str,
    draft_path: str,
    run_config_path: str,
    labels_path: str | None = None,
) -> dict:
    """Judge the labeled included records with the draft protocol and report every exclusion."""
    workspace, state = workspace_state(workspace_path)
    preview = preview_compile(workspace_path, criteria_path, draft_path)
    if preview["status"] != "ready_for_confirmation":
        raise ValueError("protocol draft is still a draft: " + "; ".join(preview["issues"]))
    draft_file = Path(draft_path).expanduser().resolve(strict=True)
    draft = json.loads(draft_file.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)

    pool_text = state.get("active_pool")
    if not pool_text or not Path(pool_text).is_file():
        raise ValueError("calibration requires the confirmed citation pool of this workspace")
    pool_file = Path(pool_text)
    rows = _load_jsonl(pool_file)
    by_id = {row["id"]: row for row in rows}

    labels_file = Path(labels_path).expanduser().resolve() if labels_path else workspace / LABELS_DIR / LABELS_NAME
    if not labels_file.is_file():
        raise ValueError(
            f"no labeled included set: pass labels_path or create {workspace / LABELS_DIR / LABELS_NAME} "
            "(one identifier per line: pmid:, doi:, title:, or a source id)"
        )
    labels = [line.strip() for line in labels_file.read_text(encoding="utf-8").split("\n") if line.strip()]

    config_path, config = load_run_config(run_config_path)
    api_key = os.environ.get("PANNER_DECISION_API_KEY", "") or os.environ.get("PANNER_LLM_API_KEY", "")
    if config["backend"] == "calibrated" and not api_key:
        raise ValueError("set PANNER_DECISION_API_KEY in the environment; keys never live in the workspace")

    entries: list[dict] = []
    judgments: dict[str, dict] = {}
    unmapped: list[str] = []
    calls = 0
    cost = 0.0
    for label in labels:
        matches = _match_label(label, rows)
        if not matches:
            unmapped.append(label)
            entries.append({"label": label, "citation_id": None, "status": "unmapped", "rules": [], "answers": []})
            continue
        representatives = sorted({(row["duplicate_of"] or row["id"]) for row in matches})
        for representative in representatives:
            citation = by_id.get(representative)
            if citation is None:
                unmapped.append(label)
                entries.append({"label": label, "citation_id": representative, "status": "unmapped", "rules": [], "answers": []})
                continue
            if representative not in judgments:
                judgment, usage, flags = _judgment_for(config, draft, citation, api_key)
                judgment["citation_id"] = representative
                judgment["flags"] = flags
                judgments[representative] = judgment
                calls += 1
                cost += float(usage.get("cost_usd") or 0.0)
            judgment = judgments[representative]
            evaluation = evaluate_rules(draft["decision_rules"], judgment)
            status = "excluded" if evaluation["status"] == "excluded_candidate" else "retained"
            entries.append(
                {
                    "label": label,
                    "citation_id": representative,
                    "status": status,
                    "rules": [item["rule"] for item in evaluation["basis"]],
                    "answers": judgment["answers"],
                    "flags": judgment.get("flags", []),
                }
            )

    excluded = [entry for entry in entries if entry["status"] == "excluded"]
    mapped = sum(1 for entry in entries if entry["citation_id"] is not None)

    sample_report = None
    sample_size = config.get("sample_size")
    if isinstance(sample_size, int) and sample_size > 0:
        """Judge a random pool sample with the same draft and report the estimated exclusion rate.

        The labeled set can only detect false exclusions of included records; it is blind to a
        draft whose rules exclude almost nothing. The sample puts a number on exclusion power
        before the protocol is frozen, at a fraction of a full screening run's cost."""
        screened = [row for row in rows if not row.get("duplicate_of")]
        rng = random.Random(sha256_file(draft_file)[:16])
        sample = rng.sample(screened, min(sample_size, len(screened)))
        sample_entries = []
        s_excluded = 0
        s_calls = 0
        s_cost = 0.0
        for citation in sample:
            judgment, usage, flags = _judgment_for(config, draft, citation, api_key)
            judgment["citation_id"] = citation["id"]
            judgment["flags"] = flags
            s_calls += 1
            s_cost += float(usage.get("cost_usd") or 0.0)
            evaluation = evaluate_rules(draft["decision_rules"], judgment)
            status = "excluded" if evaluation["status"] == "excluded_candidate" else "retained"
            s_excluded += status == "excluded"
            sample_entries.append(
                {
                    "citation_id": citation["id"],
                    "status": status,
                    "rules": [item["rule"] for item in evaluation["basis"]],
                }
            )
        sample_report = {
            "sample_size": len(sample_entries),
            "pool_size": len(screened),
            "excluded": s_excluded,
            "retained": len(sample_entries) - s_excluded,
            "estimated_exclusion_rate": round(s_excluded / len(sample_entries), 4) if sample_entries else None,
            "calls": s_calls,
            "cost_usd": round(s_cost, 6),
            "entries": sample_entries,
        }

    report = {
        "status": "calibrated",
        "workspace_path": str(workspace),
        "criteria_version": preview["criteria_version"],
        "criteria_sha256": preview["criteria_sha256"],
        "draft_path": str(draft_file),
        "draft_sha256": sha256_file(draft_file),
        "pool_path": str(pool_file),
        "pool_sha256": sha256_file(pool_file),
        "run_config_sha256": sha256_file(config_path),
        "model": config["model"],
        "endpoint": config.get("endpoint", ""),
        "labels_path": str(labels_file),
        "labels_sha256": sha256_file(labels_file),
        "labels": len(labels),
        "mapped": mapped,
        "unmapped": unmapped,
        "retained": mapped - len(excluded),
        "excluded": len(excluded),
        "excluded_labels": excluded,
        "sample": sample_report,
        "calls": calls,
        "cost_usd": round(cost, 6),
        "entries": entries,
    }
    report_file = calibration_report_path(workspace, draft_file)
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report["report_path"] = str(report_file)
    return report
