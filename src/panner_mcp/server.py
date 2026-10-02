"""Local MCP entry point for PANNER."""

from __future__ import annotations

import asyncio
from threading import Event
from typing import Annotated, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.resources import FunctionResource
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from .calibration import calibrate_compile
from .citations import citation_status, confirm_citations, preview_citations
from .criteria import confirm_criteria, criteria_status, preview_criteria
from .dedup import dedup_status, deduplicate
from .export import export_screening
from .screening import confirm_screen, continue_screen, preview_screen, screen_status
from .protocol import freeze_compile, preview_compile, protocol_status
from .resources import RESOURCES, read_resource
from .workflow import workflow_status


def _static_resources() -> list[FunctionResource]:
    return [
        FunctionResource(uri=uri, name=name, mime_type="text/markdown", fn=reader)
        for uri, (name, reader) in RESOURCES.items()
    ]


mcp = MCPServer(
    "PANNER",
    resources=_static_resources(),
    instructions=(
        "PANNER is a systematic-review title-and-abstract screening tool reached over MCP. "
        "A screening workspace advances through steps: 01 establish the eligibility criteria with the user, "
        "02 build the citation pool, 03 compile the screening protocol, 04 screening. "
        "Call panner_workflow_status with the workspace path first: it reports the current step, "
        "what each step produced, and which guide tool or prompt to use next. "
        "Host-readable contracts and worked examples are available as resources "
        "(panner://workflow, panner://contracts/*, panner://examples/*); read them before drafting. "
        "Never skip a user confirmation and never run steps out of order."
    ),
)

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)
STATIC_READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
LOCAL_WRITE = ToolAnnotations(destructive_hint=False, open_world_hint=True)
REMOTE_WRITE = ToolAnnotations(destructive_hint=False, open_world_hint=True)


class GuideResult(BaseModel):
    step: Literal["01", "02", "03", "04"]
    task: str
    guidance: str


class ContractResult(BaseModel):
    uri: str
    name: str
    content: str


class SourceInfo(BaseModel):
    original_path: str
    sha256: str
    size: int


class PreviewResult(BaseModel):
    status: Literal["ready_for_confirmation", "draft"]
    workspace_path: str
    file_name: str
    full_text_to_show_user: str
    origin_note: str
    sources: list[SourceInfo]
    unresolved_questions: list[str]
    issues: list[str]
    plan_hash: str


class ConfirmResult(BaseModel):
    status: Literal["confirmed"]
    version: str
    criteria_path: str
    manifest_path: str
    criteria_sha256: str
    next_step: str


class StatusResult(BaseModel):
    status: Literal["confirmed", "not_confirmed"]
    workspace_path: str | None = None
    version: str | None = None
    criteria_path: str | None = None
    criteria_sha256: str | None = None
    unresolved_questions: list[str] | None = None


class CitationPreviewResult(BaseModel):
    status: Literal["ready_for_confirmation", "draft"]
    input_path: str
    input_sha256: str
    workspace_path: str
    citations: int
    input_lines: int
    source_coverage: dict[str, dict[str, int]]
    unverified_source_counts: list[str]
    issues: list[str]
    plan_hash: str
    sources: list[dict]


class CitationConfirmResult(BaseModel):
    status: Literal["confirmed"]
    version: str
    citation_path: str
    manifest_path: str
    citation_sha256: str
    citations: int
    next_step: str


class CitationStatusResult(BaseModel):
    status: Literal["confirmed", "not_confirmed"]
    workspace_path: str | None = None
    version: str | None = None
    citation_path: str | None = None
    citation_sha256: str | None = None
    citations: int | None = None


class DedupPreviewResult(BaseModel):
    status: Literal["planned"]
    plan_hash: str
    input_path: str
    output_dir: str
    input_sha256: str
    input_manifest_sha256: str
    engine: str
    engine_version: str
    policy_version: str
    runtime_versions: dict[str, str]
    counts: dict[str, int]
    groups_preview: list[dict]
    remaining_groups: int
    remaining_pending_groups: int
    files_to_write: list[str]
    resolution_sha256: str | None


