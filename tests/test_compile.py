import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from panner_mcp.criteria import confirm_criteria
from panner_mcp.evaluate import evaluate_rules
from panner_mcp.protocol import freeze_compile, preview_compile, protocol_status


CRITERIA = (
    "# Review objective\n比较目标干预与对照干预在成人中的效果。\n\n"
    "# Eligibility criteria\n## Inclusion\n"
    "- 仅纳入 18 岁及以上成年人\n"
    "- 研究须比较目标干预与对照干预\n\n"
    "## Exclusion\n"
    "- 排除病例报告\n"
    "- 不允许的干预措施 D 不得出现在符合资格的比较的任一组中\n"
)

POPULATION_SIGNAL = {
    "id": "S001",
    "unit": "population",
    "question": "Is the study population adults aged 18 years or older?",
    "core": True,
    "evaluation_mode": "semantic",
    "evidence_fields": ["title", "abstract"],
    "answer_criteria": {
        "hit": "The citation explicitly states that all participants are at least 18 years old.",
        "partial": "The citation explicitly describes both adult and minor participants.",
        "miss": "The citation explicitly states that every participant is younger than 18 years old.",
        "unclear": "Age is not stated or cannot be determined.",
    },
    "criterion_refs": [{"criterion_id": "E001", "quote": "仅纳入 18 岁及以上成年人"}],
}

CASE_REPORT_SIGNAL = {
    "id": "S002",
    "unit": "citation_text",
    "question": "Is this publication a case report?",
    "core": False,
    "evaluation_mode": "field_rule",
    "evidence_fields": ["publication_types"],
    "field_rule": {"field": "publication_types", "matches": [{"value": "Case Reports", "answer": "hit"}]},
    "answer_criteria": {
        "hit": "publication_types lists a case-report type.",
        "partial": "publication_types lists mixed types, one of which is a case report.",
        "miss": "publication_types is present and names no case-report type.",
        "unclear": "publication_types is empty or absent.",
    },
    "criterion_refs": [{"criterion_id": "E003", "quote": "排除病例报告"}],
}


def arm_signal(identifier: str, question: str, quote: str) -> dict:
    return {
        "id": identifier,
        "unit": "arm",
        "question": question,
        "core": False,
        "evaluation_mode": "semantic",
        "evidence_fields": ["title", "abstract"],
        "answer_criteria": {
            "hit": "The citation explicitly states this about the group.",
            "partial": "The citation explicitly states this for only part of the group or treatment period.",
            "miss": "The citation explicitly states the group's regimen and it contradicts the question.",
            "unclear": "The group's regimen is absent, incomplete or conflicting.",
        },
        "criterion_refs": [{"criterion_id": "E004", "quote": quote}],
    }


INTERVENTION_SIGNAL = arm_signal("S003", "Does this study group receive the target intervention?", "不允许的干预措施 D 不得出现在符合资格的比较的任一组中")
CONTROL_SIGNAL = arm_signal("S004", "Does this study group receive the eligible comparator?", "不允许的干预措施 D 不得出现在符合资格的比较的任一组中")
FORBIDDEN_SIGNAL = arm_signal("S005", "Does this study group include the disallowed measure D?", "不允许的干预措施 D 不得出现在符合资格的比较的任一组中")

CASE_REPORT_RULE = {
    "id": "R001",
    "kind": "disqualifier",
    "unit": "record",
    "quantifier": "all",
    "condition": {"signal": "S002", "on": "record", "expect": "hit"},
    "criterion_refs": [{"criterion_id": "E003", "quote": "排除病例报告"}],
}

