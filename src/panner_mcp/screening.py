"""Atom-signal-based screening (step 04): backend judgment, two-state results.

Implements the frozen design in docs/04: run config with the calibrated and llm
channels, record-level concurrency with a default of 30, binding validation,
cost caps with partial persistence, and reproducible results
(screening results are recomputed from judgments by the shared evaluator).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from threading import Event, Lock
from urllib.parse import urlsplit

from .citations import _unique_object
from .evaluate import ANSWERS, evaluate_rules
from .protocol import EVAL_MODES
from .workspace import MARKER, sha256_file, workspace_state, write_json_atomic

RUN_CONFIG_NAME = "run-config.json"
BACKENDS = ("calibrated", "llm")
RUN_CONFIG_KEYS = {
    "backend", "provider", "model", "endpoint", "temperature", "concurrency", "cost_cap", "scope", "retry",
    "price_per_1m_input", "price_per_1m_output", "relevance", "batch_size", "confidence_floor", "judge_channel",
    "sample_size", "pilot",
}
# The decisions channel PANNER was validated against. A run config that answers through a different
# judge (for example a local LLM behind a decisions-shaped endpoint) must say so in `judge_channel`,
# because the results carry no calibrated probabilities and the manifest must not imply that they do.
DEFAULT_JUDGE_CHANNEL = "calibrated-decision-model"
DEFAULT_CONCURRENCY = 30
DEFAULT_RETRY = 3
DEFAULT_BATCH_SIZE = 250
MAX_GROUP_SLOTS = 6
GROUP_COUNT_ID = "PANNER_GROUP_COUNT"


class CallBudgetExhausted(Exception):
    """The current batch has no remaining API-call reservations."""


class CallBudget:
    def __init__(self, limit: int | None):
        self.limit = limit
        self.calls = 0
        self._lock = Lock()

    def reserve(self) -> bool:
        with self._lock:
            if self.limit is not None and self.calls >= self.limit:
                return False
            self.calls += 1
            return True


def _load_json(path_text: str, label: str) -> tuple[Path, dict]:
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{label} must be an absolute path")
    path = path.resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"{label} is not a file")
    return path, json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)


def _load_jsonl(path: Path) -> list[dict]:
    # JSONL records are terminated by "\n" only. str.splitlines() also breaks on
    # U+2028, U+2029, U+0085 and other Unicode line boundaries, which splits a
    # record in the middle of a JSON string as soon as a title or abstract
    # contains one of those characters.
    return [json.loads(line, object_pairs_hook=_unique_object) for line in path.read_text(encoding="utf-8").split("\n") if line.strip()]


def _write_jsonl_atomic(path: Path, rows: list[dict]) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(temporary, path)


def _load_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("rb+") as stream:
        content = stream.read()
        complete_length = content.rfind(b"\n") + 1
        if complete_length < len(content):
            stream.truncate(complete_length)
    return [
        json.loads(line, object_pairs_hook=_unique_object)
        for line in content[:complete_length].decode("utf-8").split("\n")
        if line.strip()
    ]


def _resolve_file(path_text: str, label: str) -> Path:
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{label} must be an absolute path")
    path = path.resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"{label} is not a file")
    return path


def _confirmed_artifact(path: Path, manifest_key: str, kind: str) -> dict:
    version_dir = path.parent
    if not re.fullmatch(r"v[0-9]+", version_dir.name):
        raise ValueError(f"{kind} must be a confirmed PANNER version directory")
    manifest_file = version_dir / "manifest.json"
    if not manifest_file.is_file():
        raise ValueError(f"confirmed {kind} manifest is missing")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if manifest.get(manifest_key) != sha256_file(path):
        raise ValueError(f"{kind} differs from its confirmed manifest")
    return manifest


def load_run_config(path_text: str, *, experimental: bool = False) -> tuple[Path, dict]:
    path, config = _load_json(path_text, "run_config_path")
    allowed_backends = BACKENDS if experimental else ("calibrated",)
    if not isinstance(config, dict) or config.get("backend") not in allowed_backends:
        raise ValueError("run config backend must be calibrated" if not experimental else "run config backend must be calibrated or llm")
    unknown = set(config) - RUN_CONFIG_KEYS
    if unknown:
        raise ValueError(f"run config has unknown fields: {sorted(unknown)}")
    for field in ("provider", "model"):
        if not isinstance(config.get(field), str) or not config[field].strip():
            raise ValueError(f"run config {field} must be a nonempty string")
    if config["backend"] == "llm" and config.get("temperature", 0) != 0:
        raise ValueError("the llm channel runs at temperature 0")
    concurrency = config.get("concurrency", DEFAULT_CONCURRENCY)
    if isinstance(concurrency, bool) or not isinstance(concurrency, int) or not 1 <= concurrency <= 64:
        raise ValueError("run config concurrency must be an integer from 1 to 64")
    config["concurrency"] = concurrency
    batch_size = config.get("batch_size", DEFAULT_BATCH_SIZE)
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= 1000:
        raise ValueError("run config batch_size must be an integer from 1 to 1000")
    config["batch_size"] = batch_size
    config.setdefault("retry", DEFAULT_RETRY)
    if isinstance(config["retry"], bool) or not isinstance(config["retry"], int) or not 0 <= config["retry"] <= 5:
        raise ValueError("run config retry must be an integer from 0 to 5")
    cost_cap = config.get("cost_cap")
    if cost_cap is not None and (isinstance(cost_cap, bool) or not isinstance(cost_cap, int) or cost_cap < 1):
        raise ValueError("run config cost_cap must be a positive integer or omitted")
    for field in ("price_per_1m_input", "price_per_1m_output"):
        price = config.get(field)
        if price is not None and (isinstance(price, bool) or not isinstance(price, (int, float))
                                  or not math.isfinite(price) or price < 0):
            raise ValueError(f"run config {field} must be a finite nonnegative number")
    scope = config.get("scope")
    pilot = config.get("pilot")
    if pilot is not None and (not isinstance(pilot, bool)):
        raise ValueError("run config pilot must be a boolean or omitted")
    if scope is not None and not pilot and not experimental:
        raise ValueError(
            "production screening covers the complete active citation pool; set \"pilot\": true in the run config "
            "to screen an explicit sample scope first"
        )
    if scope is not None and (not isinstance(scope, list) or any(not isinstance(item, str) for item in scope)):
        raise ValueError("run config scope must be a list of citation ids or omitted")
    if scope is not None and len(set(scope)) != len(scope):
        raise ValueError("run config scope contains duplicate citation ids")
    sample_size = config.get("sample_size")
    if sample_size is not None and (isinstance(sample_size, bool) or not isinstance(sample_size, int)
                                    or not 1 <= sample_size <= 5000):
        raise ValueError("run config sample_size must be an integer from 1 to 5000 or omitted")
    floor = config.get("confidence_floor")
    if floor is not None and (
        isinstance(floor, bool) or not isinstance(floor, (int, float))
        or not 0 <= float(floor) <= 1
    ):
        raise ValueError("run config confidence_floor must be a number from 0 to 1 or omitted")
    channel = config.get("judge_channel", DEFAULT_JUDGE_CHANNEL)
    if not isinstance(channel, str) or not channel.strip():
        raise ValueError("run config judge_channel must be a nonempty string or omitted")
    config["judge_channel"] = channel.strip()
    endpoint = config.get("endpoint")
    if config["backend"] == "calibrated":
        if not isinstance(endpoint, str):
            raise ValueError("calibrated run config endpoint must be a URL")
        destination = urlsplit(endpoint)
        host = (destination.hostname or "").lower()
        local_host = host in {"localhost", "127.0.0.1", "::1"}
        allowed_hosts = {"openrouter.ai"} | {
            item.strip().lower() for item in os.environ.get("PANNER_ALLOWED_DECISION_HOSTS", "").split(",") if item.strip()
        }
        if (not host or destination.username or destination.password or destination.fragment
                or (destination.scheme != "https" and not (local_host and destination.scheme == "http"))):
            raise ValueError("calibrated endpoint must be HTTPS (or an explicit local HTTP endpoint) without credentials")
        if host not in allowed_hosts:
            raise ValueError("decision endpoint host is not allowed; configure PANNER_ALLOWED_DECISION_HOSTS explicitly")
        if host == "openrouter.ai" and config["provider"].strip().casefold() != "openrouter":
            raise ValueError("provider must be openrouter for an openrouter.ai decision endpoint")
    return path, config


def _field_rule_answer(signal: dict, citation: dict) -> str:
    rule = signal["field_rule"]
    values = citation.get(rule["field"]) or []
    if isinstance(values, str):
        values = [values]
    for match in rule["matches"]:
        if any(str(value).strip().casefold() == str(match["value"]).strip().casefold() for value in values):
            return match["answer"]
    return "unclear"


def _fired_leaves(condition: dict, lookup) -> list[dict]:
    if "signal" in condition:
        if lookup(condition["signal"], condition["on"]) is True:
            return [condition]
        return []
    if "not" in condition:
        inner = condition["not"]
        if "signal" in inner:
            if lookup(inner["signal"], inner["on"]) is False:
                return [{"signal": inner["signal"], "on": inner["on"], "expect": inner["expect"]}]
            return []
        return _fired_leaves(inner, lookup)
    fired: list[dict] = []
    for child in condition.get("all", condition.get("any", [])):
        fired.extend(_fired_leaves(child, lookup))
    return fired


def _calibrated_call(config: dict, signals: list[dict], citation: dict, api_key: str) -> tuple[dict, dict]:
    """One decisions-API call answers the supplied typed questions for one citation."""
    import httpx

    body = {
        "model": config["model"],
        "state": {"title": citation["title"], "abstract": citation["abstract"]},
        "questions": {
            signal["id"]: {"type": "choice", "instructions": signal["question"], "criteria": signal["answer_criteria"]}
            for signal in signals
        },
    }
    response = httpx.post(
        config["endpoint"],
        headers={"Authorization": f"Bearer {api_key}"},
        json=body,
        timeout=120,
    )
    response.raise_for_status()
    payload = response.json()
    usage = dict(payload.get("usage", {}))
    usage.update({"response_model": payload.get("model"), "request_id": payload.get("id"),
                  "response_provider": payload.get("provider")})
    return payload.get("answers", {}), usage


def _bound_answer(signal_id: str, on: str, item: object, floor: float | None, flags: list[str]) -> dict:
    """Bind one model answer to a signal and keep the confidence the decision model reported."""
    where = signal_id if on == "record" else f"{signal_id} on {on}"
    raw_confidence = item.get("confidence") if isinstance(item, dict) else None
    confidence = (
        float(raw_confidence)
        if isinstance(raw_confidence, (int, float)) and not isinstance(raw_confidence, bool)
        else None
    )
    choice = item.get("choice") if isinstance(item, dict) else None
    if choice not in ANSWERS:
        flags.append(f"{where}: no readable choice from the decision model")
        choice = "unclear"
    elif choice != "unclear" and floor is not None and confidence is not None and confidence < floor:
        # A low-confidence answer is not evidence; it stays undetermined rather than excluding.
        flags.append(f"{where}: {choice} below the confidence floor {floor} (confidence {confidence})")
        choice = "unclear"
    answer = {"signal": signal_id, "on": on, "answer": choice}
    if confidence is not None:
        answer["confidence"] = confidence
    return answer


def _judge_citation_calibrated(config: dict, signals: list[dict], citation: dict, api_key: str, reserve_call=None) -> dict:
    record_signals = [signal for signal in signals if signal["unit"] not in ("arm", "regimen")]
    group_signals = [signal for signal in signals if signal["unit"] in ("arm", "regimen")]
    questions = list(record_signals)
    if group_signals:
        count_criteria = {
            str(number): f"The citation explicitly establishes that exactly {number} distinct study groups form the complete reported set."
            for number in range(MAX_GROUP_SLOTS + 1)
        }
        count_criteria["more"] = f"The citation explicitly establishes more than {MAX_GROUP_SLOTS} distinct study groups."
        count_criteria["unclear"] = "The complete number of distinct study groups cannot be established from the citation."
        questions.append({
            "id": GROUP_COUNT_ID,
            "question": "How many distinct study groups does the title and abstract explicitly establish as the complete set? Count groups in order of first mention. Choose unclear if the set may be incomplete.",
            "answer_criteria": count_criteria,
        })
        for number in range(1, MAX_GROUP_SLOTS + 1):
            for signal in group_signals:
                questions.append({
                    "id": f"{signal['id']}__group_{number}",
                    "question": (
                        f"For study group {number} in order of first mention in the title and abstract, "
                        f"answer only for that group: {signal['question']} "
                        "If that group's identity or the requested fact is unclear, choose unclear."
                    ),
                    "answer_criteria": signal["answer_criteria"],
                })
    raw, answers, usage, flags = None, [], {"cost_usd": 0.0}, []
    last_error: Exception | None = None
    for attempt in range(int(config.get("retry", DEFAULT_RETRY)) + 1):
        if reserve_call is not None and not reserve_call():
            raise CallBudgetExhausted("API call limit reached")
        try:
            raw, usage = _calibrated_call(config, questions, citation, api_key)
            break
        except Exception as exc:  # noqa: BLE001 - retried per run config
            last_error = exc
            if attempt < int(config.get("retry", DEFAULT_RETRY)):
                time.sleep(2 ** attempt)
    if raw is None:
        raise RuntimeError(f"calibrated call failed after retries: {last_error}")
    usage = {"cost_usd": float(usage.get("cost", 0.0)),
             "input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0),
             "response_model": usage.get("response_model") or config["model"],
             "request_id": usage.get("request_id"), "response_provider": usage.get("response_provider")}
    floor = config.get("confidence_floor")
    floor = float(floor) if isinstance(floor, (int, float)) and not isinstance(floor, bool) else None
    for signal in record_signals:
        answers.append(_bound_answer(signal["id"], "record", raw.get(signal["id"]), floor, flags))
    arms = []
    count_choice = None
    if group_signals:
        count_item = raw.get(GROUP_COUNT_ID)
        count_choice = count_item.get("choice") if isinstance(count_item, dict) else None
        if isinstance(count_choice, str) and count_choice.isdigit() and 0 <= int(count_choice) <= MAX_GROUP_SLOTS:
            arms = [f"group_{number}" for number in range(1, int(count_choice) + 1)]
        else:
            flags.append("the complete study-group count is unclear; group rules remain undetermined")
        for number, arm in enumerate(arms, 1):
            for signal in group_signals:
                answers.append(_bound_answer(signal["id"], arm, raw.get(f"{signal['id']}__group_{number}"), floor, flags))
    return {
        "entities": {"arms": arms, "count_answer": count_choice, "order": "first_mention"},
        "answers": answers, "flags": flags, "usage": usage,
    }


SYSTEM_PROMPT = """You are answering screening questions about one citation for a systematic review.
Rules:
- Answer each question with exactly one of: hit, partial, miss, unclear.
- hit: the citation explicitly states the fact asked about. partial: the citation explicitly shows both qualifying and non-qualifying parts within the question's dimension. miss: the citation explicitly states a fact that contradicts the question's premise; mere absence of information is always unclear, never miss. unclear: not stated, insufficient, or conflicting.
- First list every study group the title or abstract names (e.g. treatment arms); groups mentioned without a stated regimen still belong in the list.
- Bind each answer with "on": "record" for study-, population-, or citation-level questions; the group name for group or regimen questions. Objects the citation does not fully describe stay unclear; never invent objects.
- Output strict JSON only, no prose: {"entities": {"arms": ["<group names>"]}, "answers": [{"signal": "<id>", "on": "<record or group name>", "answer": "<hit|partial|miss|unclear>"}]}"""


def _chat_call(config: dict, system: str, user: str, api_key: str) -> tuple[dict, dict]:
    import httpx

    endpoint = os.environ.get("PANNER_LLM_ENDPOINT", "https://openrouter.ai/api/v1/chat/completions")
    response = httpx.post(
        endpoint,
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": config["model"],
            "temperature": config.get("temperature", 0),
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
        },
        timeout=120,
    )
    response.raise_for_status()
    payload = response.json()
    usage = payload.get("usage", {})
    content = payload["choices"][0]["message"]["content"]
    return json.loads(content), usage


def _judge_citation(config: dict, signals: list[dict], citation: dict, api_key: str, reserve_call=None) -> dict:
    user = json.dumps(
        {
            "signals": [
                {"id": signal["id"], "question": signal["question"], "answer_criteria": signal["answer_criteria"]}
                for signal in signals
            ],
            "citation": {"title": citation["title"], "abstract": citation["abstract"]},
        },
        ensure_ascii=False,
    )
    parsed, usage = None, {}
    last_error: Exception | None = None
    for attempt in range(int(config.get("retry", DEFAULT_RETRY)) + 1):
        if reserve_call is not None and not reserve_call():
            raise CallBudgetExhausted("API call limit reached")
        try:
            parsed, usage = _chat_call(config, SYSTEM_PROMPT, user, api_key)
            break
        except Exception as exc:  # noqa: BLE001 - retried per run config
            last_error = exc
            if attempt < int(config.get("retry", DEFAULT_RETRY)):
                time.sleep(2 ** attempt)
    if parsed is None:
        raise RuntimeError(f"backend call failed after retries: {last_error}")

    entities = parsed.get("entities", {})
    arms = [arm for arm in entities.get("arms", []) if isinstance(arm, str) and arm.strip()] if isinstance(entities, dict) else []
    allowed = {"record", *arms}
    signals_by_id = {signal["id"]: signal for signal in signals}
    answers, flags = [], []
    for item in parsed.get("answers", []):
        if not isinstance(item, dict) or item.get("signal") not in signals_by_id:
            flags.append(f"dropped an answer for unknown signal: {item!r}"[:120])
            continue
        answer = item.get("answer")
        on = item.get("on", "record")
        if answer not in ANSWERS:
            flags.append(f"{item.get('signal')}: answer outside the four values")
            answer = "unclear"
        if on not in allowed:
            flags.append(f"{item.get('signal')}: unknown binding {on!r}; treated as unclear")
            answer = "unclear"
        answers.append({"signal": item["signal"], "on": on, "answer": answer})
    return {"entities": {"arms": arms}, "answers": answers, "flags": flags, "usage": usage}


def _confirmed_pool(pool_file: Path) -> dict:
    """The pool must match the dedup manifest beside it (docs/02: outputs live in one directory)."""
    manifest_file = pool_file.parent / "dedup-manifest.json"
    if pool_file.name != "citation-pool.jsonl" or not manifest_file.is_file():
        raise ValueError("pool_path must be a PANNER citation-pool.jsonl with its dedup-manifest.json")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if manifest.get("outputs", {}).get("citation-pool.jsonl") != sha256_file(pool_file):
        raise ValueError("citation-pool.jsonl differs from its dedup manifest")
    return manifest


def _plan(workspace_path: str, protocol_path: str, pool_path: str, run_config_path: str, *, experimental: bool = False) -> dict:
    workspace, state = workspace_state(workspace_path)
    protocol_file, protocol = _load_json(protocol_path, "protocol_path")
    protocol_manifest = _confirmed_artifact(protocol_file, "protocol_sha256", "protocol")
    if not state.get("active_protocol") or protocol_file != workspace / state["active_protocol"]:
        raise ValueError("protocol_path must be the active protocol of this workspace")
    criteria_relative = state.get("active_eligibility_criteria")
    if not criteria_relative or protocol.get("criteria_sha256") != sha256_file(workspace / criteria_relative):
        raise ValueError("active eligibility criteria differ from the frozen protocol")
    pool_file = _resolve_file(pool_path, "pool_path")
    if not state.get("active_pool") or pool_file != Path(state["active_pool"]).resolve():
        raise ValueError("pool_path must be the active citation pool of this workspace")
    pool_manifest = _confirmed_pool(pool_file)
    citation_relative = state.get("active_citation")
    if not citation_relative or Path(pool_manifest.get("input_path", "")).resolve() != workspace / citation_relative:
        raise ValueError("pool_path must derive from the active citations of this workspace")
    if pool_manifest.get("input_sha256") != sha256_file(workspace / citation_relative):
        raise ValueError("active citations differ from the pool input")
    config_path, config = load_run_config(run_config_path, experimental=experimental)
    screened = _load_jsonl(pool_file)
    screened = [row for row in screened if row["duplicate_of"] is None]
    if config.get("scope") is not None:
        wanted = set(config["scope"])
        unknown = wanted - {row["id"] for row in screened}
        if unknown:
            raise ValueError(f"run config scope has unknown citation ids: {sorted(unknown)[:10]}")
        screened = [row for row in screened if row["id"] in wanted]
    semantic = [signal for signal in protocol["signals"] if signal["evaluation_mode"] == EVAL_MODES[1]]
    field_rule = [signal for signal in protocol["signals"] if signal["evaluation_mode"] == EVAL_MODES[0]]
    calls = len(screened) if semantic else 0
    record_semantic = [signal for signal in semantic if signal["unit"] not in ("arm", "regimen")]
    group_semantic = [signal for signal in semantic if signal["unit"] in ("arm", "regimen")]
    question_chars = sum(len(signal["question"]) + sum(map(len, signal["answer_criteria"].values())) for signal in record_semantic)
    question_chars += MAX_GROUP_SLOTS * sum(
        len(signal["question"]) + sum(map(len, signal["answer_criteria"].values())) + 150
        for signal in group_semantic
    )
    question_count = len(record_semantic) + MAX_GROUP_SLOTS * len(group_semantic) + bool(group_semantic)
    if group_semantic:
        question_chars += 650
    estimated_input_tokens = sum(
        200 + math.ceil((len(row["title"]) + len(row["abstract"]) + question_chars) / 4)
        for row in screened
    ) if semantic else 0
    estimated_output_tokens = calls * (20 + 12 * question_count)
    input_price = config.get("price_per_1m_input", 0.3)
    output_price = config.get("price_per_1m_output", 2.5)
    estimated_cost = (
        estimated_input_tokens / 1e6 * input_price
        + estimated_output_tokens / 1e6 * output_price
    )
    payload = {
        "workspace": str(workspace),
        "criteria_path": str(workspace / criteria_relative),
        "criteria_sha256": protocol["criteria_sha256"],
        "citation_path": str(workspace / citation_relative),
        "citation_sha256": pool_manifest["input_sha256"],
        "protocol_path": str(protocol_file),
        "protocol_sha256": sha256_file(protocol_file),
        "protocol_manifest_sha256": sha256_file(protocol_file.parent / "manifest.json"),
        "pool_path": str(pool_file),
        "pool_sha256": sha256_file(pool_file),
        "pool_manifest_sha256": sha256_file(pool_file.parent / "dedup-manifest.json"),
        "run_config_sha256": sha256_file(config_path),
        "screened": len(screened),
    }
    return {
        "status": "planned",
        "plan_hash": hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest(),
        "workspace_path": str(workspace),
        "protocol_version": protocol_manifest.get("version"),
        "protocol_sha256": payload["protocol_sha256"],
        "protocol_manifest_sha256": payload["protocol_manifest_sha256"],
        "pool_sha256": payload["pool_sha256"],
        "pool_manifest_sha256": payload["pool_manifest_sha256"],
        "criteria_path": payload["criteria_path"],
        "citation_path": payload["citation_path"],
        "run_config_sha256": payload["run_config_sha256"],
        "pool_path": str(pool_file),
        "counts": {"to_screen": len(screened), "semantic_signals": len(semantic), "field_rule_signals": len(field_rule)},
        "backend": config["backend"],
        "model": config["model"],
        "judge_channel": config.get("judge_channel", DEFAULT_JUDGE_CHANNEL),
        "endpoint": config.get("endpoint", ""),
        "concurrency": config["concurrency"],
        "cost_cap": config.get("cost_cap"),
        "batch_size": config["batch_size"],
        "estimated_calls": calls,
        "estimated_cost_usd": round(estimated_cost, 4),
        "estimate_basis": (
            f"upper estimate before deterministic exclusions/retries: about {estimated_input_tokens} input and "
            f"{estimated_output_tokens} output tokens across {question_count} typed questions per citation; "
            f"rates are ${input_price}/M input and ${output_price}/M output "
            "(configured values or planning defaults, not a live provider quote)"
        ),
        "files_to_write": ["screening/.lock.sqlite3", "screening/vN/events.jsonl", "screening/vN/judgments.jsonl",
                           "screening/vN/screening-results.jsonl", "screening/vN/run-config.json",
                           "screening/vN/manifest.json", MARKER],
        "_screened": screened,
        "_config": config,
        "_config_path": str(config_path),
        "_protocol": protocol,
    }


def _execute(plan: dict, api_key: str, user_confirmation: str, progress=None, cancel_event: Event | None = None,
             retry_failed: bool = False) -> dict:
    """Process one bounded batch; an append-only event journal makes the run resumable."""
    config = plan["_config"]
    protocol = plan["_protocol"]
    semantic = [signal for signal in protocol["signals"] if signal["evaluation_mode"] == "semantic"]
    field_rules = [signal for signal in protocol["signals"] if signal["evaluation_mode"] == "field_rule"]
    core_signal_ids = {signal["id"] for signal in protocol["signals"] if signal["core"]}
    rules = protocol["decision_rules"]
    root = Path(plan["workspace_path"]) / "screening"
    root.mkdir(exist_ok=True)
    with sqlite3.connect(root / ".lock.sqlite3", timeout=0) as lock_file:
        try:
            lock_file.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            raise ValueError("another screening batch is already running in this workspace") from exc

        existing = [
            folder for folder in root.iterdir()
            if folder.is_dir() and re.fullmatch(r"v[0-9]+", folder.name)
            and (folder / "manifest.json").is_file()
            and json.loads((folder / "manifest.json").read_text(encoding="utf-8")).get("plan_hash") == plan["plan_hash"]
        ]
        if existing:
            target = max(existing, key=lambda folder: int(folder.name[1:]))
            manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        else:
            versions = [int(folder.name[1:]) for folder in root.iterdir() if folder.is_dir() and re.fullmatch(r"v[0-9]+", folder.name)]
            target = root / f"v{(max(versions, default=0) + 1):04d}"
            config_data = Path(plan["_config_path"]).read_bytes()
            if hashlib.sha256(config_data).hexdigest() != plan["run_config_sha256"]:
                raise ValueError("run configuration changed after preview; preview again")
            target.mkdir()
            config_snapshot = target / "run-config.json"
            config_snapshot.write_bytes(config_data)
            manifest = {
                "plan_hash": plan["plan_hash"],
                "created_at": datetime.now(timezone.utc).isoformat(),
                "protocol_path": str(Path(plan["workspace_path"]) / "protocol" / plan["protocol_version"] / "protocol.json"),
                "protocol_version": plan["protocol_version"],
                "protocol_sha256": plan["protocol_sha256"],
                "protocol_manifest_sha256": plan["protocol_manifest_sha256"],
                "criteria_path": plan["criteria_path"],
                "pool_path": plan["pool_path"],
                "pool_sha256": plan["pool_sha256"],
                "pool_manifest_sha256": plan["pool_manifest_sha256"],
                "citation_path": plan["citation_path"],
                "run_config_path": plan["_config_path"],
                "run_config_sha256": plan["run_config_sha256"],
                "run_config_snapshot_sha256": sha256_file(config_snapshot),
                "run_config": config,
                "backend": config["backend"],
                "model": config["model"],
                "judge_channel": config.get("judge_channel", DEFAULT_JUDGE_CHANNEL),
                "user_confirmation": user_confirmation.strip(),
                "panner_version": version("panner-mcp"),
                "python": sys.version.split()[0],
                "calls": 0,
            }
            (target / "events.jsonl").touch()

        event_file = target / "events.jsonl"
        if retry_failed:
            """Requeue infrastructure failures: drop the failed events (they carry no judgment) and
            re-hash the journal so the run continues with those citations pending again. A failed
            event records an error, never a screening decision, so pruning it loses nothing."""
            events = _load_events(event_file)
            kept = [event for event in events if event["result"]["status"] != "failed"]
            if len(kept) != len(events):
                event_file.write_text(
                    "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in kept), encoding="utf-8"
                )
                manifest["events_sha256"] = sha256_file(event_file)
                write_json_atomic(target / "manifest.json", manifest)
        if not manifest.get("running", False) and manifest.get("events_sha256") is not None:
            if manifest["events_sha256"] != sha256_file(event_file):
                raise ValueError("screening event journal differs from its confirmed manifest")
        events_by_id = {}
        for event in _load_events(event_file):
            identifier = event["result"]["citation_id"]
            if identifier in events_by_id or event["judgment"]["citation_id"] != identifier:
                raise ValueError("screening event journal has duplicate or mismatched citation ids")
            result = event["result"]
            if result["status"] in ("excluded", "retained"):
                recalculated = evaluate_rules(rules, event["judgment"])
                expected = "excluded" if recalculated["status"] == "excluded_candidate" else "retained"
                if (result["status"] != expected or result.get("basis") != recalculated["basis"]
                        or result.get("undetermined") != recalculated["undetermined"]):
                    raise ValueError(f"screening event is not reproducible for {identifier}")
            events_by_id[identifier] = event
        all_ids = {row["id"] for row in plan["_screened"]}
        if set(events_by_id) - all_ids:
            raise ValueError("screening event journal contains an unexpected citation id")

        def refresh() -> dict:
            results = [
                events_by_id[row["id"]]["result"] if row["id"] in events_by_id
                else {"citation_id": row["id"], "status": "pending", "error": "awaiting the next screening batch"}
                for row in plan["_screened"]
            ]
            judgments = [events_by_id[row["id"]]["judgment"] for row in plan["_screened"] if row["id"] in events_by_id]
            _write_jsonl_atomic(target / "screening-results.jsonl", results)
            _write_jsonl_atomic(target / "judgments.jsonl", judgments)
            counts = {
                "excluded": sum(row["status"] == "excluded" for row in results),
                "retained": sum(row["status"] == "retained" for row in results),
                "failed": sum(row["status"] == "failed" for row in results),
                "pending": sum(row["status"] == "pending" for row in results),
                "to_screen": len(results),
            }
            manifest["counts"] = counts
            manifest["status"] = "partial" if counts["pending"] or counts["failed"] else "confirmed"
            manifest["stopped_early"] = bool(counts["pending"])
            manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
            manifest["cost_usd"] = round(sum(row.get("usage", {}).get("cost_usd", 0.0) for row in results), 4)
            manifest["results_sha256"] = sha256_file(target / "screening-results.jsonl")
            manifest["judgments_sha256"] = sha256_file(target / "judgments.jsonl")
            manifest["events_sha256"] = sha256_file(event_file)
            manifest["running"] = False
            write_json_atomic(target / "manifest.json", manifest)
            return counts

        counts = refresh()
        workspace, state = workspace_state(plan["workspace_path"])
        state["active_screening"] = f"screening/{target.name}/screening-results.jsonl"
        write_json_atomic(workspace / MARKER, state)

        pending = [row for row in plan["_screened"] if row["id"] not in events_by_id]
        selected = pending[:config["batch_size"]]
        budget = CallBudget(config.get("cost_cap"))
        if selected:
            manifest["running"] = True
            write_json_atomic(target / "manifest.json", manifest)

        def reserve_call() -> bool:
            if cancel_event is not None and cancel_event.is_set():
                return False
            return budget.reserve()

        def worker(citation: dict) -> dict | None:
            if cancel_event is not None and cancel_event.is_set():
                return None
            judgment = {"entities": {"arms": []}, "answers": [], "flags": [], "usage": {"input": 0, "output": 0, "cost_usd": 0.0}}
            for signal in field_rules:
                judgment["answers"].append({"signal": signal["id"], "on": "record", "answer": _field_rule_answer(signal, citation)})
            deterministic = evaluate_rules(rules, judgment)
            error = None
            if deterministic["status"] != "excluded_candidate" and semantic:
                try:
                    if config["backend"] == "calibrated":
                        judged = _judge_citation_calibrated(config, semantic, citation, api_key, reserve_call)
                    else:
                        judged = _judge_citation(config, semantic, citation, api_key, reserve_call)
                    judgment["entities"] = judged["entities"]
                    existing_answers = {(answer["signal"], answer.get("on", "record")): answer for answer in judgment["answers"]}
                    for answer in judged["answers"]:
                        existing_answers[(answer["signal"], answer.get("on", "record"))] = answer
                    judgment["answers"] = list(existing_answers.values())
                    judgment["flags"].extend(judged["flags"])
                    usage = judged["usage"]
                    judgment["usage"]["input"] = usage.get("input_tokens", usage.get("prompt_tokens", 0))
                    judgment["usage"]["output"] = usage.get("output_tokens", usage.get("completion_tokens", 0))
                    judgment["usage"]["cost_usd"] = usage.get("cost_usd", 0.0)
                    if config["backend"] == "llm" and not judgment["usage"]["cost_usd"]:
                        judgment["usage"]["cost_usd"] = (
                            judgment["usage"]["input"] / 1e6 * config.get("price_per_1m_input", 0.3)
                            + judgment["usage"]["output"] / 1e6 * config.get("price_per_1m_output", 2.5)
                        )
                    for key in ("response_model", "request_id", "response_provider"):
                        if usage.get(key) is not None:
                            judgment["usage"][key] = usage[key]
                except CallBudgetExhausted:
                    return None
                except Exception as exc:  # noqa: BLE001 - per-citation failure is preserved
                    error = str(exc)[:300]
            if error is None:
                evaluation = evaluate_rules(rules, judgment)
                result = {
                    "citation_id": citation["id"],
                    "status": "excluded" if evaluation["status"] == "excluded_candidate" else "retained",
                    "basis": evaluation["basis"],
                    "undetermined": evaluation["undetermined"],
                    "flags": judgment["flags"],
                    "usage": judgment["usage"],
                }
                if result["status"] == "retained" and protocol["relevance"]["enabled"] and core_signal_ids:
                    known_core = any(
                        answer["signal"] in core_signal_ids and answer["answer"] != "unclear"
                        for answer in judgment["answers"]
                    )
                    result["review_priority"] = "topic_unclear" if not known_core else "standard"
            else:
                result = {"citation_id": citation["id"], "status": "failed", "error": error, "flags": judgment["flags"]}
            return {"result": result, "judgment": {**judgment, "citation_id": citation["id"]}}

        try:
            with event_file.open("a", encoding="utf-8") as stream, ThreadPoolExecutor(max_workers=config["concurrency"]) as executor:
                futures = [executor.submit(worker, citation) for citation in selected]
                for done, future in enumerate(as_completed(futures), 1):
                    event = future.result()
                    if event is not None:
                        identifier = event["result"]["citation_id"]
                        stream.write(json.dumps(event, ensure_ascii=False) + "\n")
                        stream.flush()
                        events_by_id[identifier] = event
                    if progress:
                        progress(done, len(selected))
        finally:
            manifest["calls"] += budget.calls
            counts = refresh()

        return {
            "status": manifest["status"],
            "plan_hash": plan["plan_hash"],
            "version": target.name,
            "results_path": str(target / "screening-results.jsonl"),
            "judgments_path": str(target / "judgments.jsonl"),
            "manifest_path": str(target / "manifest.json"),
            "counts": counts,
            "stopped_early": manifest["stopped_early"],
        }


def preview_screen(workspace_path: str, protocol_path: str, pool_path: str, run_config_path: str, *, experimental: bool = False) -> dict:
    public = _plan(workspace_path, protocol_path, pool_path, run_config_path, experimental=experimental)
    return {key: value for key, value in public.items() if not key.startswith("_")}


def confirm_screen(workspace_path: str, protocol_path: str, pool_path: str, run_config_path: str, plan_hash: str, user_confirmation: str, *, experimental: bool = False, progress=None, cancel_event: Event | None = None, retry_failed: bool = False) -> dict:
    plan = _plan(workspace_path, protocol_path, pool_path, run_config_path, experimental=experimental)
    if plan_hash != plan["plan_hash"]:
        raise ValueError("screening plan changed; preview again before confirmation")
    if not user_confirmation.strip():
        raise ValueError("record the user's explicit confirmation of the plan")
    api_key = (
        os.environ.get("PANNER_DECISION_API_KEY", "") or os.environ.get("PANNER_LLM_API_KEY", "")
        if plan["_config"]["backend"] == "calibrated"
        else os.environ.get("PANNER_LLM_API_KEY", "")
    )
    if plan["counts"]["semantic_signals"] and not api_key:
        raise ValueError("set PANNER_DECISION_API_KEY in the environment; keys never live in the workspace")
    return _execute(plan, api_key, user_confirmation, progress, cancel_event, retry_failed)


def continue_screen(workspace_path: str, plan_hash: str, *, experimental: bool = False, progress=None, cancel_event: Event | None = None, retry_failed: bool = False) -> dict:
    workspace, state = workspace_state(workspace_path)
    relative = state.get("active_screening")
    if not relative:
        raise ValueError("this workspace has no screening run to continue")
    results_file = (workspace / relative).resolve(strict=True)
    if not results_file.is_relative_to(workspace):
        raise ValueError("active screening path leaves the workspace")
    manifest_file = results_file.parent / "manifest.json"
    if not manifest_file.is_file():
        raise ValueError("active screening manifest is missing")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if manifest.get("plan_hash") != plan_hash:
        raise ValueError("the active run has a different plan hash")
    return confirm_screen(
        workspace_path,
        manifest["protocol_path"],
        manifest["pool_path"],
        str(manifest_file.parent / "run-config.json"),
        plan_hash,
        manifest["user_confirmation"],
        experimental=experimental,
        progress=progress,
        cancel_event=cancel_event,
        retry_failed=retry_failed,
    )


def screen_status(workspace_path: str) -> dict:
    workspace, state = workspace_state(workspace_path)
    relative = state.get("active_screening")
    if not relative:
        return {"status": "not_available", "workspace_path": str(workspace)}
    results_file = (workspace / relative).resolve(strict=True)
    if not results_file.is_relative_to(workspace):
        raise ValueError("active screening path leaves the workspace")
    manifest_file = results_file.parent / "manifest.json"
    if not results_file.is_file() or not manifest_file.is_file():
        raise ValueError("active screening results or manifest is missing")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    judgments_file = results_file.parent / "judgments.jsonl"
    if (manifest.get("results_sha256") != sha256_file(results_file)
            or manifest.get("judgments_sha256") != sha256_file(judgments_file)):
        raise ValueError("active screening outputs differ from their manifest")
    if manifest.get("run_config_snapshot_sha256") != sha256_file(results_file.parent / "run-config.json"):
        raise ValueError("screening run configuration differs from its snapshot")
    if not manifest.get("running", False) and manifest.get("events_sha256") != sha256_file(results_file.parent / "events.jsonl"):
        raise ValueError("screening event journal differs from its manifest")
    pool_file = Path(manifest["pool_path"])
    protocol_file = workspace / "protocol" / manifest["protocol_version"] / "protocol.json"
    if manifest.get("pool_sha256") != sha256_file(pool_file) or manifest.get("protocol_sha256") != sha256_file(protocol_file):
        raise ValueError("screening inputs differ from their recorded hashes")
    if (manifest.get("pool_manifest_sha256") != sha256_file(pool_file.parent / "dedup-manifest.json")
            or manifest.get("protocol_manifest_sha256") != sha256_file(protocol_file.parent / "manifest.json")):
        raise ValueError("screening input manifests differ from their recorded hashes")
    judgments = _load_jsonl(judgments_file)
    pool = {row["id"]: row for row in _load_jsonl(pool_file)}
    protocol = json.loads(protocol_file.read_text(encoding="utf-8"))
    results_by_id = {}
    duplicate_result_ids = set()
    for line in results_file.read_text(encoding="utf-8").split("\n"):
        if line.strip():
            item = json.loads(line)
            if item["citation_id"] in results_by_id:
                duplicate_result_ids.add(item["citation_id"])
            results_by_id[item["citation_id"]] = item
    mismatches = sorted(duplicate_result_ids)
    judgment_ids = {judgment["citation_id"] for judgment in judgments}
    for identifier, stored in results_by_id.items():
        if stored["status"] in ("excluded", "retained") and identifier not in judgment_ids:
            mismatches.append(identifier)
    for judgment in judgments:
        stored = results_by_id.get(judgment["citation_id"])
        if not stored or stored["status"] in ("failed", "pending"):
            continue
        full = {**judgment, "_citation": pool[judgment["citation_id"]], "_signals": [s for s in protocol["signals"] if s["evaluation_mode"] == "semantic"]}
        evaluated = evaluate_rules(protocol["decision_rules"], full)
        expected = "excluded" if evaluated["status"] == "excluded_candidate" else "retained"
        if (stored["status"] != expected or stored.get("basis") != evaluated["basis"]
                or stored.get("undetermined") != evaluated["undetermined"]):
            mismatches.append(judgment["citation_id"])
    current_protocol = state.get("active_protocol")
    current_pool = state.get("active_pool")
    is_current = bool(
        current_protocol and current_pool
        and (workspace / current_protocol).resolve() == protocol_file.resolve()
        and Path(current_pool).resolve() == pool_file.resolve()
    )
    return {
        "status": manifest.get("status", "partial") if is_current else "stale",
        "plan_hash": manifest.get("plan_hash"),
        "version": results_file.parent.name,
        "results_path": str(results_file),
        "counts": manifest["counts"],
        "cost_usd": manifest.get("cost_usd"),
        "recomputation_mismatches": sorted(set(mismatches)),
    }
