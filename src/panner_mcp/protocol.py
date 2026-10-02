"""Protocol compilation for step 03: validate, trial-read, and freeze.

The agent drafts `signals` and `decision_rules`; this module checks structure,
criteria coverage and quote provenance, trial-evaluates the rules against
annotated scenarios, and freezes an immutable protocol version only after the
user confirms the complete preview.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .citations import _unique_object
from .evaluate import ANSWERS, EXPLICIT, evaluate_rules
from .workspace import MARKER, sha256_file, workspace_state, write_json_atomic

PROTOCOL_NAME = "protocol.json"
PROTOCOL_FORMAT = "panner-protocol-v1"
EVIDENCE_FIELDS = (
    "title", "abstract", "authors", "journal", "container_title", "volume", "issue", "pages",
    "publication_types", "doi", "pmid", "entry_type", "year",
)
RULE_FIELDS = ("title", "abstract")  # never matched by substring patterns; semantic reading only
SIGNAL_UNITS = ("study", "population", "arm", "regimen", "citation_text")
RECORD_UNITS = ("study", "population", "citation_text")
OBJECT_UNITS = ("arm", "regimen")
COMPARISON_UNITS = ("arm", "regimen")  # comparisons are expressed by rules, never by signals
EVAL_MODES = ("field_rule", "semantic")
ROLES = ("eligibility", "review_outcome", "retrieval", "data_handling", "context")
TARGETS = ("study", "population", "arm", "regimen", "comparison", "citation_text", "workflow")
HANDOFF_STAGES = ("full_text", "retrieval", "data_collection")
STATUSES = ("excluded_candidate", "retained")
TOP_LEVEL_KEYS = {
    "format", "signals", "decision_rules", "criteria_map", "clarifications", "relevance", "screening_review"
}
SIGNAL_KEYS = {"id", "unit", "question", "core", "evaluation_mode", "evidence_fields", "answer_criteria", "criterion_refs"}
RULE_KEYS = {"id", "kind", "unit", "quantifier", "condition", "criterion_refs"}
MAP_KEYS = {"id", "quote", "occurrence", "role", "target", "signal_ids", "rule_ids", "handoffs"}
MAP_OPTIONAL_KEYS = {"decisions"}
FACET_KEYS = {"id", "aspect", "question"}
TRIAL_KEYS = {"citation_id", "scenario", "entities", "answers", "expected_status"}
TRIAL_OPTIONAL_KEYS = {"pool", "user_feedback"}
# Protocol content is written in English; quoted criteria wording and user
# decision records keep the user's language. CJK text in a content field is
# therefore a drafting error the validator reports.
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]")


def _requires_english(label: str, field: str, value: object, issues: list[str]) -> None:
    if isinstance(value, str) and _CJK.search(value):
        issues.append(f"{label}: {field} must be in English (the protocol execution language)")


def _identifier(kind: str, value: object) -> str | None:
    if isinstance(value, str) and re.fullmatch(rf"{kind}[0-9]+", value):
        return value
    return None


def _check_signal(signal: dict, criteria: dict, issues: list[str]) -> None:
    label = _identifier("S", signal.get("id")) or "signal"
    if signal["evaluation_mode"] not in EVAL_MODES:
        issues.append(f"{label}: unknown evaluation_mode {signal['evaluation_mode']!r}")
        return
    expected_keys = SIGNAL_KEYS | ({"field_rule"} if signal["evaluation_mode"] == "field_rule" else set())
    if set(signal) != expected_keys:
        issues.append(f"{label}: expected fields {sorted(expected_keys)}; got {sorted(signal)}")
        return
    if signal["unit"] not in SIGNAL_UNITS:
        issues.append(f"{label}: unknown unit {signal['unit']!r}")
    if not isinstance(signal["question"], str) or not signal["question"].strip():
        issues.append(f"{label}: question must not be empty")
    _requires_english(label, "question", signal["question"], issues)
    if not isinstance(signal["core"], bool):
        issues.append(f"{label}: core must be a boolean")
    fields = signal["evidence_fields"]
    if not fields or len(set(fields)) != len(fields) or any(field not in EVIDENCE_FIELDS for field in fields):
        issues.append(f"{label}: evidence_fields must be unique citation fields from {list(EVIDENCE_FIELDS)}")
    answers = signal["answer_criteria"]
    if (
        not isinstance(answers, dict)
        or set(answers) != set(ANSWERS)
        or any(not isinstance(text, str) or not text.strip() for text in answers.values())
    ):
        issues.append(f"{label}: answer_criteria must define nonempty conditions for {list(ANSWERS)}")
    else:
        for answer, text in answers.items():
            _requires_english(label, f"answer_criteria.{answer}", text, issues)
    refs = signal["criterion_refs"]
    if not refs:
        issues.append(f"{label}: every signal must cite at least one criteria clause")
    for ref in refs:
        if (
            not isinstance(ref, dict)
            or set(ref) != {"criterion_id", "quote"}
            or not isinstance(ref.get("quote"), str)
            or ref["criterion_id"] not in criteria
        ):
            issues.append(f"{label}: criterion_refs must point to a criteria_map clause")
            continue
        if ref["quote"] not in criteria[ref["criterion_id"]]["_text"]:
            issues.append(f"{label}: quote is not verbatim in clause {ref['criterion_id']}")
    if signal["evaluation_mode"] == "field_rule":
        if signal["unit"] in OBJECT_UNITS:
            issues.append(f"{label}: field_rule reads citation-level structured fields and requires a record-level unit")
        rule = signal.get("field_rule")
        if not isinstance(rule, dict) or set(rule) != {"field", "matches"}:
            issues.append(f"{label}: field_rule needs exactly field and matches")
            return
        if rule["field"] in RULE_FIELDS or rule["field"] not in EVIDENCE_FIELDS:
            issues.append(
                f"{label}: field_rule must read a structured field; title and abstract use evaluation_mode semantic"
            )
        matches = rule["matches"]
        if (
            not isinstance(matches, list)
            or not matches
            or any(
                not isinstance(item, dict)
                or set(item) != {"value", "answer"}
                or not isinstance(item.get("value"), str)
                or not item["value"].strip()
                or item["answer"] not in ANSWERS
                or item["answer"] == "unclear"
                for item in matches
            )
        ):
            issues.append(
                f"{label}: each field_rule match needs a nonempty value and an explicit answer; "
                "unlisted values are fixed to unclear"
            )


def _check_leaf(leaf: dict, rules_unit: str, signals: dict, issues: list[str], label: str) -> None:
    if set(leaf) != {"signal", "on", "expect"}:
        issues.append(f"{label}: a condition leaf needs exactly signal, on, expect")
        return
    if leaf["expect"] not in EXPLICIT:
        issues.append(f"{label}: expect must be hit or miss; partial and unclear never justify exclusion")
    signal = signals.get(leaf["signal"])
    if signal is None:
        issues.append(f"{label}: condition cites unknown signal {leaf['signal']!r}")
        return
    unit = signal["unit"]
    if rules_unit == "record":
        if leaf["on"] != "record" or unit not in RECORD_UNITS:
            issues.append(f"{label}: record rules read record-level signals via on=record")
    elif rules_unit == "arms":
        if leaf["on"] != "each" or unit not in OBJECT_UNITS:
            issues.append(f"{label}: group-level rules read group signals via on=each")
    else:
        if leaf["on"] == "record":
            if unit not in RECORD_UNITS:
                issues.append(f"{label}: on=record leaves must cite record-level signals")
        elif leaf["on"] in {"member0", "member1"}:
            if unit not in COMPARISON_UNITS:
                issues.append(
                    f"{label}: comparison members must cite arm or regimen signals; "
                    "the comparison itself is expressed by the rule, not by a signal"
                )
        else:
            issues.append(f"{label}: comparison leaves use on=member0, member1, or record")


def _check_condition(condition: dict, rules_unit: str, signals: dict, issues: list[str], label: str) -> None:
    if not isinstance(condition, dict):
        issues.append(f"{label}: condition nodes are objects with one of signal, not, all, any")
        return
    keys = set(condition)
    if "signal" in keys:
        _check_leaf(condition, rules_unit, signals, issues, label)
        return
    if keys == {"not"}:
        _check_condition(condition["not"], rules_unit, signals, issues, label)
        return
    if keys == {"all"} or keys == {"any"}:
        children = condition["all" if "all" in keys else "any"]
        if not isinstance(children, list) or not children:
            issues.append(f"{label}: all and any need a nonempty list")
            return
        for child in children:
            _check_condition(child, rules_unit, signals, issues, label)
        return
    issues.append(f"{label}: condition nodes use exactly one of signal, not, all, any")


def _check_rule(rule: dict, signals: dict, criteria: dict, issues: list[str]) -> None:
    label = _identifier("R", rule.get("id")) or "rule"
    if not isinstance(rule, dict) or set(rule) != RULE_KEYS:
        issues.append(f"{label}: expected fields {sorted(RULE_KEYS)}; got {sorted(rule)}")
        return
    kind, unit, quantifier = rule["kind"], rule["unit"], rule["quantifier"]
    if kind == "disqualifier":
        if unit not in {"record", "arms"}:
            issues.append(f"{label}: disqualifier unit is record or arms")
        elif unit == "arms" and quantifier not in {"any", "all"}:
            issues.append(f"{label}: group-level disqualifiers use quantifier any or all")
        elif unit == "record" and quantifier != "all":
            issues.append(f"{label}: record rules evaluate once and use quantifier all")
    elif kind == "qualification":
        if unit != "comparison" or quantifier != "exists":
            issues.append(f"{label}: qualification rules check that at least one comparison qualifies")
    else:
        issues.append(f"{label}: unknown rule kind {kind!r}")
        return
    if not rule["criterion_refs"] or any(
        not isinstance(ref, dict) or set(ref) != {"criterion_id", "quote"} or ref["criterion_id"] not in criteria
        for ref in rule["criterion_refs"]
    ):
        issues.append(f"{label}: every rule must cite at least one criteria_map clause")
    if not isinstance(rule["condition"], dict):
        issues.append(f"{label}: condition must be an object")
        return
    _check_condition(rule["condition"], "comparison" if kind == "qualification" else unit, signals, issues, label)


def _check_relevance(relevance: dict, signals: list[dict], issues: list[str]) -> None:
    if not isinstance(relevance, dict) or set(relevance) != {"enabled", "facets", "note"}:
        issues.append("relevance needs exactly enabled, facets, note")
        return
    core = [signal for signal in signals if signal["core"]]
    if relevance["enabled"]:
        facets = relevance["facets"]
        if (
            not isinstance(facets, list)
            or not facets
            or any(
                not isinstance(facet, dict)
                or set(facet) != FACET_KEYS
                or not isinstance(facet.get("question"), str)
                or not facet["question"].strip()
                for facet in facets
            )
        ):
            issues.append("enabled relevance routing needs facets with id, aspect, question")
        else:
            for facet in facets:
                _requires_english("relevance", f"facet {facet['id']} aspect", facet["aspect"], issues)
                _requires_english("relevance", f"facet {facet['id']} question", facet["question"], issues)
    elif core:
        issues.append("core signals exist but relevance routing is disabled; enable it or drop the core marks")
    else:
        if not (isinstance(relevance["note"], str) and relevance["note"].strip()):
            issues.append("without core signals, relevance.note must explain why core routing is off")
        else:
            _requires_english("relevance", "note", relevance["note"], issues)


def _check_trials(trials: list[dict], signals: dict, issues: list[str]) -> None:
    for trial in trials:
        label = trial.get("citation_id") or "scenario"
        if not isinstance(trial, dict) or not TRIAL_KEYS <= set(trial) <= TRIAL_KEYS | TRIAL_OPTIONAL_KEYS:
            issues.append(f"trial {label}: expected fields {sorted(TRIAL_KEYS)} plus optional user_feedback")
            continue
        if not isinstance(trial["scenario"], str) or not trial["scenario"].strip():
            issues.append(f"trial {label}: scenario must describe what the trial case exercises")
        else:
            _requires_english(f"trial {label}", "scenario", trial["scenario"], issues)
        if trial["expected_status"] not in STATUSES:
            issues.append(f"trial {label}: expected_status is excluded_candidate or retained")
        entities = trial["entities"]
        arms = entities.get("arms", []) if isinstance(entities, dict) else None
        if not isinstance(arms, list) or any(not isinstance(arm, str) or not arm for arm in arms) or len(set(arms)) != len(arms):
            issues.append(f"trial {label}: entities.arms must name distinct objects")
        if not isinstance(trial["answers"], list):
            issues.append(f"trial {label}: answers must be a list")
            continue
        for item in trial["answers"]:
            if (
                not isinstance(item, dict)
                or set(item) != {"signal", "on", "answer"}
                or item["signal"] not in signals
                or item["answer"] not in ANSWERS
            ):
                issues.append(f"trial {label}: answers cite protocol signals with four-value answers")
                break


def _check_draft(draft: dict, criteria_text: str) -> tuple[list[str], dict, dict]:
    issues: list[str] = []
    if not isinstance(draft, dict) or set(draft) != TOP_LEVEL_KEYS:
        return {}, {}, [f"draft must contain exactly {sorted(TOP_LEVEL_KEYS)}"]
    if draft["format"] != PROTOCOL_FORMAT:
        issues.append(f"draft format must be {PROTOCOL_FORMAT}")
    if not isinstance(draft["clarifications"], list) or any(not isinstance(item, str) or not item.strip() for item in draft["clarifications"]):
        issues.append("clarifications must be a list of nonempty open questions")

    criteria: dict[str, dict] = {}
    for clause in draft["criteria_map"]:
        label = _identifier("E", clause.get("id")) if isinstance(clause, dict) else None
        if (
            not isinstance(clause, dict)
            or not set(MAP_KEYS) <= set(clause) <= MAP_KEYS | MAP_OPTIONAL_KEYS
        ):
            issues.append(f"criteria_map clause {label or '?'}: expected {sorted(MAP_KEYS)} plus optional decisions")
            continue
        label = label or clause["id"]
        if clause["id"] in criteria:
            issues.append(f"{label}: criteria clause id is repeated")
        record = {
            "clause": clause,
            "_text": criteria_text,
            "quote_ok": isinstance(clause["quote"], str) and clause["quote"] in criteria_text,
        }
        criteria[clause["id"]] = record
        if not record["quote_ok"]:
            issues.append(f"{label}: quote is not verbatim in the confirmed criteria text")
        if clause["role"] not in ROLES:
            issues.append(f"{label}: unknown role {clause['role']!r}")
        if clause["target"] not in TARGETS:
            issues.append(f"{label}: unknown target {clause['target']!r}")
        if not isinstance(clause["occurrence"], int) or clause["occurrence"] < 1:
            issues.append(f"{label}: occurrence counts start at 1")
        if any(handoff.get("stage") not in HANDOFF_STAGES or not str(handoff.get("task", "")).strip() for handoff in clause["handoffs"]):
            issues.append(f"{label}: handoffs need a stage in {list(HANDOFF_STAGES)} and a nonempty task")
        for handoff in clause["handoffs"]:
            _requires_english(label, "handoffs.task", handoff.get("task"), issues)
        decisions = clause.get("decisions", [])
        if decisions and (
            not isinstance(decisions, list) or any(not isinstance(item, str) or not item.strip() for item in decisions)
        ):
            issues.append(f"{label}: decisions must be a list of nonempty user decisions")
        for decision in decisions if isinstance(decisions, list) else []:
            _requires_english(label, "decisions", decision, issues)
        if clause["role"] == "eligibility" and not (
            clause["signal_ids"] or clause["rule_ids"] or clause["handoffs"] or decisions
        ):
            issues.append(
                f"{label}: eligibility clause has no signal, rule, handoff, or explicit no-restriction decision"
            )

    signals: dict[str, dict] = {}
    for signal in draft["signals"]:
        if isinstance(signal, dict) and isinstance(signal.get("id"), str):
            if signal["id"] in signals:
                issues.append(f"{signal['id']}: signal id is repeated")
            signals[signal["id"]] = signal
        _check_signal(signal if isinstance(signal, dict) else {}, criteria, issues)

    used: set[str] = set()

    def collect(condition: dict) -> None:
        if not isinstance(condition, dict):
            return
        if "signal" in condition:
            used.add(condition["signal"])
        elif "not" in condition:
            collect(condition["not"])
        else:
            for child in condition.get("all", condition.get("any", [])):
                collect(child)

    rules: dict[str, dict] = {}
    for rule in draft["decision_rules"]:
        if isinstance(rule, dict):
            if isinstance(rule.get("condition"), dict):
                collect(rule["condition"])
            if isinstance(rule.get("id"), str):
                if rule["id"] in rules:
                    issues.append(f"{rule['id']}: rule id is repeated")
                rules[rule["id"]] = rule
        _check_rule(rule if isinstance(rule, dict) else {}, signals, criteria, issues)

    for signal in draft["signals"]:
        if isinstance(signal, dict) and not signal["core"] and signal["id"] not in used:
            issues.append(f"{signal['id']}: no rule reads this signal; mark it core or remove it")

    for clause in draft["criteria_map"]:
        for signal_id in clause["signal_ids"]:
            if signal_id not in signals:
                issues.append(f"{clause['id']}: cites unknown signal {signal_id!r}")
        for rule_id in clause["rule_ids"]:
            if rule_id not in rules:
                issues.append(f"{clause['id']}: cites unknown rule {rule_id!r}")

    _check_relevance(draft["relevance"], draft["signals"], issues)
    _check_trials(draft["screening_review"], signals, issues)
    return draft, criteria, issues


def _confirmed_criteria(criteria_path: str, workspace: Path) -> tuple[Path, dict, str]:
    path = Path(criteria_path).expanduser()
    if not path.is_absolute():
        raise ValueError("criteria_path must be an absolute path")
    path = path.resolve(strict=True)
    version_dir = path.parent
    if version_dir.parent.name != "criteria" or not re.fullmatch(r"v[0-9]+", version_dir.name):
        raise ValueError("compilation requires a confirmed PANNER eligibility-criteria version")
    if version_dir.parent.parent != workspace:
        raise ValueError("criteria version is outside its PANNER workspace")
    manifest_file = version_dir / "manifest.json"
    if not manifest_file.is_file():
        raise ValueError("confirmed criteria manifest is missing")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if manifest.get("version") != version_dir.name or manifest.get("criteria_sha256") != sha256_file(path):
        raise ValueError("eligibility-criteria.md differs from its confirmed manifest")
    return path, manifest, path.read_text(encoding="utf-8")


def _evaluate_trials(draft: dict) -> tuple[list[dict], list[dict]]:
    evaluations, mismatches = [], []
    for trial in draft["screening_review"]:
        if not isinstance(trial, dict) or not TRIAL_KEYS <= set(trial):
            continue  # structural problems are already reported by the checker
        judgment = {
            "entities": trial["entities"] if isinstance(trial["entities"], dict) else {},
            "answers": [item for item in trial["answers"] if isinstance(item, dict)] if isinstance(trial["answers"], list) else [],
        }
        result = evaluate_rules(draft["decision_rules"], judgment)
        evaluations.append({"citation_id": trial["citation_id"], "scenario": trial["scenario"], **result})
        if result["status"] != trial["expected_status"]:
            mismatches.append(
                {
                    "citation_id": trial["citation_id"],
                    "scenario": trial["scenario"],
                    "expected_status": trial["expected_status"],
                    "evaluated_status": result["status"],
                }
            )
    return evaluations, mismatches


def _summarize(draft: dict) -> dict:
    rules = [rule for rule in draft["decision_rules"] if isinstance(rule, dict)]
    return {
        "signals": len(draft["signals"]),
        "core_signals": sum(isinstance(signal, dict) and signal.get("core") is True for signal in draft["signals"]),
        "rules": len(rules),
        "disqualifiers": sum(rule.get("kind") == "disqualifier" for rule in rules),
        "qualifications": sum(rule.get("kind") == "qualification" for rule in rules),
        "criteria_clauses": len(draft["criteria_map"]),
        "open_clarifications": len(draft["clarifications"]) if isinstance(draft["clarifications"], list) else 0,
        "trial_scenarios": len(draft["screening_review"]),
    }


GROUP_UNITS = ("arm", "regimen")


def _veto_leaves(condition: object, units: dict[str, object]) -> list[str]:
    """Group-level signals that a `not` node rejects, i.e. per-member vetoes of a comparison."""
    if not isinstance(condition, dict):
        return []
    if "not" in condition:
        inner = condition["not"]
        if isinstance(inner, dict) and "signal" in inner:
            return [inner["signal"]] if units.get(inner["signal"]) in GROUP_UNITS else []
        return _veto_leaves(inner, units)
    found: list[str] = []
    for child in condition.get("all", condition.get("any", [])) or []:
        found.extend(_veto_leaves(child, units))
    return found


def _condition_signals(condition: dict, found: set) -> None:
    """Collect every signal id referenced anywhere inside a rule condition."""
    if not isinstance(condition, dict):
        return
    if isinstance(condition.get("signal"), str):
        found.add(condition["signal"])
        return
    for key in ("not", "all", "any"):
        child = condition.get(key)
        if isinstance(child, dict):
            _condition_signals(child, found)
        elif isinstance(child, list):
            for item in child:
                _condition_signals(item, found)


def _lint_draft(draft: dict) -> list[str]:
    """Report rule structures that give one object's answer the power to exclude a whole record.

    These are not schema errors: the structures are legal and sometimes intended. They are
    reported because a single wrong group-level answer then removes every candidate comparison
    at once, which is how a correct decision can be implemented dangerously.
    """
    warnings: list[str] = []
    units = {signal["id"]: signal.get("unit") for signal in draft.get("signals", []) if isinstance(signal, dict)}
    referenced: set = set()
    for rule in draft.get("decision_rules", []):
        if isinstance(rule, dict):
            _condition_signals(rule.get("condition", {}), referenced)
    for signal in draft.get("signals", []):
        if not isinstance(signal, dict):
            continue
        sid = signal.get("id")
        if sid and sid not in referenced:
            clause_note = ""
            for ref in signal.get("criterion_refs", []) or []:
                clause_note = f" (criterion {ref.get('criterion_id')})"
                break
            warnings.append(
                f"{sid}{clause_note}: this signal's answers are consumed by no decision rule, so the clause can "
                "never contribute to an exclusion; the record stays retained whatever the citation says. If the "
                "clause is meant to exclude (e.g. an inclusion criterion whose explicit violation should disqualify), "
                "add a disqualifier over it; if it is meant only for review ordering or topic routing, confirm that "
                "this is intended."
            )
    for rule in draft.get("decision_rules", []):
        if not isinstance(rule, dict):
            continue
        label = _identifier("R", rule.get("id")) or "rule"
        if rule.get("kind") == "qualification":
            vetoed = sorted(set(_veto_leaves(rule.get("condition", {}), units)))
            if vetoed:
                warnings.append(
                    f"{label}: the comparison rule vetoes candidate comparisons with group-level not-leaves over "
                    f"{', '.join(vetoed)}; one group's answer can defeat every comparison. Express the restriction in "
                    "the answer criteria of the intervention signal, or as a record-level disqualifier."
                )
        if rule.get("kind") == "disqualifier" and rule.get("unit") == "arms" and rule.get("quantifier") == "any":
            warnings.append(
                f"{label}: an arms/any disqualifier excludes the whole record as soon as one group satisfies the "
                "condition; confirm that the restriction acts on the record and not only on the comparison."
            )
    return warnings


def preview_compile(workspace_path: str, criteria_path: str, draft_path: str) -> dict:
    """Validate the agent's draft, trial-evaluate rules, and return the plan hash; write nothing."""
    workspace, state = workspace_state(workspace_path)
    criteria_file, manifest, criteria_text = _confirmed_criteria(criteria_path, workspace)
    if state.get("active_eligibility_criteria") != str(criteria_file.relative_to(workspace)):
        raise ValueError("compilation requires the active confirmed criteria version")
    draft_file = Path(draft_path).expanduser()
    if not draft_file.is_absolute():
        raise ValueError("draft_path must be an absolute path")
    draft_file = draft_file.resolve(strict=True)
    try:
        draft = json.loads(draft_file.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"protocol draft is not valid JSON: {exc}") from exc
    draft, _, issues = _check_draft(draft, criteria_text)
    evaluations: list[dict] = []
    mismatches: list[dict] = []
    counts: dict = {}
    warnings: list[str] = []
    if draft:
        evaluations, mismatches = _evaluate_trials(draft)
        counts = _summarize(draft)
        warnings = _lint_draft(draft)
        if draft["clarifications"]:
            names = draft["clarifications"] if isinstance(draft["clarifications"], list) else []
            issues.append("resolve every clarification with the user before freezing; open questions: " + "; ".join(str(name) for name in names))
        if mismatches:
            issues.append("trial scenarios disagree with expected outcomes; the rules do not yet express the user's decisions")
    payload = {
        "workspace": str(workspace),
        "previous_version": state.get("active_protocol"),
        "criteria_version": manifest["version"],
        "criteria_sha256": manifest["criteria_sha256"],
        "draft_sha256": sha256_file(draft_file),
    }
    plan_hash = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return {
        "status": "ready_for_confirmation" if draft and not issues else "draft",
        "workspace_path": str(workspace),
        "criteria_version": manifest["version"],
        "criteria_sha256": manifest["criteria_sha256"],
        "counts": counts,
        "issues": issues,
        "warnings": warnings,
        "trial_evaluations": evaluations,
        "trial_mismatches": mismatches,
        "plan_hash": plan_hash,
        "guarantees": [
            "single answers never exclude: conclusions come only from decision_rules evaluated by the frozen evaluator",
            "undetermined answers and incomplete object enumerations leave the citation retained",
        ],
    }