class DedupConfirmResult(BaseModel):
    status: Literal["confirmed"]
    plan_hash: str
    output_dir: str
    citation_pool_path: str
    review_path: str
    manifest_path: str
    counts: dict[str, int]


class DedupStatusResult(BaseModel):
    status: Literal["confirmed", "not_confirmed"]
    workspace_path: str
    pool_path: str | None = None
    counts: dict[str, int] | None = None


class CompilePreviewResult(BaseModel):
    status: Literal["ready_for_confirmation", "draft"]
    workspace_path: str
    criteria_version: str
    criteria_sha256: str
    counts: dict[str, int]
    issues: list[str]
    warnings: list[str] = []
    trial_evaluations: list[dict]
    trial_mismatches: list[dict]
    plan_hash: str
    guarantees: list[str]


class CompileCalibrationResult(BaseModel):
    status: Literal["calibrated"]
    workspace_path: str
    report_path: str
    draft_sha256: str
    model: str
    labels: int
    mapped: int
    retained: int
    excluded: int
    unmapped: list[str]
    excluded_labels: list[dict]
    calls: int
    cost_usd: float


class CompileFreezeResult(BaseModel):
    status: Literal["confirmed"]
    version: str
    protocol_path: str
    manifest_path: str
    protocol_sha256: str
    next_step: str


class ProtocolStatusResult(BaseModel):
    status: Literal["confirmed", "not_confirmed"]
    workspace_path: str | None = None
    version: str | None = None
    protocol_path: str | None = None
    protocol_sha256: str | None = None
    criteria_version: str | None = None
    counts: dict[str, int] | None = None

GUIDANCE = """You are running PANNER step 01: establishing the eligibility criteria for a systematic review with the user.
Converse with the user in the user's language.
First ask whether the user already has eligibility criteria. If yes, take them in and record the original wording and its provenance faithfully; if not, start from the review objective and help the user express, in rounds, the population, intervention or exposure, comparators, study designs and other eligibility boundaries they can already determine.
Organise the material so that every recorded decision can be traced back to the original text or to an explicit statement by the user in the conversation.
Only the user's explicit wording defines the criteria: dimensions the user has not discussed stay unrecorded rather than becoming "no restriction", and you never add restrictions from common practice, search results, or your own inference.
When the provided material contradicts itself, uses terms you cannot record accurately, or omits a part the review needs, ask the user now; finer details that only matter for compiling signals and decision rules belong to step 03.
Probe only the ambiguities needed to form an accurate initial text; do not force exhaustive compilation details such as multi-arm comparisons or combination regimens at this stage.
Update the working draft every round. List questions the user has not yet decided in unresolved_questions for step 03 to pursue when compiling signals and decision rules; never write an undecided matter as "no restriction".
Organise the user-approved current text into complete Markdown containing the two sections "# Review objective" and "# Eligibility criteria", recording the original materials and the provenance of each decision.
Call panner_eligibility_preview and show the returned full text and open-questions list to the user. Only after the user confirms that it accurately records the **current** criteria, call panner_eligibility_confirm with the user's confirmation wording. Confirming this text does not mean the criteria are ready for automated screening, and it does not mean every compilation detail is resolved.
"""

