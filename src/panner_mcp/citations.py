"""Canonical citation validation and immutable intake for step 02."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from bib_dedupe.constants import entrytypes

from .workspace import MARKER, sha256_file, workspace_state, write_json_atomic


CITATION_NAME = "citation.jsonl"
TEXT_FIELDS = (
    "title", "abstract", "authors", "journal", "container_title", "volume", "issue", "pages", "doi", "pmid", "entry_type"
)
FIELDS = {"id", "source", "year", "publication_types", *TEXT_FIELDS}
SOURCE_FIELDS = {"file", "position", "id"}
ENTRY_TYPES = {
    value for name, value in vars(entrytypes).items() if name.isupper() and isinstance(value, str)
}


def _path_in_bundle(path_text: str) -> bool:
    path = PurePosixPath(path_text)
    return (
        len(path.parts) >= 2
        and path.parts[0] == "sources"
        and not path.is_absolute()
        and path.as_posix() == path_text
        and all(part not in (".", "..") for part in path.parts)
        and "\\" not in path_text
    )


def _unique_object(items: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def read_citations(path_text: str) -> tuple[Path, list[dict], list[str]]:
    """Read every imported citation without silently skipping malformed lines."""
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        raise ValueError("input_path must be an absolute path")
    path = path.resolve(strict=True)
    if not path.is_file() or path.name != CITATION_NAME:
        raise ValueError("input_path must point to a citation.jsonl file")

    rows: list[dict] = []
    issues: list[str] = []
    seen_ids = set()
    seen_positions = set()
    file_order: list[str] = []
    closed_files = set()
    with path.open("r", encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                issues.append(f"line {number}: blank line")
                continue
            try:
                row = json.loads(line, object_pairs_hook=_unique_object)
            except (json.JSONDecodeError, ValueError) as exc:
                issues.append(f"line {number}: invalid JSON: {exc}")
                continue
            if not isinstance(row, dict):
                issues.append(f"line {number}: citation must be a JSON object")
                continue
            row_issues = []
            if set(row) != FIELDS:
                row_issues.append(f"expected fields {sorted(FIELDS)}; got {sorted(row)}")
            identifier = row.get("id")
            if identifier != f"C{number:06d}":
                row_issues.append(f"id must be C{number:06d}")
            if not isinstance(identifier, str):
                row_issues.append("id must be a string")
            else:
                if identifier in seen_ids:
                    row_issues.append("id is repeated")
                seen_ids.add(identifier)
            source = row.get("source")
            if not isinstance(source, dict) or set(source) != SOURCE_FIELDS:
                row_issues.append("source must contain exactly file, position, id")
            else:
                file = source["file"]
                position = source["position"]
                if not isinstance(file, str) or not _path_in_bundle(file):
                    row_issues.append("source.file must be a relative POSIX path under sources/")
                elif file not in file_order:
                    if file_order:
                        closed_files.add(file_order[-1])
                    file_order.append(file)
                elif file in closed_files:
                    row_issues.append("source files must occupy contiguous rows")
                if not isinstance(position, int) or isinstance(position, bool) or position < 1:
                    row_issues.append("source.position must be a positive integer")
                elif isinstance(file, str):
                    if (file, position) in seen_positions:
                        row_issues.append("source.file and source.position are repeated")
                    seen_positions.add((file, position))
                if not isinstance(source["id"], str):
                    row_issues.append("source.id must be a string, empty when absent")
            for field in TEXT_FIELDS:
                if field in row and not isinstance(row[field], str):
                    row_issues.append(f"{field} must be a string, empty when absent")
            year = row.get("year")
            if year is not None and (not isinstance(year, int) or isinstance(year, bool)):
                row_issues.append("year must be an integer or null")
            publication_types = row.get("publication_types")
            if not isinstance(publication_types, list) or any(not isinstance(item, str) for item in publication_types):
                row_issues.append("publication_types must be a string list")
            if isinstance(row.get("entry_type"), str) and row["entry_type"] and row["entry_type"] not in ENTRY_TYPES:
                row_issues.append("entry_type must be a BibDedupe book type or empty")
            if row_issues:
                issues.extend(f"line {number}: {issue}" for issue in row_issues)
            else:
                rows.append(row)
    if len(issues) > 100:
        issues = issues[:100] + ["additional validation errors omitted; repair the first errors and validate again"]
    if not issues:
        positions_by_file: dict[str, list[int]] = {}
        for row in rows:
            positions_by_file.setdefault(row["source"]["file"], []).append(row["source"]["position"])
        for file, positions in positions_by_file.items():
            if positions != list(range(1, len(positions) + 1)):
                issues.append(f"{file}: source.position must be consecutive in source order, starting at 1")
    return path, rows, issues


def preview_citations(
    input_path: str,
    workspace_path: str,
    mapping_note: str,
    source_counts: dict[str, int | None],
) -> dict:
    """Validate the agent's mapping and source reconciliation without writing."""
    input_file, rows, issues = read_citations(input_path)
    workspace, state = workspace_state(workspace_path)
    if not mapping_note.strip():
        issues.append("mapping_note must describe record boundaries and source-to-citation field mapping")
    observed = Counter(row["source"]["file"] for row in rows)
    missing_sources = set(observed) - set(source_counts)
    unexpected_sources = {
        file for file, count in source_counts.items() if file not in observed and count not in (0, None)
    }
    if missing_sources or unexpected_sources:
        issues.append("source_counts must name every represented source; sources with no citations need count 0 or null")
    source_metadata = []
    for file in source_counts:
        if not _path_in_bundle(file):
            issues.append(f"invalid source path: {file}")
            continue
        original = (input_file.parent / file).resolve()
        if not original.is_file():
            issues.append(f"source file is missing: {file}")
            continue
        count = source_counts[file]
        if count is not None and (not isinstance(count, int) or isinstance(count, bool) or count < 0):
            issues.append(f"source count must be nonnegative or null: {file}")
        elif count is not None and count != observed[file]:
            issues.append(f"{file}: source count {count} differs from citation rows {observed[file]}")
        source_metadata.append({"file": file, "original_path": str(original), "sha256": sha256_file(original), "source_count": count})

    coverage = {}
    for file in source_counts:
        records = [row for row in rows if row["source"]["file"] == file]
        coverage[file] = {
            "citations": len(records),
            "title_nonempty": sum(bool(row["title"].strip()) for row in records),
            "abstract_nonempty": sum(bool(row["abstract"].strip()) for row in records),
            "doi_nonempty": sum(bool(row["doi"].strip()) for row in records),
            "pmid_nonempty": sum(bool(row["pmid"].strip()) for row in records),
        }
    payload = {
        "input_path": str(input_file),
        "input_sha256": sha256_file(input_file),
        "workspace_path": str(workspace),
        "previous_version": state.get("active_citation"),
        "mapping_note": mapping_note.strip(),
        "source_counts": source_counts,
        "sources": source_metadata,
    }
    plan_hash = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    with input_file.open("r", encoding="utf-8") as stream:
        input_lines = sum(1 for _ in stream)
    return {
        "status": "ready_for_confirmation" if not issues else "draft",
        "input_path": str(input_file),
        "input_sha256": payload["input_sha256"],
        "workspace_path": str(workspace),
        "citations": len(rows),
        "input_lines": input_lines,
        "source_coverage": coverage,
        "unverified_source_counts": [file for file, count in source_counts.items() if count is None],
        "issues": issues,
        "plan_hash": plan_hash,
        "sources": source_metadata,
    }