LABELS_DIR = "labels"
LABELS_NAME = "included.txt"
CALIBRATION_DIR = "calibration"


def calibration_report_path(workspace: Path, draft_file: Path) -> Path:
    """The calibration report is bound to the draft it judged."""
    return workspace / CALIBRATION_DIR / f"compile-{sha256_file(draft_file)[:12]}.json"


def _require_calibration(workspace: Path, draft_file: Path) -> None:
    """A workspace that carries a labeled included set must pass the calibration read before freezing."""
    labels_file = workspace / LABELS_DIR / LABELS_NAME
    report_file = calibration_report_path(workspace, draft_file)
    if report_file.is_file():
        report = json.loads(report_file.read_text(encoding="utf-8"))
        if report.get("draft_sha256") != sha256_file(draft_file):
            raise ValueError("the calibration report belongs to another draft; run the calibration read again")
        excluded = report.get("excluded_labels") or []
        if excluded:
            detail = "; ".join(
                f"{item.get('label')} excluded by {', '.join(item.get('rules') or []) or 'unknown rule'}"
                for item in excluded[:5]
            )
            raise ValueError(
                f"the protocol excludes {len(excluded)} of the {report.get('mapped')} labeled included records: {detail}. "
                "Repair the protocol, or record the user's explicit decision to drop these records."
            )
        return
    if labels_file.is_file():
        raise ValueError(
            f"this workspace carries a labeled included set at {labels_file}; run panner_compile_calibration on this draft "
            "before freezing"
        )


