import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from panner_mcp.calibration import calibrate_compile
from panner_mcp.citations import confirm_citations, preview_citations
from panner_mcp.criteria import confirm_criteria, preview_criteria
from panner_mcp.dedup import deduplicate
from panner_mcp.protocol import freeze_compile, preview_compile

CRITERIA = (
    "# Review objective\n评估未获批药物在成人中的效果。\n\n"
    "# Eligibility criteria\n## Inclusion\n"
    "- 仅纳入 18 岁及以上成年人\n"
    "- 研究须比较目标干预与对照干预\n\n"
    "## Exclusion\n"
    "- 排除未获批准的药物\n"
)


def signal(identifier, unit, question, quote, core=False):
    return {
        "id": identifier,
        "unit": unit,
        "question": question,
        "core": core,
        "evaluation_mode": "semantic",
        "evidence_fields": ["title", "abstract"],
        "answer_criteria": {
            "hit": "The citation explicitly states this fact.",
            "partial": "The citation explicitly states this for part of the objects.",
            "miss": "The citation explicitly states the contrary fact.",
            "unclear": "The citation does not state it.",
        },
        "criterion_refs": [{"criterion_id": quote[0], "quote": quote[1]}],
    }


def draft_document() -> dict:
    return {
        "format": "panner-protocol-v1",
        "signals": [
            signal("S001", "population", "Is the study population adults?", ("E001", "仅纳入 18 岁及以上成年人"), core=True),
            signal("S002", "study", "Does the study evaluate an unapproved therapy?", ("E003", "排除未获批准的药物")),
            signal("S003", "arm", "Does this group receive the target intervention?", ("E002", "研究须比较目标干预与对照干预")),
            signal("S004", "arm", "Does this group receive the eligible comparator?", ("E002", "研究须比较目标干预与对照干预")),
        ],
        "decision_rules": [
            {
                "id": "R001",
                "kind": "disqualifier",
                "unit": "record",
                "quantifier": "all",
                "condition": {"signal": "S002", "on": "record", "expect": "hit"},
                "criterion_refs": [{"criterion_id": "E003", "quote": "排除未获批准的药物"}],
            },
            {
                "id": "R003",
                "kind": "disqualifier",
                "unit": "record",
                "quantifier": "all",
                "condition": {"signal": "S001", "on": "record", "expect": "miss"},
                "criterion_refs": [{"criterion_id": "E001", "quote": "仅纳入 18 岁及以上成年人"}],
            },
            {
                "id": "R002",
                "kind": "qualification",
                "unit": "comparison",
                "quantifier": "exists",
                "condition": {
                    "any": [
                        {"all": [{"signal": "S003", "on": "member0", "expect": "hit"}, {"signal": "S004", "on": "member1", "expect": "hit"}]},
                        {"all": [{"signal": "S003", "on": "member1", "expect": "hit"}, {"signal": "S004", "on": "member0", "expect": "hit"}]},
                    ]
                },
                "criterion_refs": [{"criterion_id": "E002", "quote": "研究须比较目标干预与对照干预"}],
            },
        ],
        "criteria_map": [
            {"id": "E001", "quote": "仅纳入 18 岁及以上成年人", "occurrence": 1, "role": "eligibility", "target": "population",
             "signal_ids": ["S001"], "rule_ids": ["R003"], "handoffs": []},
            {"id": "E002", "quote": "研究须比较目标干预与对照干预", "occurrence": 1, "role": "eligibility", "target": "comparison",
             "signal_ids": ["S003", "S004"], "rule_ids": ["R002"], "handoffs": []},
            {"id": "E003", "quote": "排除未获批准的药物", "occurrence": 1, "role": "eligibility", "target": "study",
             "signal_ids": ["S002"], "rule_ids": ["R001"], "handoffs": []},
        ],
        "clarifications": [],
        "relevance": {
            "enabled": True,
            "facets": [{"id": "F001", "aspect": "population", "question": "Does the citation concern adults?"}],
            "note": "",
        },
        "screening_review": [],
    }


def citation(number: int, title: str) -> dict:
    return {
        "id": f"C{number:06d}", "source": {"file": "sources/s.txt", "position": number, "id": ""},
        "title": title, "abstract": "Adults were randomised.", "authors": "A; B", "year": 2020,
        "journal": "J", "container_title": "", "volume": "", "issue": "", "pages": "",
        "publication_types": [], "doi": f"10.1000/{number}", "pmid": str(number), "entry_type": "article",
    }