CITATION_GUIDANCE = """You are running PANNER step 02: building the citation pool. Converse with the user in the user's language.
Inspect each source file's actual structure first: determine the boundary of one citation, field positions, encoding, and ordering within the source. Never guess a format from the file extension alone.
When a record boundary, a field meaning, or the content cannot be reliably identified, show the specific doubt to the user and ask for an explanation or a better export; do not improvise a conversion.
Show the user the source-and-field mapping plan and representative samples. Keep the originals; reconcile the number of original record units against the converted citation count per source, and report the non-empty counts of title, abstract, DOI, and PMID; mark counts that cannot be independently verified as unknown and state this verification limit to the user. Never drop a citation silently and never guess-fill missing fields.
Place the originals under sources/ next to citation.jsonl; source.file is a relative POSIX path inside that directory. Create citation.jsonl with the fixed structure, one citation per line, numbered in source order. entry_type is the bibliographic type and stays empty when unidentified; publication_types holds the publication-type words provided by the source; the two are different.
Call panner_citation_preview and resolve every structural error and reconciliation difference before showing the result; the plan stays a draft while counts disagree or the structure is invalid. Show the counts and field coverage to the user. After the user confirms the mapping, call panner_citation_confirm to obtain the immutable citation.jsonl version.
Pass the confirmed version to panner_deduplicate_preview: show the automatic duplicates, groups awaiting decision, and the files to be written; after the user confirms, call panner_deduplicate_confirm with the same plan_hash and the user's confirmation wording. maybe pairs, within-group classification conflicts, and DOI/PMID contradictions stay pending; when the decision file changes, recompute into a new output directory.
Never edit the deduplication logic or judge duplicate relations yourself in bulk; the fixed engine and the user's adjudications produce them.
Downstream screening reads only rows with an empty duplicate_of in the confirmed citation-pool.jsonl; before moving to step 03, show the user the counts (imported, duplicates, to screen, pending groups) and the pending groups.
"""

