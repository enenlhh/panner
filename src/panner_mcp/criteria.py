"""Versioned eligibility-criteria confirmation for step 01."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .workspace import MARKER, sha256_file, workspace_state, write_json_atomic

CRITERIA_NAME = "eligibility-criteria.md"


def _source_metadata(paths: list[str]) -> list[dict]:
    seen: set[Path] = set()
    sources = []
    for path_text in paths:
        path = Path(path_text).expanduser()
        if not path.is_absolute():
            raise ValueError("source_paths must contain absolute file paths")
        path = path.resolve(strict=True)
        if not path.is_file():
            raise ValueError(f"criteria source is not a file: {path}")
        if path in seen:
            raise ValueError(f"criteria source listed twice: {path}")
        seen.add(path)
        sources.append({"original_path": str(path), "sha256": sha256_file(path), "size": path.stat().st_size})
    return sources


def _section(markdown: str, heading: str) -> str:
    match = re.search(rf"(?im)^# {re.escape(heading)}[ \t]*$", markdown)
    if not match:
        return ""
    tail = markdown[match.end():]
    end = re.search(r"(?m)^# ", tail)
    if end:
        tail = tail[: end.start()]
    return re.sub(r"(?m)^#{2,6} .*$", "", tail).strip()


def preview_criteria(
    workspace_path: str,
    markdown: str,
    origin_note: str,
    source_paths: list[str],
    unresolved_questions: list[str],
) -> dict:
    """Return the exact text and hash to show the user before confirmation; write nothing."""
    workspace, state = workspace_state(workspace_path)
    sources = _source_metadata(source_paths)
    questions = [question.strip() for question in unresolved_questions if question.strip()]
    issues = []
    if not _section(markdown, "Review objective"):
        issues.append("Review objective is missing or empty")
    if not _section(markdown, "Eligibility criteria"):
        issues.append("Eligibility criteria are missing or empty")
    if not origin_note.strip():
        issues.append("The origin of the criteria and user decisions must be described")

    payload = {
        "workspace": str(workspace),
        "previous_version": state.get("active_eligibility_criteria"),
        "markdown": markdown,
        "origin_note": origin_note.strip(),
        "sources": sources,
        "unresolved_questions": questions,
    }
    plan_hash = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return {
        "status": "ready_for_confirmation" if not issues else "draft",
        "workspace_path": str(workspace),
        "file_name": CRITERIA_NAME,
        "full_text_to_show_user": markdown,
        "origin_note": origin_note.strip(),
        "sources": sources,
        "unresolved_questions": questions,
        "issues": issues,
        "plan_hash": plan_hash,
    }


def confirm_criteria(
    workspace_path: str,
    markdown: str,
    origin_note: str,
    source_paths: list[str],
    unresolved_questions: list[str],
    plan_hash: str,
    user_confirmation: str,
) -> dict:
    """Save an immutable version only when the exact preview was confirmed."""
    preview = preview_criteria(workspace_path, markdown, origin_note, source_paths, unresolved_questions)
    if preview["status"] != "ready_for_confirmation":
        raise ValueError("criteria are still a draft: " + "; ".join(preview["issues"]))
    if plan_hash != preview["plan_hash"]:
        raise ValueError("the preview changed; show the new full text to the user before confirmation")
    if not user_confirmation.strip():
        raise ValueError("record the user's explicit confirmation of the full preview")

    workspace, state = workspace_state(workspace_path)
    workspace.mkdir(parents=True, exist_ok=True)
    criteria_root = workspace / "criteria"
    criteria_root.mkdir(exist_ok=True)
    versions = sorted(
        int(path.name[1:])
        for path in criteria_root.iterdir()
        if path.is_dir() and re.fullmatch(r"v[0-9]+", path.name)
    )
    version = f"v{(versions[-1] if versions else 0) + 1:04d}"
    target = criteria_root / version
    staging = Path(tempfile.mkdtemp(prefix=".criteria-", dir=criteria_root))
    try:
        criteria_file = staging / CRITERIA_NAME
        criteria_file.write_text(markdown, encoding="utf-8")
        saved_sources = []
        if preview["sources"]:
            source_dir = staging / "sources"
            source_dir.mkdir()
            for index, source in enumerate(preview["sources"], 1):
                original = Path(source["original_path"])
                if sha256_file(original) != source["sha256"]:
                    raise ValueError(f"criteria source changed during confirmation: {original}")
                saved_name = f"{index:03d}-{original.name[:120]}"
                copy = source_dir / saved_name
                shutil.copyfile(original, copy)
                if sha256_file(copy) != source["sha256"]:
                    raise ValueError(f"criteria source copy failed verification: {original}")
                saved_sources.append({**source, "snapshot": f"sources/{saved_name}"})
        manifest = {
            "version": version,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "criteria_sha256": sha256_file(criteria_file),
            "plan_hash": plan_hash,
            "previous_version": state.get("active_eligibility_criteria"),
            "origin_note": origin_note.strip(),
            "user_confirmation": user_confirmation.strip(),
            "unresolved_questions": preview["unresolved_questions"],
            "sources": saved_sources,
        }
        (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(staging, target)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise

    relative = f"criteria/{version}/{CRITERIA_NAME}"
    state["active_eligibility_criteria"] = relative
    write_json_atomic(workspace / MARKER, state)
    return {
        "status": "confirmed",
        "version": version,
        "criteria_path": str(workspace / relative),
        "manifest_path": str(target / "manifest.json"),
        "criteria_sha256": manifest["criteria_sha256"],
        "next_step": "02: build citation.jsonl and the citation pool",
    }


def criteria_status(workspace_path: str) -> dict:
    workspace, state = workspace_state(workspace_path)
    relative = state.get("active_eligibility_criteria")
    if not relative:
        return {"status": "not_confirmed", "workspace_path": str(workspace)}
    criteria_file = workspace / relative
    manifest_file = criteria_file.parent / "manifest.json"
    if not criteria_file.is_file() or not manifest_file.is_file():
        raise ValueError("active eligibility criteria or its manifest is missing")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if sha256_file(criteria_file) != manifest.get("criteria_sha256"):
        raise ValueError("active eligibility criteria differ from the confirmed version")
    return {
        "status": "confirmed",
        "version": manifest["version"],
        "criteria_path": str(criteria_file),
        "manifest_path": str(manifest_file),
        "criteria_sha256": manifest["criteria_sha256"],
        "unresolved_questions": manifest.get("unresolved_questions", []),
    }