def run_config(root: Path, **overrides) -> str:
    payload = {
        "backend": "calibrated", "provider": "openrouter", "model": "test-model",
        "endpoint": "https://openrouter.ai/api/alpha/decisions", "concurrency": 2, "cost_cap": 20,
    }
    payload.update(overrides)
    path = root / "run-config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


class LintTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.workspace = str(self.root / "review")
        preview = preview_criteria(self.workspace, CRITERIA, "原文", [], [])
        self.criteria = confirm_criteria(self.workspace, CRITERIA, "原文", [], [], preview["plan_hash"], "确认")["criteria_path"]

    def tearDown(self):
        self._temporary.cleanup()

    def preview(self, document: dict) -> dict:
        path = self.root / "draft.json"
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        return preview_compile(self.workspace, self.criteria, str(path))

    def test_clean_comparison_rule_has_no_warning(self):
        self.assertEqual(self.preview(draft_document())["warnings"], [])

    def test_group_veto_inside_comparison_is_warned(self):
        document = draft_document()
        comparison_rule = next(rule for rule in document["decision_rules"] if rule["id"] == "R002")
        comparison_rule["condition"]["any"][0]["all"].append(
            {"not": {"signal": "S003", "on": "member0", "expect": "hit"}}
        )
        warnings = self.preview(document)["warnings"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("R002", warnings[0])
        self.assertIn("S003", warnings[0])

    def test_arms_any_disqualifier_is_warned(self):
        document = draft_document()
        document["decision_rules"].append({
            "id": "R004",
            "kind": "disqualifier",
            "unit": "arms",
            "quantifier": "any",
            "condition": {"signal": "S003", "on": "each", "expect": "miss"},
            "criterion_refs": [{"criterion_id": "E002", "quote": "研究须比较目标干预与对照干预"}],
        })
        warnings = self.preview(document)["warnings"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("arms/any", warnings[0])

    def test_unrouted_signal_is_warned(self):
        document = draft_document()
        document["decision_rules"] = [rule for rule in document["decision_rules"] if rule["id"] != "R003"]
        warnings = self.preview(document)["warnings"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("S001", warnings[0])
        self.assertIn("no decision rule", warnings[0])


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self._previous_key = os.environ.get("PANNER_LLM_API_KEY")
        os.environ["PANNER_LLM_API_KEY"] = "test-only-placeholder-key"
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.workspace = str(self.root / "review")
        self.workspace_path = Path(self.workspace)

        preview = preview_criteria(self.workspace, CRITERIA, "原文", [], [])
        self.criteria = confirm_criteria(self.workspace, CRITERIA, "原文", [], [], preview["plan_hash"], "确认")["criteria_path"]

        stage = self.root / "stage"
        (stage / "sources").mkdir(parents=True)
        (stage / "sources/s.txt").write_text("x", encoding="utf-8")
        rows = [citation(1, "Approved therapy trial"), citation(2, "Unapproved therapy trial"), citation(3, "Other trial")]
        (stage / "citation.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        saved = confirm_citations(
            str(stage / "citation.jsonl"), self.workspace, "direct", {"sources/s.txt": 3},
            preview_citations(str(stage / "citation.jsonl"), self.workspace, "direct", {"sources/s.txt": 3})["plan_hash"],
            "确认",
        )
        dedup_plan = deduplicate(saved["citation_path"], str(self.root / "pool"))
        deduplicate(saved["citation_path"], str(self.root / "pool"), confirm=dedup_plan["plan_hash"])

        self.draft = self.root / "draft.json"
        self.draft.write_text(json.dumps(draft_document(), ensure_ascii=False), encoding="utf-8")
        self.config = run_config(self.root)
        self.labels = self.workspace_path / "labels"
        self.labels.mkdir()
        (self.labels / "included.txt").write_text("pmid:1\npmid:2\n", encoding="utf-8")

    def tearDown(self):
        self._temporary.cleanup()
        if self._previous_key is None:
            os.environ.pop("PANNER_LLM_API_KEY", None)
        else:
            os.environ["PANNER_LLM_API_KEY"] = self._previous_key

    def scripted(self, unapproved: set[str]):
        """Answers as the calibrated channel returns them: one dict per citation."""

        def fake(config, signals, citation, api_key, reserve_call=None):
            answers = [
                {"signal": "S001", "on": "record", "answer": "hit"},
                {"signal": "S002", "on": "record", "answer": "hit" if citation["id"] in unapproved else "miss"},
                {"signal": "S003", "on": "group_1", "answer": "hit"},
                {"signal": "S004", "on": "group_2", "answer": "hit"},
            ]
            return {
                "entities": {"arms": ["group_1", "group_2"]},
                "answers": answers,
                "flags": [],
                "usage": {"cost_usd": 0.002, "input_tokens": 100, "output_tokens": 10},
            }

        return fake

    def test_calibration_reports_labels_and_exclusions(self):
        with patch("panner_mcp.calibration._judge_citation_calibrated", side_effect=self.scripted({"C000002"})):
            report = calibrate_compile(self.workspace, self.criteria, str(self.draft), self.config)
        self.assertEqual(report["labels"], 2)
        self.assertEqual(report["mapped"], 2)
        self.assertEqual(report["excluded"], 1)
        self.assertEqual(report["retained"], 1)
        self.assertEqual(report["calls"], 2)
        self.assertEqual(report["excluded_labels"][0]["citation_id"], "C000002")
        self.assertEqual(report["excluded_labels"][0]["rules"], ["R001"])
        self.assertTrue(Path(report["report_path"]).is_file())
        self.assertEqual(report["unmapped"], [])

    def test_calibration_sample_reports_exclusion_rate(self):
        config = run_config(self.root, sample_size=2)
        with patch("panner_mcp.calibration._judge_citation_calibrated", side_effect=self.scripted(set())):
            report = calibrate_compile(self.workspace, self.criteria, str(self.draft), config)
        self.assertIsNotNone(report["sample"])
        self.assertEqual(report["sample"]["sample_size"], 2)
        self.assertEqual(report["sample"]["excluded"] + report["sample"]["retained"], 2)
        self.assertEqual(report["sample"]["estimated_exclusion_rate"],
                         round(report["sample"]["excluded"] / 2, 4))
        # the labeled path is unchanged
        self.assertEqual(report["labels"], 2)
        self.assertEqual(report["calls"], 2)

    def test_freeze_refuses_while_a_labeled_record_is_excluded(self):
        plan = preview_compile(self.workspace, self.criteria, str(self.draft))
        with patch("panner_mcp.calibration._judge_citation_calibrated", side_effect=self.scripted({"C000002"})):
            calibrate_compile(self.workspace, self.criteria, str(self.draft), self.config)
        with self.assertRaises(ValueError) as caught:
            freeze_compile(self.workspace, self.criteria, str(self.draft), plan["plan_hash"], "确认")
        self.assertIn("pmid:2", str(caught.exception))
        self.assertIn("R001", str(caught.exception))

    def test_freeze_requires_a_report_when_labels_exist(self):
        plan = preview_compile(self.workspace, self.criteria, str(self.draft))
        with self.assertRaises(ValueError) as caught:
            freeze_compile(self.workspace, self.criteria, str(self.draft), plan["plan_hash"], "确认")
        self.assertIn("panner_compile_calibration", str(caught.exception))

    def test_freeze_succeeds_after_a_clean_calibration(self):
        plan = preview_compile(self.workspace, self.criteria, str(self.draft))
        with patch("panner_mcp.calibration._judge_citation_calibrated", side_effect=self.scripted(set())):
            report = calibrate_compile(self.workspace, self.criteria, str(self.draft), self.config)
        self.assertEqual(report["excluded"], 0)
        frozen = freeze_compile(self.workspace, self.criteria, str(self.draft), plan["plan_hash"], "确认")
        self.assertEqual(frozen["status"], "confirmed")

    def test_unmapped_label_is_reported_without_bending_the_protocol(self):
        (self.labels / "included.txt").write_text("pmid:1\npmid:404\n", encoding="utf-8")
        with patch("panner_mcp.calibration._judge_citation_calibrated", side_effect=self.scripted(set())):
            report = calibrate_compile(self.workspace, self.criteria, str(self.draft), self.config)
        self.assertEqual(report["unmapped"], ["pmid:404"])
        self.assertEqual(report["mapped"], 1)


if __name__ == "__main__":
    unittest.main()