COMPILE_GUIDANCE = """You are running PANNER step 03: compiling the confirmed eligibility criteria into a freezable screening protocol. Converse with the user in the user's language.
First read the current criteria text and its open-questions list returned by panner_eligibility_status. Split the criteria into individually traceable clauses (E001...) and determine each clause's role (eligibility, review outcome, retrieval, data handling, or context) and destination; compile what the title and abstract can observe, record the rest as handoffs for full-text, retrieval, or data work, and never drop a clause silently - an eligibility clause must have at least one signal, one rule, one handoff, or one explicit no-restriction decision in decisions.
Implicature is a positive obligation: axes the criteria do not mention default to no restriction. Do not generate exclusion questions on publication forms, languages, years, or countries the criteria never mention, and do not infer exclusions from common practice or from features of the retrieved pool; "include RCTs" does not by itself exclude conference abstracts or protocols.
When compiling a clause, if a decision is missing that would change the answer conditions or the combined outcome, ask the user about exactly that gap, across seven dimensions: unit of judgment, which object carries the restriction (the record, the comparison, or a single group), quantifier, comparison relation and direction, regimen composition, scope of a restriction, and what partial evidence or limited observability means for this clause. When the object is a group, ask also whether the restriction is part of that group's eligibility definition rather than a separate exclusion. Open questions have no default answers; if an answer modifies the eligibility criteria, first save a new version through step 01's preview and confirm, then continue compiling.
Distinguish what a criterion constrains: a requirement about the study ("the study measures the outcome") is not a requirement about the abstract text ("the abstract states that the outcome was measured"). Compile a text requirement only when the user explicitly set it as an eligibility condition on the abstract wording and explained its role; otherwise record the handoff. Absence from the abstract is not miss, with the single exception of an explicitly confirmed complete-abstract requirement whose verified absence answers per that requirement's own conditions.
Draft atomic signal questions: one factual dimension per question, state the object it addresses - study, population, group, regimen, or citation text; comparison relations belong in decision rules - and write the selection condition for each of hit/partial/miss/unclear. partial means the citation explicitly shows both qualifying and non-qualifying parts within the dimension and never stands for merely insufficient information; unclear covers unstated, insufficient, missing, or contradictory information; absence is not miss. Write every hit condition as a statement the citation must explicitly contain, and never let a hit follow from an inference: an agent described as being in development is not thereby an unapproved agent, and a study of that agent is not thereby without phase 3 trial data; when the criteria restrict on approval status or on the existence of phase 3 trial data, the hit condition must require the citation to say so.
Bindings belong to the answers, not to the signal: step 04 records {signal, on, answer} where on names the observed object, and objects the citation does not fully describe stay in the answers as unclear rather than being invented. Cite the criteria verbatim in criterion_refs; when several questions cite the same fragment, explain what distinct exclusion fact each question captures.
Draft decision rules with the fixed schema: kind disqualifier (unit record, or arms with quantifier any/all) or qualification (unit comparison, quantifier exists); a condition combines all/any/not with leaves {signal, on, expect}, and expect is only hit or miss.
The two ways to exclude are not interchangeable. A record-level disqualifier removes the whole citation once its condition is explicitly satisfied. A comparison rule answers one question only: whether some candidate pair of groups forms a qualifying intervention-comparator comparison; a `not` leaf over a group signal inside it is a per-member veto, so one wrong group-level answer then defeats every candidate comparison at once. An object-level restriction - which agents count, which combination is excluded, which subgroup is ineligible - therefore belongs in the answer criteria of the signal that reads that object, so that a group failing it simply does not satisfy the intervention question; only a restriction whose subject is the trial itself becomes a record-level disqualifier. Never implement an object-level restriction as a per-group veto inside a comparison rule.
The combination logic is fixed: an explicitly satisfied disqualifier on its confirmed scope produces an exclusion candidate; "no qualifying comparison" may only be concluded when every candidate comparison is explicitly disqualified and the enumeration of objects is complete; answers that are partial, unclear, missing, or contradictory stay undecided, and undecided never excludes - the citation is retained and the reason is kept.
Mark the few topic-defining positive questions core. A core mark never grants a single question the power to exclude; when every core question stays unclear, the citation is retained and marked topic_unclear for review ordering. Keep relevance.facets in the frozen protocol, but this release does not call a relevance backend or exclude through relevance. If no safe core question exists, set relevance.enabled to false and explain why in relevance.note.
Write all protocol content in English - questions, answer criteria, facets, handoff tasks, and scenario descriptions; keep the user's original wording verbatim in criterion_refs and in decision records.
Pick trial scenarios from the step 02 citation pool and the actual criteria (clearly relevant, clearly irrelevant, borderline, missing information, multi-arm, swapped roles, combination regimens), write the expected answers and expected outcomes into screening_review, and pay particular attention to the boundaries between miss and unclear, and between partial and unclear, when reading the four values. You author both the answers and the expectations, so a trial read only checks internal consistency; when the review carries a labeled included set, every labeled record the pool contains must appear as a retained scenario, and the calibrated read below is what actually tests the draft against the review.
A draft is not ready to freeze on structure alone. When the workspace carries a labeled included set (`labels/included.txt`, one identifier per line as pmid:, doi:, title:, or a source id), call panner_compile_calibration with the run configuration: it judges those records with the calibrated model and reports every one the rules would exclude. Freezing is refused while any labeled included record is excluded or while no calibration report exists for the current draft. A label the pool does not contain is reported as unmapped, which is a step 02 finding rather than a protocol defect: report it to the user instead of bending the protocol.
Call panner_compile_preview: resolve every issue and trial mismatch - the draft stays a draft while clarifications are open or a trial disagrees with its expected outcome - and show the signals, the rules, the criteria coverage, the trial results, and every warning to the user in full. The preview warns when a comparison rule carries per-group vetoes or when an arms/any disqualifier excludes a whole record; a warning is not a schema error, but treat it as a question to answer before freezing.
After the user confirms that the protocol expresses the criteria accurately, call panner_compile_freeze with the confirmation wording. After freezing, step 04 reads only this protocol.
"""

ORCHESTRATION_GUIDANCE = """You are orchestrating a PANNER screening workspace. Converse with the user in the user's language.
Call panner_workflow_status first. It reports which step is current, what each step has produced, and what to do next.
Run the current step with its instructions: call its guide tool, then use panner_get_contract for the corresponding data contract or example if the host does not expose MCP resources. The tools enforce previews, explicit user confirmation, and version discipline; a step reads only confirmed outputs of the previous steps.
Never skip a user confirmation and never run steps out of order. When a step finishes, call panner_workflow_status again and continue until screening is complete.
"""


