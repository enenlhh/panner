import tempfile
import unittest
import json
from pathlib import Path

from mcp import Client

from panner_mcp.server import mcp


CRITERIA = "# Review objective\n研究目标。\n\n# Eligibility criteria\n- 目标人群。\n"
COMPILE_CRITERIA = (
    "# Review objective\n研究目标。\n\n# Eligibility criteria\n- 仅纳入 18 岁及以上成年人\n"
)


def minimal_draft() -> dict:
    return {
        "format": "panner-protocol-v1",
        "signals": [
            {
                "id": "S001",
                "unit": "population",
                "question": "Is the study population adults aged 18 years or older?",
                "core": False,
                "evaluation_mode": "semantic",
                "evidence_fields": ["title", "abstract"],
                "answer_criteria": {
                    "hit": "All participants are explicitly adults.",
                    "partial": "The citation explicitly describes both adult and minor participants.",
                    "miss": "Every participant is explicitly younger than 18 years old.",
                    "unclear": "Age is not stated.",
                },
                "criterion_refs": [{"criterion_id": "E001", "quote": "仅纳入 18 岁及以上成年人"}],
            }
        ],
        "decision_rules": [
            {
                "id": "R001",
                "kind": "disqualifier",
                "unit": "record",
                "quantifier": "all",
                "condition": {"signal": "S001", "on": "record", "expect": "miss"},
                "criterion_refs": [{"criterion_id": "E001", "quote": "仅纳入 18 岁及以上成年人"}],
            }
        ],
        "criteria_map": [
            {
                "id": "E001",
                "quote": "仅纳入 18 岁及以上成年人",
                "occurrence": 1,
                "role": "eligibility",
                "target": "population",
                "signal_ids": ["S001"],
                "rule_ids": ["R001"],
                "handoffs": [],
            }
        ],
        "clarifications": [],
        "relevance": {
            "enabled": False,
            "facets": [],
            "note": "This review has no safely compilable core topic questions; records are retained and routed to review.",
        },
        "screening_review": [],
    }


