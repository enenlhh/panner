import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from panner_mcp.criteria import confirm_criteria, preview_criteria
from panner_mcp.protocol import freeze_compile, preview_compile
from panner_mcp.server import CITATION_GUIDANCE, COMPILE_GUIDANCE, GUIDANCE, ORCHESTRATION_GUIDANCE
from panner_mcp.workflow import workflow_status

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_compile import CRITERIA, CompileFlowTests  # reuse the working compile fixtures


def build_workspace(root: Path) -> tuple[str, str]:
    workspace = str(root / "review")
    preview = preview_criteria(workspace, CRITERIA, "用户提供并经对话确认", [], [])
    confirmed = confirm_criteria(workspace, CRITERIA, "用户提供并经对话确认", [], [], preview["plan_hash"], "全文准确")
    return workspace, confirmed["criteria_path"]


class WorkflowStatusTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)

    def tearDown(self):
        self._temporary.cleanup()

    def test_empty_workspace_starts_at_step_01(self):
        status = workflow_status(str(self.root / "review"))
        self.assertEqual(status["current_step"], "01")
        self.assertEqual(status["guidance_tool"], "panner_eligibility_guide")

    def test_confirmed_steps_advance_the_state_machine(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            workspace, criteria_path = build_workspace(source)
            # step 02 state comes from an active citation version; build one through the modules
            from panner_mcp.citations import confirm_citations, preview_citations

            stage = source / "stage"
            (stage / "sources").mkdir(parents=True)
            (stage / "sources/search.txt").write_text("one citation", encoding="utf-8")
            citation = {
                "id": "C000001", "source": {"file": "sources/search.txt", "position": 1, "id": ""},
                "title": "Example title", "abstract": "An abstract", "authors": "A. Author", "year": 2024,
                "journal": "J", "container_title": "", "volume": "", "issue": "", "pages": "",
                "publication_types": [], "doi": "", "pmid": "", "entry_type": "",
            }
            (stage / "citation.jsonl").write_text(json.dumps(citation) + "\n", encoding="utf-8")
            intake = preview_citations(str(stage / "citation.jsonl"), workspace, "direct", {"sources/search.txt": 1})
            confirmed_citations = confirm_citations(
                str(stage / "citation.jsonl"), workspace, "direct", {"sources/search.txt": 1}, intake["plan_hash"], "确认"
            )
            status = workflow_status(workspace)
            self.assertEqual(status["current_step"], "02")
            self.assertEqual(status["steps"]["02"]["status"], "pool_pending")
            from panner_mcp.dedup import deduplicate

            pool_dir = str(source / "pool")
            pool_plan = deduplicate(confirmed_citations["citation_path"], pool_dir)
            deduplicate(confirmed_citations["citation_path"], pool_dir, confirm=pool_plan["plan_hash"])
            status = workflow_status(workspace)
            self.assertEqual(status["current_step"], "03")
            self.assertEqual(status["steps"]["02"]["citations"], 1)

            flow = CompileFlowTests(methodName="test_valid_draft_previews_ready_and_freezes_after_confirmation")
            draft_document = flow.valid_draft()
            draft_file = source / "protocol-draft.json"
            draft_file.write_text(json.dumps(draft_document, ensure_ascii=False), encoding="utf-8")
            preview = preview_compile(workspace, criteria_path, str(draft_file))
            freeze_compile(workspace, criteria_path, str(draft_file), preview["plan_hash"], "确认")
            status = workflow_status(workspace)
            self.assertEqual(status["current_step"], "04")
            self.assertEqual(status["steps"]["04"]["status"], "not_available")
            self.assertIn("panner_screen_preview", status["next_action"])

    def test_tampered_criteria_surface_as_repairable_integrity_error(self):
        workspace, criteria_path = build_workspace(self.root)
        criteria_file = Path(criteria_path)
        criteria_file.write_text("tampered", encoding="utf-8")
        status = workflow_status(workspace)
        self.assertEqual(status["current_step"], "01")
        self.assertEqual(status["steps"]["01"]["status"], "integrity_error")
        self.assertIn("repair the step 01 version", status["next_action"])


class GuidanceContentTests(unittest.TestCase):
    def test_step_03_guidance_carries_the_ruled_semantics(self):
        required = [
            "Implicature is a positive obligation",
            '"include RCTs" does not by itself exclude conference abstracts',
            "seven dimensions: unit of judgment, which object carries the restriction (the record, the comparison, or a single group), quantifier, comparison relation and direction, regimen composition, scope of a restriction",
            "belongs in the answer criteria of the signal that reads that object",
            "Never implement an object-level restriction as a per-group veto inside a comparison rule",
            "never let a hit follow from an inference",
            "call panner_compile_calibration",
            "Freezing is refused while any labeled included record is excluded",
            "absence is not miss",
            "partial means the citation explicitly shows both qualifying and non-qualifying parts",
            "{signal, on, answer}",
            "expect is only hit or miss",
            "undecided never excludes",
            "the enumeration of objects is complete",
            "A core mark never grants a single question the power to exclude",
            "relevance.facets",
            "Write all protocol content in English",
            "between miss and unclear, and between partial and unclear",
            "an eligibility clause must have at least one signal, one rule, one handoff, or one explicit no-restriction decision in decisions",
        ]
        for phrase in required:
            self.assertIn(phrase, COMPILE_GUIDANCE, phrase)

    def test_all_guidance_is_english_only(self):
        import re

        cjk = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]")
        for name, guidance in (
            ("01", GUIDANCE), ("02", CITATION_GUIDANCE), ("03", COMPILE_GUIDANCE), ("orchestration", ORCHESTRATION_GUIDANCE)
        ):
            self.assertFalse(cjk.search(guidance), name)


if __name__ == "__main__":
    unittest.main()
