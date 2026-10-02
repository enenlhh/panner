"""Host-readable knowledge resources: contracts, orchestration, and examples.

Docs are written for human review; the host agent cannot read them at runtime.
Everything in docs that shapes host behaviour is mirrored here so the host can
list and read it from the MCP connection. The human-facing Chinese docs remain
the review copies; these resources are the English runtime knowledge.
"""

from __future__ import annotations

import json

from .citations import FIELDS, SOURCE_FIELDS
from .protocol import EVAL_MODES, HANDOFF_STAGES, ROLES, RULE_KEYS, SIGNAL_KEYS, SIGNAL_UNITS, TARGETS, TOP_LEVEL_KEYS

WORKFLOW_URI = "panner://workflow"
CRITERIA_CONTRACT_URI = "panner://contracts/criteria"
CITATIONS_CONTRACT_URI = "panner://contracts/citations"
PROTOCOL_CONTRACT_URI = "panner://contracts/protocol"
PROTOCOL_EXAMPLE_URI = "panner://examples/protocol-draft"
CITATION_EXAMPLE_URI = "panner://examples/citation"


def _identifiers(values) -> str:
    return ", ".join(f"`{value}`" for value in values)


def workflow_resource() -> str:
    return """# PANNER screening workflow

A screening workspace advances through four steps in order. Each step reads only the confirmed outputs of the previous steps; the workspace marker records the active version of each step. Query the live state with the `panner_workflow_status` tool; start or resume with the `start_or_resume_screening` prompt.

| Workspace state | Current step | Action | Host entry points |
|---|---|---|---|
| No confirmed criteria | 01 | Establish and confirm the eligibility criteria with the user | prompt `establish_eligibility_criteria`, tool `panner_eligibility_guide` |
| Criteria confirmed, citation or confirmed pool pending | 02 | Build `citation.jsonl` from the user's sources and run the fixed deduplication | prompt `build_citation_pool`, tool `panner_citation_guide` |
| Citation pool confirmed, no frozen protocol | 03 | Compile the confirmed criteria into the screening protocol, calibrate it against the review's labeled included set, and freeze it | prompt `compile_screening_protocol`, tools `panner_compile_guide`, `panner_compile_preview`, `panner_compile_calibration`, `panner_compile_freeze` |
| Frozen protocol | 04 | Screening: answer the atomic signal questions and apply the rules | prompt —, tool `panner_screen_guide` |

Discipline shared by every step: preview before writing, show the user the full content, execute only with the same plan_hash after the user confirms, keep every confirmed version immutable, and never run steps out of order. A version that no longer matches its manifest is an integrity error to repair, not a state to continue from.

Step products:

- `criteria/vN/eligibility-criteria.md` + `manifest.json` + `sources/` — the confirmed criteria
- `citations/vN/citation.jsonl` + `manifest.json` + `sources/` — the immutable citation intake
- confirmed deduplication output directory (may be outside the workspace): `citation-pool.jsonl`, `dedup-review.jsonl`, `dedup-manifest.json`; the workspace marker stores its active absolute path
- `protocol/vN/protocol.json` + `manifest.json` — the frozen screening protocol
- `screening/vN/` — judgments, screening results, manifest
"""


def criteria_contract_resource() -> str:
    return """# Data contract: eligibility criteria (step 01)

The confirmed criteria text is a Markdown file with exactly two required sections, written in the user's language; deeper headings follow the user's criteria:

```markdown
# Review objective
<what the review is about>

# Eligibility criteria
<the criteria as the user states them>
```

Each confirmed version lives in `criteria/vN/`:

| File | Content |
|---|---|
| `eligibility-criteria.md` | The confirmed text, saved byte-for-byte |
| `manifest.json` | version, criteria_sha256, plan_hash, previous_version, origin_note, user_confirmation, unresolved_questions, sources (each with original_path, sha256, snapshot) |
| `sources/NNN-name` | Snapshots of the files the user provided |

Rules the tools enforce: both sections must have actual content; `unresolved_questions` may be non-empty and are saved with the version for step 03; a changed plan hash invalidates the preview; a new version never overwrites an old one. The workspace marker records the active version under `active_eligibility_criteria`.

Behavioural rules the host must follow are in the step 01 guidance (`panner_eligibility_guide`); the tools cannot check them mechanically.
"""


