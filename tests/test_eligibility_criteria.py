import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from panner_mcp.criteria import confirm_criteria, criteria_status, preview_criteria


CRITERIA = "# Review objective\n研究目标。\n\n# Eligibility criteria\n- 纳入符合目标人群的研究。\n"


class EligibilityCriteriaTests(unittest.TestCase):
    def test_empty_criteria_section_is_not_confirmable(self):
        with tempfile.TemporaryDirectory() as temporary:
            markdown = "# Review objective\n研究目标。\n\n# Eligibility criteria\n## Inclusion\n\n# Open questions\n具体标准待定。\n"
            preview = preview_criteria(str(Path(temporary) / "review"), markdown, "对话", [], [])
            self.assertEqual(preview["status"], "draft")
            self.assertIn("Eligibility criteria are missing or empty", preview["issues"])

    def test_known_compile_questions_are_preserved_with_confirmed_initial_criteria(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = str(Path(temporary) / "review")
            preview = preview_criteria(workspace, CRITERIA, "用户在对话中确定", [], ["混合样本如何处理？"])
            self.assertEqual(preview["status"], "ready_for_confirmation")
            result = confirm_criteria(
                workspace, CRITERIA, "用户在对话中确定", [], ["混合样本如何处理？"], preview["plan_hash"], "准确记录了当前标准及待澄清项"
            )
            self.assertEqual(result["status"], "confirmed")
            self.assertEqual(criteria_status(workspace)["unresolved_questions"], ["混合样本如何处理？"])

    def test_confirmed_version_preserves_sources_and_rejects_stale_preview(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = str(root / "review")
            source = root / "original.txt"
            source_bytes = b"Original criteria\r\n"
            source.write_bytes(source_bytes)
            note = "用户提供的原文及对话确认"
            preview = preview_criteria(workspace, CRITERIA, note, [str(source)], [])
            self.assertEqual(preview["status"], "ready_for_confirmation")
            self.assertFalse(Path(workspace).exists())

            first = confirm_criteria(workspace, CRITERIA, note, [str(source)], [], preview["plan_hash"], "全文正确")
            self.assertEqual(first["version"], "v0001")
            self.assertEqual(Path(first["criteria_path"]).read_text(encoding="utf-8"), CRITERIA)
            copy = Path(workspace) / "criteria/v0001/sources/001-original.txt"
            self.assertEqual(copy.read_bytes(), source_bytes)
            self.assertEqual(criteria_status(workspace)["criteria_sha256"], hashlib.sha256(CRITERIA.encode()).hexdigest())

            revised = CRITERIA + "- 接受混合人群中可分离的数据。\n"
            second_preview = preview_criteria(workspace, revised, note, [str(source)], [])
            with self.assertRaisesRegex(ValueError, "preview changed"):
                confirm_criteria(workspace, revised, note, [str(source)], [], preview["plan_hash"], "同意")
            second = confirm_criteria(workspace, revised, note, [str(source)], [], second_preview["plan_hash"], "修改后的全文正确")
            self.assertEqual(second["version"], "v0002")
            self.assertEqual(Path(first["criteria_path"]).read_text(encoding="utf-8"), CRITERIA)

    def test_changed_source_and_unmarked_directory_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = str(root / "review")
            source = root / "source.md"
            source.write_text("first", encoding="utf-8")
            preview = preview_criteria(workspace, CRITERIA, "原文", [str(source)], [])
            source.write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "preview changed"):
                confirm_criteria(workspace, CRITERIA, "原文", [str(source)], [], preview["plan_hash"], "同意")

            non_workspace = root / "existing"
            non_workspace.mkdir()
            (non_workspace / "other-data.txt").write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "new, empty"):
                preview_criteria(str(non_workspace), CRITERIA, "原文", [], [])
            self.assertEqual((non_workspace / "other-data.txt").read_text(encoding="utf-8"), "keep")

    def test_workspace_marker_cannot_redirect_active_criteria_outside_workspace(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "review"
            workspace.mkdir()
            (workspace / "panner-workspace.json").write_text(
                json.dumps({"format": "panner-workspace-v1", "active_eligibility_criteria": "../other.md"}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "invalid criteria path"):
                criteria_status(str(workspace))


if __name__ == "__main__":
    unittest.main()
