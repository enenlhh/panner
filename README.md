# PANNER

**Systematic-review title-and-abstract screening, reached over MCP.**

PANNER is a local MCP server for evidence-review teams. It takes a review's eligibility criteria and citation search exports, and — under pre-declared, checkable rules — automatically excludes as many citations as the evidence supports, while every remaining record passes safely to full-text assessment. Safety first: a wrong exclusion costs a study, a wrong retention only costs a re-read.

- **low-cost decision models** (e.g. the Jev decisions API) answer frozen four-value questions; deterministic rules handle straightforward records before any model call
- everything is versioned, previewed, confirmed and re-computable; the host agent carries no hidden state

## Install

Requires Python 3.10+.

```bash
uv sync          # or: pip install .
```

## Register in your MCP host

Point your host's MCP configuration at the installed server:

```json
{
  "mcpServers": {
    "panner": {
      "command": "panner-mcp",
      "args": []
    }
  }
}
```

On connect the server announces instructions and exposes tools, prompts, and resources. Start any workspace by calling `panner_workflow_status` (or the `start_or_resume_screening` prompt) with an absolute workspace directory — the state machine tells you which step is current and what to run next.

## The workflow

| Step | Tool set | Product |
|---|---|---|
| 01 — establish the eligibility criteria with the user | `panner_eligibility_guide / preview / confirm / status` | `criteria/vN/eligibility-criteria.md` (+ manifest, source snapshots) |
| 02 — build the citation pool | `panner_citation_guide / preview / confirm / status`, `panner_deduplicate_preview / confirm / status` | immutable `citation.jsonl`, then `citation-pool.jsonl` with deduplication review |
| 03 — compile the screening protocol | `panner_compile_guide / preview / calibration / freeze / status` | frozen `protocol/vN/protocol.json`: atomic signal questions + decision rules, calibrated against the review's own included set |
| 04 — screening on atomic signal questions | `panner_screen_guide / preview / confirm / continue / status`, `panner_screen_export` | `screening/vN/`: judgments, results, manifest |

Host-readable knowledge (workflow, data contracts, a complete worked protocol example) ships as resources: `panner://workflow`, `panner://contracts/*`, `panner://examples/*`. Hosts that do not expose resources to the agent can use the read-only `panner_get_contract` tool.

## Screening run configuration

Step 04 is driven by a versioned run config:

```json
{
  "backend": "calibrated",
  "provider": "openrouter",
  "model": "~typesafe/jev-latest",
  "endpoint": "https://openrouter.ai/api/alpha/decisions",
  "concurrency": 30,
  "batch_size": 250,
  "cost_cap": 300
}
```

- `backend: "calibrated"` — a decision model answers the frozen four-value questions; PANNER applies the frozen rules to its answers
- `judge_channel` records which judge actually answered; it defaults to `calibrated-decision-model`, and a run answered by anything else must say so, because the manifest, the screening preview and the exported report all carry the field
- `confidence_floor` (optional, 0–1) turns an answer whose reported confidence is below the floor into `unclear`; it has no effect on a judge that reports no confidence
- `batch_size` bounds the number of records in one MCP call; `cost_cap` bounds API request attempts in that batch, including retries
- `price_per_1m_input` and `price_per_1m_output` may be set for the preview estimate; omitted values use disclosed planning rates rather than live provider pricing
- a partial run keeps pending citations visible; call `panner_screen_continue` with the same plan hash to process the next batch
- the API key comes from `PANNER_DECISION_API_KEY`; it never lives in the workspace
- decision requests go only to the configured HTTPS endpoint; `openrouter.ai` is allowed by default, and other hosts require an explicit `PANNER_ALLOWED_DECISION_HOSTS` setting

## Running a local judge instead of a paid decisions API

A calibrated decisions model bills per input token and returns probabilities; a chat LLM bills input and output and returns a label, so it is usually the more expensive judge, not the cheaper one. It is still the only option when no decisions-model budget exists, and a locally hosted model costs nothing per call. `tools/local_decision_proxy.py` accepts PANNER's decisions request, asks an OpenAI-compatible chat endpoint the same questions in one JSON reply, validates the shape and returns the decisions response:

```sh
# a local model through Ollama (default), or any OpenAI-compatible endpoint:
PANNER_PROXY_BACKEND=ollama PANNER_PROXY_MODEL=qwen2.5:14b-instruct \
  python3 tools/local_decision_proxy.py        # listens on http://127.0.0.1:8765/decisions
```

Then point the run config at `http://127.0.0.1:8765/decisions`, set `"judge_channel": "local-llm"`, and allow the host in the MCP server environment (`PANNER_ALLOWED_DECISION_HOSTS=127.0.0.1,localhost`). `tools/stub_chat_server.py` scripts the same path offline for testing without a model.

Results are exported for humans with `panner_screen_export`: a colour-coded `screening-report.xlsx` (summary, per-citation decisions with rule-and-clause basis, per-answer signal detail) plus `screening-retained.ris` / `screening-excluded.ris` for reference managers. Pending and failed records travel with the retained handoff so no unscreened citation is lost.

## Safety posture

- the four-value semantics carry evidentiality: explicit evidence is required for hit/partial/miss; absence is always unclear
- **silence never excludes** — undecided answers and incomplete object enumerations keep the citation retained
- every exclusion is auditable end-to-end: status → rule → signal answers → the frozen protocol → the confirmed criteria
- results are recomputable: given the stored judgments, the shared evaluator reproduces every conclusion

## Known limitations (v2.0.0)

- deduplication false-merge/false-split rates are not yet measured on real corpora (BibDedupe results are held open for review whenever identifiers conflict)
- records whose core answers are all unclear receive a review-ordering flag; the frozen relevance facets do not yet call a backend or trigger exclusion
- group-level answers use the first-mention order of up to six explicitly enumerated groups; an unclear or larger group set remains undetermined for group-based exclusion
- the calibrated channel has no quotation to verify (the decisions API returns choices); evidentiality rests on the model's training, and the confidence the decisions API reports is stored but only acted on when `confidence_floor` is configured
- the step 03 calibration read covers the records the review itself included, so it measures false exclusions of the review's own set; a workspace without `labels/included.txt` is gated by structure, warnings and agent-written trial scenarios alone

## Documentation

The blueprint lives in `docs/` (Chinese, for human review): `00-positioning` through `04-screening`, organised in four layers — responsibility & logic, data contract, MCP tools, host instructions. The runtime English counterparts are the server resources.
