import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp import Client

from panner_mcp.protocol import freeze_compile, preview_compile
from panner_mcp.resources import RESOURCES, example_protocol_draft, read_resource
from panner_mcp.server import mcp
from test_workflow import build_workspace


class ResourceContentTests(unittest.TestCase):
    def test_every_resource_is_nonempty_english_markdown(self):
        import re

        cjk = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
        for uri, (name, reader) in RESOURCES.items():
            text = reader()
            self.assertTrue(text.strip(), uri)
            self.assertIn("# ", text, uri)
            # The example quotes user criteria verbatim, so it may contain CJK quotes.
            if not uri.startswith("panner://examples"):
                self.assertFalse(cjk.search(text), uri)

    def test_unknown_resource_uri_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown PANNER resource"):
            read_resource("panner://nope")

    def test_example_draft_passes_real_validation(self):
        # The example resource must stay a verified sample: run it through the
        # same preview the host would face, against the criteria excerpt it quotes.
        draft = example_protocol_draft()
        criteria_excerpt = (
            "# Review objective\n比较目标干预与对照干预在成人中的效果。\n\n# Eligibility criteria\n## Inclusion\n"
            "- 仅纳入 18 岁及以上成年人\n- 研究须比较目标干预与对照干预\n## Exclusion\n"
            "- 排除病例报告\n- 不允许的干预措施 D 不得出现在符合资格的比较的任一组中\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            from panner_mcp.criteria import confirm_criteria, preview_criteria

            ws = str(root / "review")
            preview = preview_criteria(ws, criteria_excerpt, "用户提供并经对话确认", [], [])
            confirmed = confirm_criteria(ws, criteria_excerpt, "用户提供并经对话确认", [], [], preview["plan_hash"], "全文准确")
            workspace, criteria_path = ws, confirmed["criteria_path"]
            draft_file = root / "example-draft.json"
            draft_file.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
            preview = preview_compile(workspace, criteria_path, str(draft_file))
            self.assertEqual(preview["status"], "ready_for_confirmation", preview["issues"])
            self.assertEqual(preview["trial_mismatches"], [])


class MCPResourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_host_discovers_and_reads_knowledge_resources(self):
        async with Client(mcp, raise_exceptions=True) as client:
            listed = await client.list_resources()
            uris = {str(resource.uri) for resource in listed.resources}
            self.assertEqual(uris, set(RESOURCES))
            contents = (await client.read_resource("panner://workflow")).contents[0]
            self.assertIn("panner_workflow_status", contents.text)
            example = (await client.read_resource("panner://examples/protocol-draft")).contents[0]
            json.loads(example.text.rsplit("```json\n", 1)[1].split("\n```", 1)[0])
            with self.assertRaises(Exception):
                await client.read_resource("panner://nope")

    async def test_server_instructions_name_the_entry_point(self):
        # The initialize result carries instructions; the in-memory client exposes them.
        server_info = getattr(mcp, "instructions", None)
        self.assertIsNotNone(server_info)
        self.assertIn("panner_workflow_status", server_info)


if __name__ == "__main__":
    unittest.main()