COMPARISON_RULE = {
    "id": "R002",
    "kind": "qualification",
    "unit": "comparison",
    "quantifier": "exists",
    "condition": {
        "all": [
            {"any": [{"signal": "S003", "on": "member0", "expect": "hit"}, {"signal": "S003", "on": "member1", "expect": "hit"}]},
            {"any": [{"signal": "S004", "on": "member0", "expect": "hit"}, {"signal": "S004", "on": "member1", "expect": "hit"}]},
            {"not": {"signal": "S005", "on": "member0", "expect": "hit"}},
            {"not": {"signal": "S005", "on": "member1", "expect": "hit"}},
        ]
    },
    "criterion_refs": [
        {"criterion_id": "E002", "quote": "研究须比较目标干预与对照干预"},
        {"criterion_id": "E004", "quote": "不允许的干预措施 D 不得出现在符合资格的比较的任一组中"},
    ],
}


def draft_document(trials: list[dict]) -> dict:
    document = {
        "format": "panner-protocol-v1",
        "signals": [POPULATION_SIGNAL, CASE_REPORT_SIGNAL, INTERVENTION_SIGNAL, CONTROL_SIGNAL, FORBIDDEN_SIGNAL],
        "decision_rules": [CASE_REPORT_RULE, COMPARISON_RULE],
        "criteria_map": [
            {
                "id": "E001",
                "quote": "仅纳入 18 岁及以上成年人",
                "occurrence": 1,
                "role": "eligibility",
                "target": "population",
                "signal_ids": ["S001"],
                "rule_ids": [],
                "handoffs": [],
            },
            {
                "id": "E002",
                "quote": "研究须比较目标干预与对照干预",
                "occurrence": 1,
                "role": "eligibility",
                "target": "comparison",
                "signal_ids": ["S003", "S004"],
                "rule_ids": ["R002"],
                "handoffs": [],
            },
            {
                "id": "E003",
                "quote": "排除病例报告",
                "occurrence": 1,
                "role": "eligibility",
                "target": "citation_text",
                "signal_ids": ["S002"],
                "rule_ids": ["R001"],
                "handoffs": [],
            },
            {
                "id": "E004",
                "quote": "不允许的干预措施 D 不得出现在符合资格的比较的任一组中",
                "occurrence": 1,
                "role": "eligibility",
                "target": "comparison",
                "signal_ids": ["S005"],
                "rule_ids": ["R002"],
                "handoffs": [{"stage": "full_text", "task": "check whether the combination changes the group's eligibility"}],
            },
        ],
        "clarifications": [],
        "relevance": {
            "enabled": True,
            "facets": [{"id": "F001", "aspect": "condition", "question": "Does the citation concern the target condition?"}],
            "note": "",
        },
        "screening_review": trials,
    }
    # Every test must mutate its own copy, never the module-level fixtures.
    return copy.deepcopy(document)


def trial(citation_id: str, scenario: str, arms: list[str], answers: list[dict], expected: str) -> dict:
    return {
        "citation_id": citation_id,
        "scenario": scenario,
        "entities": {"arms": arms},
        "answers": answers,
        "expected_status": expected,
    }


RECORD_ANSWERS = [
    {"signal": "S001", "on": "record", "answer": "hit"},
    {"signal": "S002", "on": "record", "answer": "miss"},
]

THREE_ARM_TRIAL = trial(
    "C000001",
    "Three arms: A1 target intervention, A2 eligible comparator, A3 receives the disallowed measure D; the user confirmed D's restriction acts on the comparison",
    ["A1", "A2", "A3"],
    RECORD_ANSWERS
    + [
        {"signal": "S003", "on": "A1", "answer": "hit"},
        {"signal": "S003", "on": "A2", "answer": "miss"},
        {"signal": "S003", "on": "A3", "answer": "miss"},
        {"signal": "S004", "on": "A1", "answer": "miss"},
        {"signal": "S004", "on": "A2", "answer": "hit"},
        {"signal": "S004", "on": "A3", "answer": "miss"},
        {"signal": "S005", "on": "A1", "answer": "miss"},
        {"signal": "S005", "on": "A2", "answer": "miss"},
        {"signal": "S005", "on": "A3", "answer": "hit"},
    ],
    "retained",
)