def freeze_compile(workspace_path: str, criteria_path: str, draft_path: str, plan_hash: str, user_confirmation: str) -> dict:
    """Freeze an immutable protocol version only when the exact preview was confirmed."""
    preview = preview_compile(workspace_path, criteria_path, draft_path)
    if preview["status"] != "ready_for_confirmation":
        raise ValueError("protocol is still a draft: " + "; ".join(preview["issues"]))
    if plan_hash != preview["plan_hash"]:
        raise ValueError("the protocol draft changed; show the new preview to the user before confirmation")
    if not user_confirmation.strip():
        raise ValueError("record the user's explicit confirmation of the full preview")
    _require_calibration(Path(preview["workspace_path"]), Path(draft_path).expanduser().resolve(strict=True))

    workspace, state = workspace_state(workspace_path)
    workspace.mkdir(parents=True, exist_ok=True)
    protocol_root = workspace / "protocol"
    protocol_root.mkdir(exist_ok=True)
    versions = sorted(
        int(path.name[1:]) for path in protocol_root.iterdir() if path.is_dir() and re.fullmatch(r"v[0-9]+", path.name)
    )
    version = f"v{(versions[-1] if versions else 0) + 1:04d}"
    target = protocol_root / version
    draft_file = Path(draft_path).expanduser().resolve(strict=True)
    draft = json.loads(draft_file.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    saved = {
        "format": PROTOCOL_FORMAT,
        "criteria_version": preview["criteria_version"],
        "criteria_sha256": preview["criteria_sha256"],
        "signals": draft["signals"],
        "decision_rules": draft["decision_rules"],
        "criteria_map": draft["criteria_map"],
        "relevance": draft["relevance"],
        "screening_review": draft["screening_review"],
    }
    staging = Path(tempfile.mkdtemp(prefix=".protocol-", dir=protocol_root))
    try:
        protocol_file = staging / PROTOCOL_NAME
        protocol_file.write_text(json.dumps(saved, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifest = {
            "version": version,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "protocol_sha256": sha256_file(protocol_file),
            "plan_hash": preview["plan_hash"],
            "previous_version": state.get("active_protocol"),
            "criteria_version": preview["criteria_version"],
            "criteria_sha256": preview["criteria_sha256"],
            "user_confirmation": user_confirmation.strip(),
            "counts": preview["counts"],
            "trial_evaluations": preview["trial_evaluations"],
            "trial_mismatches": preview["trial_mismatches"],
        }
        (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(staging, target)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise

    relative = f"protocol/{version}/{PROTOCOL_NAME}"
    state["active_protocol"] = relative
    write_json_atomic(workspace / MARKER, state)
    return {
        "status": "confirmed",
        "version": version,
        "protocol_path": str(workspace / relative),
        "manifest_path": str(target / "manifest.json"),
        "protocol_sha256": manifest["protocol_sha256"],
        "next_step": "04: answer the signals for each screened citation and evaluate the frozen rules",
    }


def protocol_status(workspace_path: str) -> dict:
    workspace, state = workspace_state(workspace_path)
    relative = state.get("active_protocol")
    if not relative:
        return {"status": "not_confirmed", "workspace_path": str(workspace)}
    protocol_file = workspace / relative
    manifest_file = protocol_file.parent / "manifest.json"
    if not protocol_file.is_file() or not manifest_file.is_file():
        raise ValueError("active protocol or its manifest is missing")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if sha256_file(protocol_file) != manifest.get("protocol_sha256"):
        raise ValueError("active protocol differs from its confirmed version")
    protocol = json.loads(protocol_file.read_text(encoding="utf-8"))
    criteria_relative = state.get("active_eligibility_criteria")
    if not criteria_relative or protocol.get("criteria_sha256") != sha256_file(workspace / criteria_relative):
        raise ValueError("active protocol does not derive from the active criteria")
    return {
        "status": "confirmed",
        "version": manifest["version"],
        "protocol_path": str(protocol_file),
        "protocol_sha256": manifest["protocol_sha256"],
        "criteria_version": manifest["criteria_version"],
        "counts": manifest["counts"],
    }