def citations_contract_resource() -> str:
    return f"""# Data contract: citations and the citation pool (step 02)

`citation.jsonl` holds one imported citation per line. Every line must contain exactly these top-level fields: {_identifiers(sorted(FIELDS))}. Each row also carries `source`, an object with exactly {_identifiers(sorted(SOURCE_FIELDS))}.

Field meanings:

| Field | Meaning |
|---|---|
| `id` | Stable identity assigned by Panner, `C000001`…, numbered in file order and within-source order; the same source occupies consecutive rows |
| `source.file` | Relative POSIX path of the original under `sources/` next to the file |
| `source.position` | Position of this citation within that source, starting at 1, consecutive |
| `source.id` | The source's own identifier, empty string when absent |
| `title` / `abstract` | Original content; empty string when the source lacks it |
| `authors` / `journal` / `container_title` / `volume` / `issue` / `pages` | Bibliographic fields as provided; `container_title` is the generic venue title |
| `year` | Integer or null |
| `publication_types` | Publication-type words as provided, string list, empty list when absent |
| `doi` / `pmid` | Identifiers as provided, empty string when absent |
| `entry_type` | Bibliographic type when identifiable (BibDedupe book type), empty string otherwise |

Deduplication (fixed BibDedupe 0.11.0; the host never judges duplicates itself):

| BibDedupe result | Panner action |
|---|---|
| pair labelled `duplicate` | candidate group; merged automatically only if the group has no conflicting non-empty DOI or PMID; other members get `duplicate_of` pointing at the screened citation |
| pair labelled `maybe` | pending relation; both citations screened separately until the user decides |
| no pair | citation screened independently |

A `duplicate` connected group containing a `maybe` member pair, or two different non-empty DOIs/PMIDs, stays unmerged and pending. The output directory contains `citation-pool.jsonl` (same rows plus `duplicate_of`, `duplicate_evidence`, `review_group`), `dedup-review.jsonl` (pending pairs, conflicts), and `dedup-manifest.json` (hashes, versions, counts, user resolutions). Downstream screening reads only rows with an empty `duplicate_of`. Invariants: every input citation appears exactly once; duplicates + to-screen = imported; outputs never overwrite the input.
"""