def confirm_citations(
    input_path: str,
    workspace_path: str,
    mapping_note: str,
    source_counts: dict[str, int | None],
    plan_hash: str,
    user_confirmation: str,
) -> dict:
    preview = preview_citations(input_path, workspace_path, mapping_note, source_counts)
    if preview["status"] != "ready_for_confirmation":
        raise ValueError("citation mapping is still a draft: " + "; ".join(preview["issues"]))
    if plan_hash != preview["plan_hash"]:
        raise ValueError("citation input or source plan changed; preview again before confirmation")
    if not user_confirmation.strip():
        raise ValueError("record the user's explicit confirmation of the source mapping and counts")

    workspace, state = workspace_state(workspace_path)
    workspace.mkdir(parents=True, exist_ok=True)
    citations_root = workspace / "citations"
    citations_root.mkdir(exist_ok=True)
    versions = sorted(
        int(path.name[1:])
        for path in citations_root.iterdir()
        if path.is_dir() and re.fullmatch(r"v[0-9]+", path.name)
    )
    version = f"v{(versions[-1] if versions else 0) + 1:04d}"
    target = citations_root / version
    staging = Path(tempfile.mkdtemp(prefix=".citations-", dir=citations_root))
    try:
        copied_input = staging / CITATION_NAME
        shutil.copyfile(preview["input_path"], copied_input)
        if sha256_file(copied_input) != preview["input_sha256"]:
            raise ValueError("citation.jsonl changed during confirmation")
        sources = []
        for source in preview["sources"]:
            original = Path(source["original_path"])
            if sha256_file(original) != source["sha256"]:
                raise ValueError(f"source file changed during confirmation: {original}")
            copied = staging / source["file"]
            copied.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, copied)
            if sha256_file(copied) != source["sha256"]:
                raise ValueError(f"source copy failed verification: {original}")
            sources.append({**source, "snapshot": source["file"]})
        manifest = {
            "version": version,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "citation_sha256": preview["input_sha256"],
            "plan_hash": plan_hash,
            "previous_version": state.get("active_citation"),
            "mapping_note": mapping_note.strip(),
            "source_counts": source_counts,
            "unverified_source_counts": preview["unverified_source_counts"],
            "source_coverage": preview["source_coverage"],
            "input_lines": preview["input_lines"],
            "user_confirmation": user_confirmation.strip(),
            "sources": sources,
        }
        (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(staging, target)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise

    relative = f"citations/{version}/{CITATION_NAME}"
    state["active_citation"] = relative
    write_json_atomic(workspace / MARKER, state)
    return {
        "status": "confirmed",
        "version": version,
        "citation_path": str(workspace / relative),
        "manifest_path": str(target / "manifest.json"),
        "citation_sha256": preview["input_sha256"],
        "citations": preview["citations"],
        "next_step": "call panner_deduplicate with this citation_path",
    }


def citation_status(workspace_path: str) -> dict:
    workspace, state = workspace_state(workspace_path)
    relative = state.get("active_citation")
    if not relative:
        return {"status": "not_confirmed", "workspace_path": str(workspace)}
    citation_file = workspace / relative
    manifest_file = citation_file.parent / "manifest.json"
    if not citation_file.is_file() or not manifest_file.is_file():
        raise ValueError("active citation.jsonl or its manifest is missing")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if sha256_file(citation_file) != manifest.get("citation_sha256"):
        raise ValueError("active citation.jsonl differs from the confirmed version")
    with citation_file.open("r", encoding="utf-8") as stream:
        count = sum(1 for _ in stream)
    return {
        "status": "confirmed",
        "version": manifest["version"],
        "citation_path": str(citation_file),
        "citation_sha256": manifest["citation_sha256"],
        "citations": count,
    }