class MCPMappingTests(unittest.IsolatedAsyncioTestCase):
    async def test_host_discovers_and_calls_step_01(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = str(Path(temporary) / "review")
            async with Client(mcp, raise_exceptions=True) as client:
                tools = await client.list_tools()
                names = {tool.name for tool in tools.tools}
                self.assertTrue(
                    {"panner_eligibility_guide", "panner_eligibility_preview", "panner_eligibility_confirm", "panner_eligibility_status"}
                    <= names
                )
                preview = await client.call_tool(
                    "panner_eligibility_preview",
                    {
                        "workspace_path": workspace,
                        "markdown": CRITERIA,
                        "origin_note": "用户在对话中确定",
                        "source_paths": [],
                        "unresolved_questions": ["多组比较的作用范围待编译时确认"],
                    },
                )
                plan = preview.structured_content
                self.assertEqual(plan["status"], "ready_for_confirmation")
                self.assertEqual(plan["full_text_to_show_user"], CRITERIA)
                confirmation = await client.call_tool(
                    "panner_eligibility_confirm",
                    {
                        "workspace_path": workspace,
                        "markdown": CRITERIA,
                        "origin_note": "用户在对话中确定",
                        "source_paths": [],
                        "unresolved_questions": ["多组比较的作用范围待编译时确认"],
                        "plan_hash": plan["plan_hash"],
                        "user_confirmation": "用户确认了展示的全文",
                    },
                )
                self.assertEqual(confirmation.structured_content["status"], "confirmed")
                self.assertTrue(Path(confirmation.structured_content["criteria_path"]).is_file())
                status = await client.call_tool("panner_eligibility_status", {"workspace_path": workspace})
                self.assertEqual(
                    status.structured_content["unresolved_questions"], ["多组比较的作用范围待编译时确认"]
                )

    async def test_host_discovers_and_calls_step_02(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stage = root / "stage"
            (stage / "sources").mkdir(parents=True)
            (stage / "sources/search.txt").write_text("one citation", encoding="utf-8")
            citation = {
                "id": "C000001", "source": {"file": "sources/search.txt", "position": 1, "id": ""},
                "title": "Example title", "abstract": "", "authors": "A. Author", "year": 2024,
                "journal": "Example Journal", "container_title": "", "volume": "", "issue": "", "pages": "",
                "publication_types": [], "doi": "", "pmid": "", "entry_type": "",
            }
            citation_file = stage / "citation.jsonl"
            citation_file.write_text(json.dumps(citation) + "\n", encoding="utf-8")
            workspace = str(root / "review")
            async with Client(mcp, raise_exceptions=True) as client:
                names = {tool.name for tool in (await client.list_tools()).tools}
                self.assertTrue({"panner_citation_guide", "panner_citation_preview", "panner_citation_confirm", "panner_deduplicate_preview", "panner_deduplicate_confirm", "panner_deduplicate_status"} <= names)
                guide = await client.call_tool("panner_citation_guide", {})
                self.assertEqual(guide.structured_content["step"], "02")
                preview = await client.call_tool(
                    "panner_citation_preview",
                    {"input_path": str(citation_file), "workspace_path": workspace, "mapping_note": "direct mapping",
                     "source_counts": {"sources/search.txt": 1}},
                )
                self.assertEqual(preview.structured_content["status"], "ready_for_confirmation")
                confirmed = await client.call_tool(
                    "panner_citation_confirm",
                    {"input_path": str(citation_file), "workspace_path": workspace, "mapping_note": "direct mapping",
                     "source_counts": {"sources/search.txt": 1}, "plan_hash": preview.structured_content["plan_hash"],
                     "user_confirmation": "确认来源和字段映射"},
                )
                output = str(root / "pool")
                dedup = await client.call_tool(
                    "panner_deduplicate_preview", {"input_path": confirmed.structured_content["citation_path"], "output_dir": output}
                )
                self.assertEqual(dedup.structured_content["counts"]["to_screen"], 1)
                saved_pool = await client.call_tool(
                    "panner_deduplicate_confirm",
                    {"input_path": confirmed.structured_content["citation_path"], "output_dir": output,
                     "plan_hash": dedup.structured_content["plan_hash"], "user_confirmation": "确认去重计划"},
                )
                pool_status = await client.call_tool("panner_deduplicate_status", {"workspace_path": workspace})
                self.assertEqual(pool_status.structured_content["pool_path"], saved_pool.structured_content["citation_pool_path"])


    async def test_host_discovers_and_calls_step_03(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = str(root / "review")
            draft_file = root / "protocol-draft.json"
            draft_file.write_text(json.dumps(minimal_draft(), ensure_ascii=False), encoding="utf-8")
            async with Client(mcp, raise_exceptions=True) as client:
                tools = await client.list_tools()
                names = {tool.name for tool in tools.tools}
                self.assertTrue(
                    {"panner_compile_guide", "panner_compile_preview", "panner_compile_calibration",
                     "panner_compile_freeze", "panner_compile_status"}
                    <= names
                )
                self.assertEqual(len(tools.tools), 24)
                prompts = await client.list_prompts()
                self.assertEqual(
                    {prompt.name for prompt in prompts.prompts},
                    {
                        "establish_eligibility_criteria",
                        "build_citation_pool",
                        "compile_screening_protocol",
                        "start_or_resume_screening",
                    },
                )
                confirmed = await client.call_tool(
                    "panner_eligibility_confirm",
                    {
                        "workspace_path": workspace,
                        "markdown": COMPILE_CRITERIA,
                        "origin_note": "用户提供并经对话确认",
                        "source_paths": [],
                        "unresolved_questions": [],
                        "plan_hash": (
                            await client.call_tool(
                                "panner_eligibility_preview",
                                {
                                    "workspace_path": workspace,
                                    "markdown": COMPILE_CRITERIA,
                                    "origin_note": "用户提供并经对话确认",
                                    "source_paths": [],
                                    "unresolved_questions": [],
                                },
                            )
                        ).structured_content["plan_hash"],
                        "user_confirmation": "全文准确",
                    },
                )
                criteria_path = confirmed.structured_content["criteria_path"]
                preview = await client.call_tool(
                    "panner_compile_preview",
                    {"workspace_path": workspace, "criteria_path": criteria_path, "draft_path": str(draft_file)},
                )
                self.assertEqual(preview.structured_content["status"], "ready_for_confirmation", preview.structured_content["issues"])
                frozen = await client.call_tool(
                    "panner_compile_freeze",
                    {
                        "workspace_path": workspace,
                        "criteria_path": criteria_path,
                        "draft_path": str(draft_file),
                        "plan_hash": preview.structured_content["plan_hash"],
                        "user_confirmation": "协议准确表达了标准",
                    },
                )
                self.assertEqual(frozen.structured_content["status"], "confirmed")
                status = await client.call_tool("panner_compile_status", {"workspace_path": workspace})
                self.assertEqual(status.structured_content["version"], "v0001")

    async def test_screen_guide_and_contract_are_available_as_tools(self):
        async with Client(mcp, raise_exceptions=True) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            self.assertTrue(tools["panner_screen_guide"].annotations.read_only_hint)
            self.assertTrue(tools["panner_get_contract"].annotations.read_only_hint)
            guide = await client.call_tool("panner_screen_guide", {})
            self.assertEqual(guide.structured_content["step"], "04")
            contract = await client.call_tool("panner_get_contract", {"name": "protocol"})
            self.assertEqual(contract.structured_content["uri"], "panner://contracts/protocol")
            self.assertIn("decision_rules", contract.structured_content["content"])


if __name__ == "__main__":
    unittest.main()