def protocol_contract_resource() -> str:
    return f"""# Data contract: the screening protocol (step 03)

A protocol draft is a JSON object with exactly these top-level keys: {_identifiers(sorted(TOP_LEVEL_KEYS))}. `format` is fixed to `panner-protocol-v1`. All protocol content is written in English; `criterion_refs` quotes and user decision records keep the user's original wording.

`signals` — atomic signal questions. Required fields per signal: {_identifiers(sorted(SIGNAL_KEYS))}, plus `field_rule` when `evaluation_mode` is `field_rule`:

- `unit` is one of {_identifiers(SIGNAL_UNITS)}
- `core` is a boolean; only a few topic-defining positive questions are core
- `evaluation_mode` is one of {_identifiers(EVAL_MODES)}: `field_rule` reads one structured field with `{{"field": ..., "matches": [{{"value": ..., "answer": "hit"|"miss"|"partial"}}]}}` (unlisted values answer unclear; title and abstract never use field_rule); `semantic` reads the title and abstract
- `answer_criteria` defines the selection condition for each of hit/partial/miss/unclear; absence is not miss
- `criterion_refs` cites criteria clauses verbatim: `[{{"criterion_id": "E001", "quote": "<verbatim fragment>"}}]`

`decision_rules` — combination rules. Required fields per rule: {_identifiers(sorted(RULE_KEYS))}:

- kind `disqualifier`: unit `record` (quantifier `all`) or `arms` (quantifier `any`/`all`)
- kind `qualification`: unit `comparison`, quantifier `exists`
- `condition` combines `all`/`any`/`not`; leaves are `{{"signal", "on", "expect"}}` and `expect` is only `hit` or `miss`
- `on` values: `record` for record-level signals, `each` in arms rules, `member0`/`member1` inside comparisons
- a single answer never excludes; "no qualifying comparison" requires every candidate comparison explicitly disqualified and a complete object enumeration; undecided (partial, unclear, missing, contradictory) never excludes

Scope semantics, which the two exclusion forms do not share. A record-level disqualifier (`unit: record`) removes the whole citation as soon as its condition is explicitly satisfied; an `arms` disqualifier with `quantifier: any` does the same as soon as one group satisfies it. A `qualification` rule only asks whether some candidate pair of groups forms a qualifying intervention-comparator comparison, so a `not` leaf over a group signal inside it is a per-member veto: one wrong group-level answer then defeats every candidate comparison, and the record is excluded for "no qualifying comparison". An object-level restriction - which agent counts as the intervention, which combination is excluded, which subgroup is ineligible - therefore belongs in the `answer_criteria` of the signal that reads that object, so that a group failing it simply does not satisfy the intervention question. Only a restriction whose subject is the trial itself becomes a record-level disqualifier. The preview reports a per-group veto inside a comparison rule and an `arms`/`any` disqualifier as warnings, not as schema errors.

For group or regimen signals, step 04 asks the calibrated decision model for the complete group count and for each of the first six groups in order of first mention within one request. It binds answers to `group_1`, `group_2`, etc. An unclear count or more than six groups leaves group-based rules undetermined; it never supplies a group-based exclusion.

`criteria_map` — destination of every criteria clause: `{{id, quote, occurrence, role, target, signal_ids, rule_ids, handoffs, decisions?}}`:

- `role` is one of {_identifiers(ROLES)}
- `target` is one of {_identifiers(TARGETS)}
- `handoffs` are `{{"stage": ..., "task": ...}}` with `stage` one of {_identifiers(HANDOFF_STAGES)}
- an eligibility clause must have at least one signal, one rule, one handoff, or one explicit no-restriction decision in `decisions`; no clause may be dropped silently

`clarifications` — open questions; the list must be empty before freezing. `relevance` — `{{enabled, facets, note}}`; enabled facets are `{{id, aspect, question}}`; with core signals present, relevance must be enabled, otherwise a non-empty note explains why. `screening_review` — trial scenarios `{{citation_id, scenario, entities, answers, expected_status, pool?, user_feedback?}}`; the tool evaluates each scenario with the frozen evaluator and keeps the draft when an expectation disagrees. Scenarios are authored by the same agent that wrote the draft, so they check internal consistency only; when the review carries a labeled included set, every labeled record the pool contains must appear as a retained scenario.

Calibration before freezing. A workspace may carry the review's own included set at `labels/included.txt`, one identifier per line in the vocabulary `pmid:<id>`, `doi:<doi>`, `title:<record title as it stands in the citation file>`, or a bare source id. `panner_compile_calibration(workspace_path, criteria_path, draft_path, run_config_path, labels_path?)` judges those records with the calibrated model, evaluates the draft's rules over the resulting judgments, and writes `calibration/compile-<draft_sha256[:12]>.json` (label-to-citation mapping, per-label status, the rules that fired, the answers, calls and cost). While such a report is missing for the current draft, or while it lists an excluded labeled record, `panner_compile_freeze` refuses to freeze. A label the pool does not contain is reported as `unmapped`: that is a step 02 finding, not a protocol defect.

At runtime, records with no determined core answer are retained and marked `topic_unclear` for review ordering. The frozen relevance facets are not sent to a backend and cannot exclude in this release.

Freezing produces `protocol/vN/protocol.json` + `manifest.json` (criteria version and hash, protocol hash, user confirmation, counts, trial evaluations). Step 04 reads only the frozen protocol and the confirmed pool.
"""


def citation_example_resource() -> str:
    return """# Example: one citation row (step 02)

```json
{"id":"C000001","source":{"file":"sources/pubmed.nbib","position":1,"id":"12345678"},"title":"Example title","abstract":"Example abstract","authors":"A. Author; B. Author","year":2024,"journal":"Example Journal","container_title":"","volume":"","issue":"","pages":"","publication_types":[],"doi":"","pmid":"12345678","entry_type":""}
```

Notes: the source file sits under `sources/pubmed.nbib` next to this citation.jsonl; `container_title`, `volume`, `issue`, and `pages` stay empty when the source does not provide them; `publication_types` is a list; `entry_type` stays empty when the bibliographic type is not identifiable.
"""


