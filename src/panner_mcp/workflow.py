"""Cross-step workflow status: which step is current and what to do next."""

from __future__ import annotations

from .citations import citation_status
from .criteria import criteria_status
from .dedup import dedup_status
from .protocol import protocol_status
from .screening import screen_status

GUIDANCE_TOOL = {"01": "panner_eligibility_guide", "02": "panner_citation_guide", "03": "panner_compile_guide", "04": "panner_screen_guide"}
PROMPT = {"01": "establish_eligibility_criteria", "02": "build_citation_pool", "03": "compile_screening_protocol"}
NEXT_ACTION = {
    "01": "establish and confirm the eligibility criteria with the user",
    "02": "build citation.jsonl from the user's sources and run the fixed deduplication",
    "03": "compile the confirmed criteria into the screening protocol",
    "04": "run screening with panner_screen_preview and panner_screen_confirm",
}


def _step(fn, workspace_path: str, extra) -> dict:
    try:
        result = fn(workspace_path)
    except (OSError, ValueError) as exc:
        # A tampered or missing version must surface as a state the user can
        # repair, not as a tool crash.
        return {"status": "integrity_error", "error": str(exc), **extra(None)}
    return {"status": result["status"], "version": result.get("version"), **extra(result)}


def _citation_pool_status(workspace_path: str) -> dict:
    citations = citation_status(workspace_path)
    if citations["status"] != "confirmed":
        return citations
    pool = dedup_status(workspace_path)
    if pool["status"] != "confirmed":
        return {"status": "pool_pending", "version": citations["version"], "citations": citations["citations"]}
    return {"status": "confirmed", "version": citations["version"], "citations": citations["citations"],
            "pool_path": pool["pool_path"], "counts": pool["counts"]}


def workflow_status(workspace_path: str) -> dict:
    steps: dict[str, dict] = {}
    steps["01"] = _step(
        criteria_status,
        workspace_path,
        lambda result: {"open_questions": len(result.get("unresolved_questions", []))} if result else {},
    )
    steps["02"] = _step(_citation_pool_status, workspace_path, lambda result: {
        "citations": result.get("citations"), "pool_path": result.get("pool_path"), "counts": result.get("counts")
    } if result else {})
    steps["03"] = _step(protocol_status, workspace_path, lambda result: {"counts": result.get("counts")} if result else {})
    steps["04"] = _step(screen_status, workspace_path, lambda result: {
        "counts": result.get("counts"), "plan_hash": result.get("plan_hash")
    } if result else {})

    current = "04"
    for step in ("01", "02", "03"):
        if steps[step]["status"] != "confirmed":
            current = step
            break

    if steps[current].get("status") == "integrity_error":
        action = f"repair the step {current} version before continuing: " + steps[current].get("error", "")
    elif steps[current].get("status") == "not_available":
        action = NEXT_ACTION["04"]
    elif current == "04" and steps[current]["status"] == "confirmed":
        action = "review the screening results and export the handoff files"
    elif current == "04" and steps[current]["status"] == "partial":
        action = "resume unfinished citations and review failed citations before final handoff"
    elif current == "04" and steps[current]["status"] == "stale":
        action = "run screening with the current confirmed protocol and citation pool"
    else:
        action = NEXT_ACTION[current]
    return {
        "workspace_path": workspace_path,
        "current_step": current,
        "steps": steps,
        "next_action": action,
        "guidance_tool": GUIDANCE_TOOL.get(current),
        "prompt": PROMPT.get(current),
    }