INCOMPLETE_ARM_TRIAL = trial(
    "C000002",
    "The abstract does not state the comparator's regimen, so no comparison can be ruled out",
    ["A1", "A2"],
    RECORD_ANSWERS
    + [
        {"signal": "S003", "on": "A1", "answer": "hit"},
        {"signal": "S003", "on": "A2", "answer": "miss"},
        {"signal": "S004", "on": "A1", "answer": "miss"},
        {"signal": "S004", "on": "A2", "answer": "unclear"},
        {"signal": "S005", "on": "A1", "answer": "miss"},
        {"signal": "S005", "on": "A2", "answer": "unclear"},
    ],
    "retained",
)

CASE_REPORT_TRIAL = trial(
    "C000003",
    "publication_types lists Case Reports; the record-level disqualifying condition is satisfied",
    [],
    [{"signal": "S002", "on": "record", "answer": "hit"}],
    "excluded_candidate",
)

COMBINATION_TRIAL = trial(
    "C000004",
    "The target-intervention group combines another drug; whether the combination is still an eligible regimen is undecidable from the abstract",
    ["A1", "A2"],
    RECORD_ANSWERS
    + [
        {"signal": "S003", "on": "A1", "answer": "partial"},
        {"signal": "S003", "on": "A2", "answer": "miss"},
        {"signal": "S004", "on": "A1", "answer": "miss"},
        {"signal": "S004", "on": "A2", "answer": "hit"},
        {"signal": "S005", "on": "A1", "answer": "miss"},
        {"signal": "S005", "on": "A2", "answer": "miss"},
    ],
    "retained",
)

SWAPPED_ROLE_TRIAL = trial(
    "C000005",
    "Author labels swapped: the control group received the target intervention and vice versa; the comparison still qualifies",
    ["X1", "X2"],
    RECORD_ANSWERS
    + [
        {"signal": "S003", "on": "X1", "answer": "miss"},
        {"signal": "S003", "on": "X2", "answer": "hit"},
        {"signal": "S004", "on": "X1", "answer": "hit"},
        {"signal": "S004", "on": "X2", "answer": "miss"},
        {"signal": "S005", "on": "X1", "answer": "miss"},
        {"signal": "S005", "on": "X2", "answer": "miss"},
    ],
    "retained",
)


def confirm_baseline(workspace: str) -> str:
    confirmed = confirm_criteria(workspace, CRITERIA, "用户提供并经对话确认", [], [])
    return confirmed["criteria_path"]


class EvaluatorTests(unittest.TestCase):
    RULES = [CASE_REPORT_RULE, COMPARISON_RULE]

    def test_undetermined_answers_never_exclude(self):
        for answer in ("partial", "unclear"):
            result = evaluate_rules(
                [CASE_REPORT_RULE], {"entities": {}, "answers": [{"signal": "S002", "on": "record", "answer": answer}]}
            )
            self.assertEqual(result["status"], "retained")
            self.assertTrue(result["undetermined"])

    def test_missing_answers_are_undetermined_and_conflicts_resolve_to_undetermined(self):
        result = evaluate_rules([CASE_REPORT_RULE], {"entities": {}, "answers": []})
        self.assertEqual(result["status"], "retained")
        conflict = evaluate_rules(
            [CASE_REPORT_RULE],
            {"entities": {}, "answers": [{"signal": "S002", "on": "record", "answer": "hit"}, {"signal": "S002", "on": "record", "answer": "miss"}]},
        )
        self.assertEqual(conflict["status"], "retained")

    def test_explicit_case_report_hit_excludes(self):
        result = evaluate_rules(
            [CASE_REPORT_RULE], {"entities": {}, "answers": [{"signal": "S002", "on": "record", "answer": "hit"}]}
        )
        self.assertEqual(result["status"], "excluded_candidate")
        self.assertEqual(result["basis"][0]["rule"], "R001")

    def test_three_arm_study_with_irrelevant_forbidden_arm_retains(self):
        result = evaluate_rules(self.RULES, THREE_ARM_TRIAL)
        self.assertEqual(result["status"], "retained")

    def test_restriction_scope_changes_the_verdict_for_the_same_facts(self):
        forbidden_anywhere = {
            "id": "R003",
            "kind": "disqualifier",
            "unit": "arms",
            "quantifier": "any",
            "condition": {"signal": "S005", "on": "each", "expect": "hit"},
            "criterion_refs": [],
        }
        result = evaluate_rules([forbidden_anywhere], THREE_ARM_TRIAL)
        self.assertEqual(result["status"], "excluded_candidate")

    def test_swapped_arm_roles_still_form_a_qualifying_comparison(self):
        result = evaluate_rules(self.RULES, SWAPPED_ROLE_TRIAL)
        self.assertEqual(result["status"], "retained")
        self.assertEqual(result["undetermined"], [])

    def test_incomplete_group_information_never_proves_no_qualifying_comparison(self):
        result = evaluate_rules(self.RULES, INCOMPLETE_ARM_TRIAL)
        self.assertEqual(result["status"], "retained")
        self.assertTrue(any(item["rule"] == "R002" for item in result["undetermined"]))

    def test_partial_combination_leaves_the_comparison_undetermined(self):
        result = evaluate_rules(self.RULES, COMBINATION_TRIAL)
        self.assertEqual(result["status"], "retained")

    def test_empty_arm_enumeration_cannot_negate_existence(self):
        result = evaluate_rules([COMPARISON_RULE], {"entities": {"arms": []}, "answers": []})
        self.assertEqual(result["status"], "retained")


class CompileFlowTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        root = Path(self._temporary.name)
        self.root = root
        self.workspace = str(root / "review")
        from panner_mcp.criteria import preview_criteria

        preview = preview_criteria(self.workspace, CRITERIA, "用户提供并经对话确认", [], [])
        confirmed = confirm_criteria(
            self.workspace, CRITERIA, "用户提供并经对话确认", [], [], preview["plan_hash"], "全文准确"
        )
        self.criteria_path = confirmed["criteria_path"]

    def tearDown(self):
        self._temporary.cleanup()

    def write_draft(self, draft: dict) -> str:
        path = self.root / "protocol-draft.json"
        path.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)

    def valid_draft(self) -> dict:
        return draft_document([THREE_ARM_TRIAL, INCOMPLETE_ARM_TRIAL, CASE_REPORT_TRIAL, COMBINATION_TRIAL, SWAPPED_ROLE_TRIAL])

    def test_valid_draft_previews_ready_and_freezes_after_confirmation(self):
        path = self.write_draft(self.valid_draft())
        preview = preview_compile(self.workspace, self.criteria_path, path)
        self.assertEqual(preview["status"], "ready_for_confirmation", preview["issues"])
        self.assertEqual(preview["trial_mismatches"], [])
        self.assertEqual(preview["counts"]["signals"], 5)
        self.assertEqual(preview["counts"]["core_signals"], 1)
        self.assertEqual(preview["counts"]["disqualifiers"], 1)
        self.assertEqual(preview["counts"]["qualifications"], 1)
        verdicts = {item["citation_id"]: item["status"] for item in preview["trial_evaluations"]}
        self.assertEqual(
            verdicts,
            {
                "C000001": "retained",
                "C000002": "retained",
                "C000003": "excluded_candidate",
                "C000004": "retained",
                "C000005": "retained",
            },
        )
        frozen = freeze_compile(self.workspace, self.criteria_path, path, preview["plan_hash"], "协议准确表达了标准与试读结论")
        self.assertEqual(frozen["version"], "v0001")
        self.assertTrue(Path(frozen["protocol_path"]).is_file())
        status = protocol_status(self.workspace)
        self.assertEqual(status["status"], "confirmed")
        self.assertEqual(status["criteria_version"], "v0001")
        self.assertEqual(status["counts"]["trial_scenarios"], 5)

    def test_non_verbatim_quote_blocks_freeze(self):
        draft = self.valid_draft()
        draft["signals"][0]["criterion_refs"][0]["quote"] = "仅纳入 18 岁以上成年人"
        preview = preview_compile(self.workspace, self.criteria_path, self.write_draft(draft))
        self.assertEqual(preview["status"], "draft")
        self.assertTrue(any("not verbatim" in issue for issue in preview["issues"]))
        with self.assertRaisesRegex(ValueError, "still a draft"):
            freeze_compile(self.workspace, self.criteria_path, self.write_draft(draft), preview["plan_hash"], "同意")

    def test_open_clarifications_block_freeze_even_when_structure_is_valid(self):
        draft = self.valid_draft()
        draft["clarifications"] = ["D 的限制是否作用于整个研究？"]
        path = self.write_draft(draft)
        preview = preview_compile(self.workspace, self.criteria_path, path)
        self.assertEqual(preview["status"], "draft")
        self.assertTrue(any("clarification" in issue for issue in preview["issues"]))
        with self.assertRaisesRegex(ValueError, "still a draft"):
            freeze_compile(self.workspace, self.criteria_path, path, preview["plan_hash"], "同意")

    def test_trial_mismatch_reports_that_rules_do_not_express_the_decision(self):
        draft = self.valid_draft()
        draft["screening_review"][0]["expected_status"] = "excluded_candidate"
        preview = preview_compile(self.workspace, self.criteria_path, self.write_draft(draft))
        self.assertEqual(preview["status"], "draft")
        self.assertEqual(preview["trial_mismatches"][0]["citation_id"], "C000001")
        self.assertEqual(preview["trial_mismatches"][0]["evaluated_status"], "retained")

    def test_stale_plan_hash_is_rejected(self):
        path = self.write_draft(self.valid_draft())
        preview = preview_compile(self.workspace, self.criteria_path, path)
        revised = self.valid_draft()
        revised["signals"][0]["question"] = revised["signals"][0]["question"] + " (revised)"
        self.write_draft(revised)
        with self.assertRaisesRegex(ValueError, "draft changed"):
            freeze_compile(self.workspace, self.criteria_path, path, preview["plan_hash"], "同意")

    def test_unconfirmed_criteria_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            empty_workspace = str(Path(temporary) / "fresh")
            draft_path = self.write_draft(self.valid_draft())
            with self.assertRaisesRegex(ValueError, "outside its PANNER workspace"):
                preview_compile(empty_workspace, self.criteria_path, draft_path)

    def test_unexpected_expect_value_and_dead_signal_are_structural_issues(self):
        draft = self.valid_draft()
        draft["decision_rules"][0]["condition"]["expect"] = "partial"
        preview = preview_compile(self.workspace, self.criteria_path, self.write_draft(draft))
        self.assertTrue(any("expect must be hit or miss" in issue for issue in preview["issues"]))

        draft = self.valid_draft()
        extra = dict(POPULATION_SIGNAL)
        extra["id"] = "S006"
        extra["core"] = False
        draft["signals"].append(extra)
        preview = preview_compile(self.workspace, self.criteria_path, self.write_draft(draft))
        self.assertTrue(any("no rule reads this signal" in issue for issue in preview["issues"]))

    def test_field_rule_cannot_read_title_or_abstract(self):
        draft = self.valid_draft()
        draft["signals"][1]["field_rule"]["field"] = "title"
        preview = preview_compile(self.workspace, self.criteria_path, self.write_draft(draft))
        self.assertTrue(any("field_rule must read a structured field" in issue for issue in preview["issues"]))

    def test_protocol_content_must_be_english(self):
        draft = self.valid_draft()
        draft["signals"][0]["question"] = "研究人群是否为 18 岁及以上成年人？"
        preview = preview_compile(self.workspace, self.criteria_path, self.write_draft(draft))
        self.assertEqual(preview["status"], "draft")
        self.assertTrue(any("question must be in English" in issue for issue in preview["issues"]))


if __name__ == "__main__":
    unittest.main()