def protocol_example_resource() -> str:
    return """# Example: a complete protocol draft (step 03)

Criteria excerpt (step 01 product, user's language):

```markdown
# Eligibility criteria
## Inclusion
- 仅纳入 18 岁及以上成年人
- 研究须比较目标干预与对照干预
## Exclusion
- 排除病例报告
- 不允许的干预措施 D 不得出现在符合资格的比较的任一组中
```

The draft below compiles these clauses. S001 answers the population requirement; S003/S004/S005 answer per study group (`unit: arm`); S002 reads a structured field. R001 is the record-level disqualifier for case reports; R002 states that at least one ordered pair of groups forms a qualifying comparison with a symmetric (swappable) direction and no disallowed measure on either member. The trial scenarios encode the ruled edge cases: a three-arm study where the disallowed measure sits in an irrelevant arm stays retained; incomplete group information stays retained; a case report is an exclusion candidate.

```json
{
  "format": "panner-protocol-v1",
  "signals": [
    {
      "id": "S001", "unit": "population",
      "question": "Is the study population adults aged 18 years or older?",
      "core": true, "evaluation_mode": "semantic", "evidence_fields": ["title", "abstract"],
      "answer_criteria": {
        "hit": "The citation explicitly states that all participants are at least 18 years old.",
        "partial": "The citation explicitly describes both adult and minor participants.",
        "miss": "The citation explicitly states that every participant is younger than 18 years old.",
        "unclear": "Age is not stated or cannot be determined."
      },
      "criterion_refs": [{"criterion_id": "E001", "quote": "仅纳入 18 岁及以上成年人"}]
    },
    {
      "id": "S002", "unit": "citation_text",
      "question": "Is this publication a case report?",
      "core": false, "evaluation_mode": "field_rule", "evidence_fields": ["publication_types"],
      "field_rule": {"field": "publication_types", "matches": [{"value": "Case Reports", "answer": "hit"}]},
      "answer_criteria": {
        "hit": "publication_types lists a case-report type.",
        "partial": "publication_types lists mixed types, one of which is a case report.",
        "miss": "publication_types is present and names no case-report type.",
        "unclear": "publication_types is empty or absent."
      },
      "criterion_refs": [{"criterion_id": "E003", "quote": "排除病例报告"}]
    },
    {
      "id": "S003", "unit": "arm",
      "question": "Does this study group receive the target intervention?",
      "core": false, "evaluation_mode": "semantic", "evidence_fields": ["title", "abstract"],
      "answer_criteria": {
        "hit": "The citation explicitly states this group receives the target intervention.",
        "partial": "The citation explicitly states this for only part of the group or treatment period.",
        "miss": "The citation explicitly states this group's regimen and it does not contain the target intervention.",
        "unclear": "The group's regimen is absent, incomplete or conflicting."
      },
      "criterion_refs": [{"criterion_id": "E002", "quote": "研究须比较目标干预与对照干预"}]
    },
    {
      "id": "S004", "unit": "arm",
      "question": "Does this study group receive the eligible comparator?",
      "core": false, "evaluation_mode": "semantic", "evidence_fields": ["title", "abstract"],
      "answer_criteria": {
        "hit": "The citation explicitly states this group receives the eligible comparator.",
        "partial": "The citation explicitly states this for only part of the group or treatment period.",
        "miss": "The citation explicitly states this group's regimen and it does not contain the eligible comparator.",
        "unclear": "The group's regimen is absent, incomplete or conflicting."
      },
      "criterion_refs": [{"criterion_id": "E002", "quote": "研究须比较目标干预与对照干预"}]
    },
    {
      "id": "S005", "unit": "arm",
      "question": "Does this study group include the disallowed measure D?",
      "core": false, "evaluation_mode": "semantic", "evidence_fields": ["title", "abstract"],
      "answer_criteria": {
        "hit": "The citation explicitly states this group includes the disallowed measure D.",
        "partial": "The citation explicitly states D is part of the regimen for only part of the group or period.",
        "miss": "The citation explicitly states this group's regimen and it does not include D.",
        "unclear": "The group's regimen is absent, incomplete or conflicting."
      },
      "criterion_refs": [{"criterion_id": "E004", "quote": "不允许的干预措施 D 不得出现在符合资格的比较的任一组中"}]
    }
  ],
  "decision_rules": [
    {
      "id": "R001", "kind": "disqualifier", "unit": "record", "quantifier": "all",
      "condition": {"signal": "S002", "on": "record", "expect": "hit"},
      "criterion_refs": [{"criterion_id": "E003", "quote": "排除病例报告"}]
    },
    {
      "id": "R002", "kind": "qualification", "unit": "comparison", "quantifier": "exists",
      "condition": {
        "all": [
          {"any": [{"signal": "S003", "on": "member0", "expect": "hit"}, {"signal": "S003", "on": "member1", "expect": "hit"}]},
          {"any": [{"signal": "S004", "on": "member0", "expect": "hit"}, {"signal": "S004", "on": "member1", "expect": "hit"}]},
          {"not": {"signal": "S005", "on": "member0", "expect": "hit"}},
          {"not": {"signal": "S005", "on": "member1", "expect": "hit"}}
        ]
      },
      "criterion_refs": [
        {"criterion_id": "E002", "quote": "研究须比较目标干预与对照干预"},
        {"criterion_id": "E004", "quote": "不允许的干预措施 D 不得出现在符合资格的比较的任一组中"}
      ]
    }
  ],
  "criteria_map": [
    {"id": "E001", "quote": "仅纳入 18 岁及以上成年人", "occurrence": 1, "role": "eligibility", "target": "population", "signal_ids": ["S001"], "rule_ids": [], "handoffs": []},
    {"id": "E002", "quote": "研究须比较目标干预与对照干预", "occurrence": 1, "role": "eligibility", "target": "comparison", "signal_ids": ["S003", "S004"], "rule_ids": ["R002"], "handoffs": []},
    {"id": "E003", "quote": "排除病例报告", "occurrence": 1, "role": "eligibility", "target": "citation_text", "signal_ids": ["S002"], "rule_ids": ["R001"], "handoffs": []},
    {"id": "E004", "quote": "不允许的干预措施 D 不得出现在符合资格的比较的任一组中", "occurrence": 1, "role": "eligibility", "target": "comparison", "signal_ids": ["S005"], "rule_ids": ["R002"], "handoffs": [{"stage": "full_text", "task": "check whether the combination changes the group's eligibility"}]}
  ],
  "clarifications": [],
  "relevance": {
    "enabled": true,
    "facets": [{"id": "F001", "aspect": "condition", "question": "Does the citation concern the target condition?"}],
    "note": ""
  },
  "screening_review": [
    {
      "citation_id": "C000001",
      "scenario": "Three arms: A1 target intervention, A2 eligible comparator, A3 receives the disallowed measure D; the user confirmed D's restriction acts on the comparison",
      "entities": {"arms": ["A1", "A2", "A3"]},
      "answers": [
        {"signal": "S001", "on": "record", "answer": "hit"},
        {"signal": "S002", "on": "record", "answer": "miss"},
        {"signal": "S003", "on": "A1", "answer": "hit"}, {"signal": "S003", "on": "A2", "answer": "miss"}, {"signal": "S003", "on": "A3", "answer": "miss"},
        {"signal": "S004", "on": "A1", "answer": "miss"}, {"signal": "S004", "on": "A2", "answer": "hit"}, {"signal": "S004", "on": "A3", "answer": "miss"},
        {"signal": "S005", "on": "A1", "answer": "miss"}, {"signal": "S005", "on": "A2", "answer": "miss"}, {"signal": "S005", "on": "A3", "answer": "hit"}
      ],
      "expected_status": "retained"
    },
    {
      "citation_id": "C000002",
      "scenario": "The abstract does not state the comparator's regimen, so no comparison can be ruled out",
      "entities": {"arms": ["A1", "A2"]},
      "answers": [
        {"signal": "S001", "on": "record", "answer": "hit"},
        {"signal": "S002", "on": "record", "answer": "miss"},
        {"signal": "S003", "on": "A1", "answer": "hit"}, {"signal": "S003", "on": "A2", "answer": "miss"},
        {"signal": "S004", "on": "A1", "answer": "miss"}, {"signal": "S004", "on": "A2", "answer": "unclear"},
        {"signal": "S005", "on": "A1", "answer": "miss"}, {"signal": "S005", "on": "A2", "answer": "unclear"}
      ],
      "expected_status": "retained"
    },
    {
      "citation_id": "C000003",
      "scenario": "publication_types lists Case Reports; the record-level disqualifying condition is satisfied",
      "entities": {"arms": []},
      "answers": [{"signal": "S002", "on": "record", "answer": "hit"}],
      "expected_status": "excluded_candidate"
    }
  ]
}
```

This example passes `panner_compile_preview` structurally when the criteria text above is confirmed as the step 01 version: every `quote` is verbatim in the criteria, the trial scenarios evaluate to their expected statuses, and the relevance block is consistent with the core mark on S001.
"""


RESOURCES = {
    WORKFLOW_URI: ("PANNER screening workflow", workflow_resource),
    CRITERIA_CONTRACT_URI: ("Data contract: eligibility criteria", criteria_contract_resource),
    CITATIONS_CONTRACT_URI: ("Data contract: citations and the citation pool", citations_contract_resource),
    PROTOCOL_CONTRACT_URI: ("Data contract: the screening protocol", protocol_contract_resource),
    CITATION_EXAMPLE_URI: ("Example: one citation row", citation_example_resource),
    PROTOCOL_EXAMPLE_URI: ("Example: a complete protocol draft", protocol_example_resource),
}


def list_resources() -> list[dict]:
    return [
        {"uri": uri, "name": name, "mimeType": "text/markdown"}
        for uri, (name, _) in RESOURCES.items()
    ]


def read_resource(uri: str) -> str:
    if uri not in RESOURCES:
        raise ValueError(f"unknown PANNER resource: {uri}")
    return RESOURCES[uri][1]()


def example_protocol_draft() -> dict:
    """The machine-readable example draft, used by tests to stay verified."""
    text = protocol_example_resource()
    block = text.rsplit("```json\n", 1)[1].split("\n```", 1)[0]
    return json.loads(block)