class WorkflowStatusResult(BaseModel):
    workspace_path: str
    current_step: Literal["01", "02", "03", "04"]
    steps: dict[str, dict]
    next_action: str
    guidance_tool: str | None = None
    prompt: str | None = None


class ScreenPreviewResult(BaseModel):
    status: Literal["planned"]
    plan_hash: str
    workspace_path: str
    protocol_version: str
    criteria_path: str
    citation_path: str
    pool_path: str
    run_config_sha256: str
    counts: dict[str, int]
    backend: str
    model: str
    judge_channel: str | None = None
    endpoint: str
    concurrency: int
    cost_cap: int | None
    batch_size: int
    estimated_calls: int
    estimated_cost_usd: float
    estimate_basis: str
    files_to_write: list[str]


class ScreenOutcomeCounts(BaseModel):
    excluded: int
    retained: int
    failed: int
    pending: int
    to_screen: int


class ScreenConfirmResult(BaseModel):
    status: Literal["confirmed", "partial"]
    plan_hash: str
    version: str
    results_path: str
    judgments_path: str
    manifest_path: str
    counts: ScreenOutcomeCounts
    stopped_early: bool


class ScreenExportResult(BaseModel):
    status: Literal["exported"]
    workspace_path: str
    output_dir: str
    files: dict[str, str]
    counts: dict[str, int]


class ScreenStatusResult(BaseModel):
    status: Literal["confirmed", "partial", "stale", "not_available"]
    plan_hash: str | None = None
    workspace_path: str | None = None
    version: str | None = None
    results_path: str | None = None
    counts: ScreenOutcomeCounts | None = None
    cost_usd: float | None = None
    recomputation_mismatches: list[str] | None = None


@mcp.prompt()
def establish_eligibility_criteria() -> str:
    """Start the PANNER eligibility-criteria conversation."""
    return GUIDANCE


@mcp.prompt()
def build_citation_pool() -> str:
    """Start the PANNER citation-pool conversation."""
    return CITATION_GUIDANCE


@mcp.prompt()
def compile_screening_protocol() -> str:
    """Start the PANNER protocol-compilation conversation."""
    return COMPILE_GUIDANCE


@mcp.prompt()
def start_or_resume_screening() -> str:
    """Start or resume a PANNER screening workspace from its current step."""
    return ORCHESTRATION_GUIDANCE


@mcp.tool(annotations=STATIC_READ)
def panner_eligibility_guide() -> GuideResult:
    """Step 01 instructions for the initial criteria interview and confirmation; compilation clarifications belong to step 03."""
    return GuideResult(step="01", task="establish eligibility criteria with the user", guidance=GUIDANCE)


@mcp.tool(annotations=READ_ONLY)
def panner_eligibility_preview(
    workspace_path: str,
    markdown: str,
    origin_note: str,
    source_paths: list[str],
    unresolved_questions: list[str],
) -> PreviewResult:
    """Read-only preview of the criteria text with structure and open-question checks, returning the plan hash for confirmation."""
    try:
        return PreviewResult.model_validate(
            preview_criteria(workspace_path, markdown, origin_note, source_paths, unresolved_questions)
        )
    except (OSError, ValueError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=LOCAL_WRITE)
def panner_eligibility_confirm(
    workspace_path: str,
    markdown: str,
    origin_note: str,
    source_paths: list[str],
    unresolved_questions: list[str],
    plan_hash: str,
    user_confirmation: str,
) -> ConfirmResult:
    """Save a new criteria version after the user confirms the exact preview; rejects a changed plan or unresolved questions."""
    try:
        return ConfirmResult.model_validate(
            confirm_criteria(
                workspace_path, markdown, origin_note, source_paths, unresolved_questions, plan_hash, user_confirmation
            )
        )
    except (OSError, ValueError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=READ_ONLY)
