"""Fixed BibDedupe 0.11.0 adapter and reviewable citation-pool derivation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import pandas as pd
from bib_dedupe.bib_dedupe import block, cluster, match, prep

from .citations import _path_in_bundle, _unique_object, read_citations
from .workspace import MARKER, sha256_file, workspace_state, write_json_atomic


ENGINE_VERSION = "0.11.0"
POLICY_VERSION = "panner-dedup-v1"
OUTPUTS = ("citation-pool.jsonl", "dedup-review.jsonl", "dedup-manifest.json")


def _jsonl(rows: list[dict]) -> bytes:
    return "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _output_directory(path_text: str, input_file: Path) -> Path:
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        raise ValueError("output_dir must be an absolute path")
    path = path.resolve()
    if path == input_file or (path.exists() and not path.is_dir()):
        raise ValueError("output_dir must be a directory separate from citation.jsonl")
    if path.exists() and any(path.iterdir()):
        raise ValueError("output_dir must be new or empty")
    return path


def _confirmed_input(input_file: Path, rows: list[dict]) -> str:
    version_dir = input_file.parent
    if version_dir.parent.name != "citations" or not re.fullmatch(r"v[0-9]+", version_dir.name):
        raise ValueError("deduplication requires a confirmed PANNER citation.jsonl version")
    workspace, state = workspace_state(str(version_dir.parent.parent))
    if workspace != version_dir.parent.parent:
        raise ValueError("citation version is outside its PANNER workspace")
    if state.get("active_citation") != f"citations/{version_dir.name}/citation.jsonl":
        raise ValueError("deduplication requires the active confirmed citation version")
    manifest_file = version_dir / "manifest.json"
    if not manifest_file.is_file():
        raise ValueError("confirmed citation manifest is missing")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("confirmed citation manifest is invalid")
    if manifest.get("version") != version_dir.name or manifest.get("citation_sha256") != sha256_file(input_file):
        raise ValueError("citation.jsonl differs from its confirmed manifest")
    sources = manifest.get("sources")
    if not isinstance(sources, list):
        raise ValueError("confirmed citation manifest has no source list")
    snapshot_paths = set()
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("confirmed citation manifest has an invalid source entry")
        snapshot = source.get("snapshot")
        if not isinstance(snapshot, str) or not _path_in_bundle(snapshot):
            raise ValueError("citation source snapshot path is invalid")
        if snapshot in snapshot_paths:
            raise ValueError("citation source snapshot is listed twice")
        snapshot_paths.add(snapshot)
        copied = version_dir / snapshot
        if not copied.is_file() or sha256_file(copied) != source.get("sha256"):
            raise ValueError(f"citation source snapshot differs from its manifest: {snapshot}")
    source_counts = manifest.get("source_counts")
    if not isinstance(source_counts, dict) or set(source_counts) != snapshot_paths:
        raise ValueError("citation source list differs from its confirmed manifest")
    if {row["source"]["file"] for row in rows} - snapshot_paths:
        raise ValueError("citation.jsonl refers to an unconfirmed source file")
    return sha256_file(manifest_file)


def _bib_rows(rows: list[dict]) -> pd.DataFrame:
    converted = []
    for row in rows:
        entry_type = row["entry_type"] or "article"
        container = row["container_title"] or row["journal"]
        converted.append(
            {
                "ID": row["id"],
                "ENTRYTYPE": entry_type,
                "title": row["title"],
                "author": row["authors"],
                "year": "" if row["year"] is None else str(row["year"]),
                "journal": row["journal"] or (container if entry_type == "article" else ""),
                "booktitle": container if entry_type in {"inproceedings", "proceedings", "inbook"} else "",
                "container_title": container,
                "volume": row["volume"],
                "number": row["issue"],
                "pages": row["pages"],
                "abstract": row["abstract"],
                "doi": row["doi"],
                # BibDedupe excludes pairs within one nonempty search_set.
                # Source files are provenance, not mutually exclusive search sets.
                "search_set": "",
            }
        )
    return pd.DataFrame(converted)


def _pairs_and_clusters(rows: list[dict]) -> tuple[list[dict], list[list[str]]]:
    if version("bib-dedupe") != ENGINE_VERSION:
        raise ValueError(f"PANNER requires bib-dedupe {ENGINE_VERSION}")
    if len(rows) < 2:
        return [], []
    prepared = prep(_bib_rows(rows), cpu=1, verbosity_level=0)
    blocked = block(prepared, cpu=1, verbosity_level=0)
    matched = match(blocked, cpu=1, verbosity_level=0)
    known = {row["id"] for row in rows}
    labels: dict[tuple[str, str], set[str]] = defaultdict(set)
    for _, pair in matched.iterrows():
        first, second = str(pair["ID_1"]), str(pair["ID_2"])
        label = str(pair["duplicate_label"])
        if first not in known or second not in known or first == second:
            raise ValueError("BibDedupe returned an invalid citation pair")
        if label not in {"duplicate", "maybe"}:
            raise ValueError(f"BibDedupe returned an unknown pair label: {label}")
        labels[tuple(sorted((first, second)))].add(label)
    pairs = [
        {"id_1": first, "id_2": second, "label": next(iter(values)) if len(values) == 1 else "conflict"}
        for (first, second), values in sorted(labels.items())
    ]
    duplicate_rows = [
        {
            "ID_1": pair["id_1"],
            "ID_2": pair["id_2"],
            "search_set_1": "",
            "search_set_2": "",
            "duplicate_label": "duplicate",
        }
        for pair in pairs
        if pair["label"] == "duplicate"
    ]
    clusters = cluster(pd.DataFrame(duplicate_rows), verbosity_level=0) if duplicate_rows else []
    clusters = sorted([sorted(component) for component in clusters], key=lambda members: members[0])
    assigned = [member for component in clusters for member in component]
    if len(assigned) != len(set(assigned)) or any(member not in known for member in assigned):
        raise ValueError("BibDedupe returned overlapping or unknown duplicate components")
    return pairs, clusters


def _candidate_groups(pairs: list[dict]) -> list[list[str]]:
    parent: dict[str, str] = {}

    def find(identifier: str) -> str:
        parent.setdefault(identifier, identifier)
        root = identifier
        while parent[root] != root:
            root = parent[root]
        while parent[identifier] != identifier:
            following = parent[identifier]
            parent[identifier] = root
            identifier = following
        return root

    for pair in pairs:
        first, second = find(pair["id_1"]), find(pair["id_2"])
        if first != second:
            parent[max(first, second)] = min(first, second)
    groups: dict[str, list[str]] = defaultdict(list)
    for identifier in parent:
        groups[find(identifier)].append(identifier)
    return sorted((sorted(members) for members in groups.values()), key=lambda members: members[0])


def _load_resolutions(path_text: str | None) -> tuple[list[dict], str | None]:
    if path_text is None:
        return [], None
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        raise ValueError("resolutions_path must be an absolute path")
    path = path.resolve(strict=True)
    if not path.is_file():
        raise ValueError("resolutions_path is not a file")
    resolutions = []
    lines = path.read_text(encoding="utf-8").split("\n")
    if lines and not lines[-1].strip():
        lines.pop()  # a trailing newline terminates the last record; it is not a blank line
    for number, line in enumerate(lines, 1):
        if not line.strip():
            raise ValueError(f"resolution line {number} is blank")
        try:
            resolution = json.loads(line, object_pairs_hook=_unique_object)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"resolution line {number} is invalid JSON: {exc}") from exc
        if not isinstance(resolution, dict):
            raise ValueError(f"resolution line {number} must be an object")
        resolutions.append(resolution)
    return resolutions, sha256_file(path)


def _resolution_partitions(resolution: dict, members: list[str]) -> list[dict]:
    if set(resolution) != {"group", "partitions", "reason"} or not isinstance(resolution["reason"], str) or not resolution["reason"].strip():
        raise ValueError("each resolution needs group, partitions, and a nonempty reason")
    partitions = resolution["partitions"]
    if not isinstance(partitions, list) or not partitions:
        raise ValueError(f"{resolution['group']}: partitions must be a nonempty list")
    assigned = []
    for partition in partitions:
        if not isinstance(partition, dict) or set(partition) != {"members", "screen_id"}:
            raise ValueError(f"{resolution['group']}: each partition needs members and screen_id")
        group_members = partition["members"]
        if not isinstance(group_members, list) or not group_members or any(not isinstance(item, str) for item in group_members):
            raise ValueError(f"{resolution['group']}: partition members must be citation IDs")
        if partition["screen_id"] not in group_members:
            raise ValueError(f"{resolution['group']}: screen_id must belong to its partition")
        assigned.extend(group_members)
    if sorted(assigned) != sorted(members):
        raise ValueError(f"{resolution['group']}: partitions must cover each group member exactly once")
    return partitions


def _representative(members: list[str], rows_by_id: dict[str, dict], order: dict[str, int]) -> str:
    return min(members, key=lambda identifier: (not bool(rows_by_id[identifier]["abstract"].strip()), order[identifier]))


def _identifier_value(field: str, value: str) -> str:
    normalized = value.strip().casefold()
    if field == "doi":
        normalized = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", normalized)
    if field == "pmid":
        normalized = re.sub(r"^pmid:\s*", "", normalized)
    return normalized


def _identifier_conflicts(component: list[str], rows_by_id: dict[str, dict]) -> list[dict]:
    conflicts = []
    for field in ("doi", "pmid"):
        values = {
            identifier: rows_by_id[identifier][field]
            for identifier in component
            if _identifier_value(field, rows_by_id[identifier][field])
        }
        if len({_identifier_value(field, value) for value in values.values()}) > 1:
            conflicts.append({"field": field, "values": values})
    return conflicts


def _derive(rows: list[dict], pairs: list[dict], clusters: list[list[str]], resolutions: list[dict]) -> tuple[list[dict], list[dict], list[dict], dict]:
    groups = _candidate_groups(pairs)
    group_map = {f"G{index:06d}": members for index, members in enumerate(groups, 1)}
    by_group = {member: group for group, members in group_map.items() for member in members}
    resolution_map = {}
    for resolution in resolutions:
        group = resolution.get("group")
        if not isinstance(group, str) or group not in group_map or group in resolution_map:
            raise ValueError(f"unknown or repeated resolution group: {group}")
        _resolution_partitions(resolution, group_map[group])
        resolution_map[group] = resolution
    rows_by_id = {row["id"]: row for row in rows}
    order = {row["id"]: index for index, row in enumerate(rows)}
    duplicate_of: dict[str, str] = {}
    evidence: dict[str, dict] = {}
    pending_groups = set()
    review_rows = []
    component_by_id = {identifier: component for component in clusters for identifier in component}
    pairs_by_group: dict[str, list[dict]] = defaultdict(list)
    for pair in pairs:
        pairs_by_group[by_group[pair["id_1"]]].append(pair)
    clusters_by_group: dict[str, list[list[str]]] = defaultdict(list)
    for component in clusters:
        clusters_by_group[by_group[component[0]]].append(component)
    group_summaries = []

    for group, members in group_map.items():
        group_pairs = pairs_by_group[group]
        maybe_pairs = [pair for pair in group_pairs if pair["label"] != "duplicate"]
        conflicted_components = {
            tuple(component_by_id[pair["id_1"]])
            for pair in maybe_pairs
            if pair["id_1"] in component_by_id
            and pair["id_2"] in component_by_id
            and component_by_id[pair["id_1"]] is component_by_id[pair["id_2"]]
        }
        identifier_conflicts = []
        for component in clusters_by_group[group]:
            for conflict in _identifier_conflicts(component, rows_by_id):
                conflicted_components.add(tuple(component))
                identifier_conflicts.append({"members": component, **conflict})
        resolved = group in resolution_map
        if resolved:
            resolution = resolution_map[group]
            for partition in _resolution_partitions(resolution, members):
                representative = partition["screen_id"]
                for identifier in partition["members"]:
                    if identifier != representative:
                        duplicate_of[identifier] = representative
                        evidence[identifier] = {"source": "user_resolution", "group": group, "reason": resolution["reason"]}
        else:
            if maybe_pairs:
                pending_groups.add(group)
            for component in clusters_by_group[group]:
                if tuple(component) in conflicted_components:
                    pending_groups.add(group)
                    continue
                representative = _representative(component, rows_by_id, order)
                for identifier in component:
                    if identifier != representative:
                        duplicate_of[identifier] = representative
                        evidence[identifier] = {
                            "source": "bib-dedupe", "version": ENGINE_VERSION, "classification": "duplicate", "group": group
                        }
        for pair in maybe_pairs:
            same_component = (
                pair["id_1"] in component_by_id
                and pair["id_2"] in component_by_id
                and component_by_id[pair["id_1"]] is component_by_id[pair["id_2"]]
            )
            review_rows.append(
                {
                    "group": group,
                    "id_1": pair["id_1"],
                    "id_2": pair["id_2"],
                    "title_1": rows_by_id[pair["id_1"]]["title"],
                    "title_2": rows_by_id[pair["id_2"]]["title"],
                    "library_label": pair["label"],
                    "kind": "intragroup_conflict" if same_component or pair["label"] == "conflict" else "maybe",
                    "status": "resolved" if resolved else "pending",
                }
            )
        for conflict in identifier_conflicts:
            review_rows.append(
                {
                    "group": group,
                    "kind": "identifier_conflict",
                    "members": conflict["members"],
                    "field": conflict["field"],
                    "values": conflict["values"],
                    "status": "resolved" if resolved else "pending",
                }
            )
        group_summaries.append(
            {"group": group, "members": members, "duplicate_pairs": sum(pair["label"] == "duplicate" for pair in group_pairs),
             "maybe_or_conflict_pairs": len(maybe_pairs), "identifier_conflicts": len(identifier_conflicts),
             "status": "resolved" if resolved else ("pending" if group in pending_groups else "automatic")}
        )

    pool = []
    for row in rows:
        identifier = row["id"]
        pool.append(
            {**row, "duplicate_of": duplicate_of.get(identifier), "duplicate_evidence": evidence.get(identifier),
             "review_group": by_group[identifier] if identifier in by_group and by_group[identifier] in pending_groups else None}
        )
    for row in pool:
        target = row["duplicate_of"]
        if target is not None and (target not in rows_by_id or target in duplicate_of):
            raise ValueError("derived duplicate_of does not point directly to a screened citation")
    counts = {
        "imported": len(rows),
        "duplicates": len(duplicate_of),
        "to_screen": len(rows) - len(duplicate_of),
        "automatic_groups": sum(group["status"] == "automatic" for group in group_summaries),
        "pending_groups": len(pending_groups),
        "resolved_groups": len(resolution_map),
        "review_items": len(review_rows),
        "default_article_count": sum(not row["entry_type"] for row in rows),
    }
    return pool, review_rows, group_summaries, counts


def _plan(input_path: str, output_dir: str, resolutions_path: str | None) -> dict:
    input_file, rows, issues = read_citations(input_path)
    if issues:
        raise ValueError("citation.jsonl is invalid: " + "; ".join(issues[:10]))
    input_manifest_hash = _confirmed_input(input_file, rows)
    output = _output_directory(output_dir, input_file)
    resolutions, resolution_hash = _load_resolutions(resolutions_path)
    pairs, clusters = _pairs_and_clusters(rows)
    pool, review, groups, counts = _derive(rows, pairs, clusters, resolutions)
    pool_bytes, review_bytes = _jsonl(pool), _jsonl(review)
    input_hash = sha256_file(input_file)
    runtime_versions = {
        "python": sys.version.split()[0],
        **{package: version(package) for package in ("bib-dedupe", "pandas", "numpy", "rapidfuzz", "number-parser")},
    }
    payload = {
        "input_path": str(input_file), "input_sha256": input_hash, "input_manifest_sha256": input_manifest_hash,
        "output_dir": str(output),
        "resolution_sha256": resolution_hash, "engine": "bib-dedupe", "engine_version": ENGINE_VERSION,
        "policy_version": POLICY_VERSION,
        "runtime_versions": runtime_versions,
        "pool_sha256": _sha256(pool_bytes), "review_sha256": _sha256(review_bytes),
        "pairs_sha256": _sha256(json.dumps(pairs, ensure_ascii=False, sort_keys=True).encode("utf-8")),
        "groups_sha256": _sha256(json.dumps(groups, ensure_ascii=False, sort_keys=True).encode("utf-8")),
        "counts": counts,
    }
    plan_hash = _sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    preview_groups = sorted(groups, key=lambda group: (group["status"] != "pending", group["members"][0]))[:20]
    return {
        "status": "planned", "plan_hash": plan_hash, "input_path": str(input_file), "output_dir": str(output),
        "input_sha256": input_hash, "input_manifest_sha256": input_manifest_hash,
        "engine": "bib-dedupe", "engine_version": ENGINE_VERSION,
        "policy_version": POLICY_VERSION,
        "runtime_versions": runtime_versions,
        "counts": counts, "groups_preview": preview_groups, "remaining_groups": max(0, len(groups) - 20),
        "remaining_pending_groups": counts["pending_groups"] - sum(group["status"] == "pending" for group in preview_groups),
        "files_to_write": list(OUTPUTS), "resolution_sha256": resolution_hash,
        "_pool_bytes": pool_bytes, "_review_bytes": review_bytes, "_pairs": pairs, "_groups": groups,
        "_resolutions": resolutions,
    }


def deduplicate(input_path: str, output_dir: str, resolutions_path: str | None = None, confirm: str | None = None, user_confirmation: str | None = None) -> dict:
    """Preview or confirm one fixed BibDedupe run; never modify input citations."""
    plan = _plan(input_path, output_dir, resolutions_path)
    public = {key: value for key, value in plan.items() if not key.startswith("_")}
    if confirm is None:
        return public
    if confirm != plan["plan_hash"]:
        raise ValueError("deduplication plan changed; preview again before confirmation")
    if user_confirmation is not None and not user_confirmation.strip():
        raise ValueError("record the user's explicit confirmation of the deduplication plan")
    output = Path(plan["output_dir"])
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".dedup-", dir=output.parent))
    try:
        pool_file = staging / OUTPUTS[0]
        review_file = staging / OUTPUTS[1]
        pool_file.write_bytes(plan["_pool_bytes"])
        review_file.write_bytes(plan["_review_bytes"])
        manifest = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "plan_hash": plan["plan_hash"],
            "user_confirmation": user_confirmation.strip() if user_confirmation else None,
            "input_path": plan["input_path"],
            "input_sha256": plan["input_sha256"],
            "input_manifest_sha256": plan["input_manifest_sha256"],
            "engine": plan["engine"],
            "engine_version": plan["engine_version"],
            "policy_version": plan["policy_version"],
            "runtime_versions": plan["runtime_versions"],
            "resolution_sha256": plan["resolution_sha256"],
            "resolutions": plan["_resolutions"],
            "counts": plan["counts"],
            "pairs": plan["_pairs"],
            "groups": plan["_groups"],
            "outputs": {OUTPUTS[0]: sha256_file(pool_file), OUTPUTS[1]: sha256_file(review_file)},
        }
        (staging / OUTPUTS[2]).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(staging, output)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    workspace, state = workspace_state(str(Path(plan["input_path"]).parent.parent.parent))
    state["active_pool"] = str(output / OUTPUTS[0])
    write_json_atomic(workspace / MARKER, state)
    return {
        "status": "confirmed", "plan_hash": plan["plan_hash"], "output_dir": str(output),
        "citation_pool_path": str(output / OUTPUTS[0]), "review_path": str(output / OUTPUTS[1]),
        "manifest_path": str(output / OUTPUTS[2]), "counts": plan["counts"],
    }


def dedup_status(workspace_path: str) -> dict:
    workspace, state = workspace_state(workspace_path)
    pool_path = state.get("active_pool")
    if not pool_path:
        return {"status": "not_confirmed", "workspace_path": str(workspace)}
    pool_file = Path(pool_path).resolve(strict=True)
    manifest_file = pool_file.parent / OUTPUTS[2]
    if not manifest_file.is_file():
        raise ValueError("active citation pool manifest is missing")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if manifest.get("outputs", {}).get(OUTPUTS[0]) != sha256_file(pool_file):
        raise ValueError("active citation pool differs from its manifest")
    citation_relative = state.get("active_citation")
    if not citation_relative or Path(manifest.get("input_path", "")).resolve() != workspace / citation_relative:
        raise ValueError("active citation pool does not derive from the active citations")
    if manifest.get("input_sha256") != sha256_file(workspace / citation_relative):
        raise ValueError("active citation pool input differs from the current citations")
    return {"status": "confirmed", "workspace_path": str(workspace), "pool_path": str(pool_file), "counts": manifest["counts"]}