def panner_eligibility_status(workspace_path: str) -> StatusResult:
    """Read the confirmed criteria version and verify it against its manifest."""
    try:
        return StatusResult.model_validate(criteria_status(workspace_path))
    except (OSError, ValueError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=STATIC_READ)
def panner_citation_guide() -> GuideResult:
    """Step 02 instructions for source mapping, reconciliation, and the fixed deduplication."""
    return GuideResult(step="02", task="build the citation pool", guidance=CITATION_GUIDANCE)


@mcp.tool(annotations=READ_ONLY)
def panner_citation_preview(
    input_path: str,
    workspace_path: str,
    mapping_note: str,
    source_counts: dict[str, int | None],
) -> CitationPreviewResult:
    """Read-only check of the citation.jsonl structure, source files, counts, and field coverage."""
    try:
        return CitationPreviewResult.model_validate(preview_citations(input_path, workspace_path, mapping_note, source_counts))
    except (OSError, ValueError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=LOCAL_WRITE)
def panner_citation_confirm(
    input_path: str,
    workspace_path: str,
    mapping_note: str,
    source_counts: dict[str, int | None],
    plan_hash: str,
    user_confirmation: str,
) -> CitationConfirmResult:
    """Save the immutable citation.jsonl and original sources after the user confirms the mapping and counts."""
    try:
        return CitationConfirmResult.model_validate(
            confirm_citations(input_path, workspace_path, mapping_note, source_counts, plan_hash, user_confirmation)
        )
    except (OSError, ValueError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=READ_ONLY)
def panner_citation_status(workspace_path: str) -> CitationStatusResult:
    """Read the confirmed citation.jsonl version and verify it against its manifest."""
    try:
        return CitationStatusResult.model_validate(citation_status(workspace_path))
    except (OSError, ValueError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=READ_ONLY)
def panner_deduplicate_preview(
    input_path: Annotated[str, Field(description="Absolute path to this workspace's active confirmed citation.jsonl")],
    output_dir: Annotated[str, Field(description="New or empty output directory for the confirmed citation pool")],
    resolutions_path: str | None = None,
) -> DedupPreviewResult:
    """Preview fixed BibDedupe results, pending groups, and output paths without writing files."""
    try:
        return DedupPreviewResult.model_validate(deduplicate(input_path, output_dir, resolutions_path))
    except (OSError, ValueError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=LOCAL_WRITE)
def panner_deduplicate_confirm(
    input_path: Annotated[str, Field(description="The same active citation.jsonl used in the preview")],
    output_dir: Annotated[str, Field(description="The same new or empty output directory used in the preview")],
    plan_hash: Annotated[str, Field(description="Plan hash returned by panner_deduplicate_preview")],
    user_confirmation: Annotated[str, Field(description="The user's explicit confirmation wording for this plan")],
    resolutions_path: str | None = None,
) -> DedupConfirmResult:
    """Write a new citation-pool directory after the user confirms its exact deduplication plan."""
    try:
        if not user_confirmation.strip():
            raise ValueError("record the user's explicit confirmation of the deduplication plan")
        return DedupConfirmResult.model_validate(
            deduplicate(input_path, output_dir, resolutions_path, confirm=plan_hash, user_confirmation=user_confirmation)
        )
    except (OSError, ValueError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=READ_ONLY)
def panner_deduplicate_status(workspace_path: str) -> DedupStatusResult:
    """Verify the active citation pool and its relationship to this workspace's active citations."""
    try:
        return DedupStatusResult.model_validate(dedup_status(workspace_path))
    except (OSError, ValueError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=STATIC_READ)
def panner_compile_guide() -> GuideResult:
    """Step 03 instructions for clause splitting, semantic clarification, signal and rule drafting, and trial reads."""
    return GuideResult(step="03", task="compile the screening protocol from confirmed criteria", guidance=COMPILE_GUIDANCE)


@mcp.tool(annotations=READ_ONLY)
def panner_compile_preview(workspace_path: str, criteria_path: str, draft_path: str) -> CompilePreviewResult:
    """Read-only validation of the protocol draft: structure, criteria coverage, verbatim quotes, and trial evaluations."""
    try:
        return CompilePreviewResult.model_validate(preview_compile(workspace_path, criteria_path, draft_path))
    except (OSError, ValueError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=LOCAL_WRITE)
def panner_compile_freeze(
    workspace_path: str, criteria_path: str, draft_path: str, plan_hash: str, user_confirmation: str
) -> CompileFreezeResult:
    """Freeze protocol/vN after the user confirms the full preview; refuses while clarifications are open or a trial disagrees."""
    try:
        return CompileFreezeResult.model_validate(
            freeze_compile(workspace_path, criteria_path, draft_path, plan_hash, user_confirmation)
        )
    except (OSError, ValueError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=REMOTE_WRITE)
def panner_compile_calibration(
    workspace_path: str,
    criteria_path: str,
    draft_path: str,
    run_config_path: str,
    labels_path: str | None = None,
) -> CompileCalibrationResult:
    """Judge the review's labeled included records with the draft protocol; freezing refuses while any is excluded."""
    try:
        return CompileCalibrationResult.model_validate(
            calibrate_compile(workspace_path, criteria_path, draft_path, run_config_path, labels_path)
        )
    except (OSError, ValueError, RuntimeError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=READ_ONLY)
def panner_compile_status(workspace_path: str) -> ProtocolStatusResult:
    """Read the frozen protocol version and verify it against its manifest."""
    try:
        return ProtocolStatusResult.model_validate(protocol_status(workspace_path))
    except (OSError, ValueError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=READ_ONLY)
def panner_workflow_status(workspace_path: str) -> WorkflowStatusResult:
    """Report the workspace's current step, what each step produced, and what to do next."""
    try:
        return WorkflowStatusResult.model_validate(workflow_status(workspace_path))
    except (OSError, ValueError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=STATIC_READ)
def panner_get_contract(
    name: Literal["workflow", "criteria", "citations", "protocol", "citation_example", "protocol_example"],
) -> ContractResult:
    """Read a PANNER data contract or worked example when the host does not expose MCP resources."""
    uri_by_name = {
        "workflow": "panner://workflow",
        "criteria": "panner://contracts/criteria",
        "citations": "panner://contracts/citations",
        "protocol": "panner://contracts/protocol",
        "citation_example": "panner://examples/citation",
        "protocol_example": "panner://examples/protocol-draft",
    }
    uri = uri_by_name[name]
    return ContractResult(uri=uri, name=RESOURCES[uri][0], content=read_resource(uri))


SCREEN_GUIDANCE = """You are running PANNER step 04: screening. Converse with the user in the user's language.
Read the active frozen protocol, the citation pool derived from the active citations, and the calibrated-model run configuration. Only rows with an empty duplicate_of are screened.
Call panner_screen_preview and show the user the plan: how many citations, how many semantic signals, the model and destination endpoint, the concurrency, the maximum calls, the estimated cost with its basis, and the files to be written. Explain that title and abstract are sent to the configured decision endpoint. Resolve every issue before confirming.
After the user confirms, call panner_screen_confirm with the same plan_hash. A call processes at most one configured batch. While its result is partial with pending citations, call panner_screen_continue with that plan_hash; the saved confirmation remains valid for this unchanged plan. Failures retry per the config and never block other records. Failed or pending citations remain visible for later assessment.
Show the user the results - excluded with their rule basis, retained with their undetermined reasons, and failed or unfinished with their errors - then call panner_workflow_status to continue.
"""


@mcp.tool(annotations=STATIC_READ)
def panner_screen_guide() -> GuideResult:
    """Step 04 instructions for screening execution and review."""
    return GuideResult(step="04", task="run screening over the confirmed pool", guidance=SCREEN_GUIDANCE)


@mcp.tool(annotations=READ_ONLY)
def panner_screen_preview(
    workspace_path: Annotated[str, Field(description="Absolute path to the existing PANNER workspace")],
    protocol_path: Annotated[str, Field(description="Absolute path to this workspace's active frozen protocol")],
    pool_path: Annotated[str, Field(description="Absolute path to the pool derived from this workspace's active citations")],
    run_config_path: Annotated[str, Field(description="Absolute path to the calibrated-model run configuration")],
) -> ScreenPreviewResult:
    """Read-only plan for a screening run: counts, backend, concurrency, estimated calls and cost."""
    try:
        return ScreenPreviewResult.model_validate(preview_screen(workspace_path, protocol_path, pool_path, run_config_path))
    except (OSError, ValueError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=REMOTE_WRITE)
async def panner_screen_confirm(
    workspace_path: Annotated[str, Field(description="Absolute PANNER workspace path")],
    protocol_path: Annotated[str, Field(description="Active frozen protocol path from the preview")],
    pool_path: Annotated[str, Field(description="Active confirmed citation pool path from the preview")],
    run_config_path: Annotated[str, Field(description="Run configuration path from the preview")],
    plan_hash: Annotated[str, Field(description="Plan hash returned by panner_screen_preview")],
    user_confirmation: Annotated[str, Field(description="The user's explicit confirmation wording for this plan")],
    ctx: Context,
) -> ScreenConfirmResult:
    """Start a bounded screening batch after confirmation; return partial status when citations remain."""
    loop = asyncio.get_running_loop()
    cancelled = Event()

    def progress(done: int, total: int) -> None:
        asyncio.run_coroutine_threadsafe(ctx.report_progress(done, total, "Screening citations"), loop)

    try:
        return ScreenConfirmResult.model_validate(
            await asyncio.to_thread(
                confirm_screen, workspace_path, protocol_path, pool_path, run_config_path, plan_hash, user_confirmation,
                progress=progress, cancel_event=cancelled,
            )
        )
    except asyncio.CancelledError:
        cancelled.set()
        raise
    except (OSError, ValueError, RuntimeError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=REMOTE_WRITE)
async def panner_screen_continue(
    workspace_path: Annotated[str, Field(description="Workspace containing the active partial screening run")],
    plan_hash: Annotated[str, Field(description="Plan hash reported by panner_screen_status or the prior batch")],
    ctx: Context,
    retry_failed: Annotated[bool, Field(description="Requeue infrastructure-failed citations (their events carry no judgment) and judge them again")] = False,
) -> ScreenConfirmResult:
    """Continue the active screening run with its already confirmed plan, one bounded batch at a time."""
    loop = asyncio.get_running_loop()
    cancelled = Event()

    def progress(done: int, total: int) -> None:
        asyncio.run_coroutine_threadsafe(ctx.report_progress(done, total, "Screening citations"), loop)

    try:
        return ScreenConfirmResult.model_validate(
            await asyncio.to_thread(continue_screen, workspace_path, plan_hash, progress=progress,
                                    cancel_event=cancelled, retry_failed=retry_failed)
        )
    except asyncio.CancelledError:
        cancelled.set()
        raise
    except (OSError, ValueError, RuntimeError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=LOCAL_WRITE)
def panner_screen_export(workspace_path: str, output_dir: str, results_path: str | None = None) -> ScreenExportResult:
    """Export a colour-coded xlsx report and retained/excluded RIS files from confirmed results."""
    try:
        return ScreenExportResult.model_validate(export_screening(workspace_path, output_dir, results_path))
    except (OSError, ValueError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=READ_ONLY)
def panner_screen_status(workspace_path: str) -> ScreenStatusResult:
    """Read the latest screening results and recompute them from judgments to verify consistency."""
    try:
        return ScreenStatusResult.model_validate(screen_status(workspace_path))
    except (OSError, ValueError) as exc:
        raise ToolError(str(exc)) from exc


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
