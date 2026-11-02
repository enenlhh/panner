"""Offline counterexamples for screening correctness and version consistency."""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_screening as fixtures
import test_calibration as calibration_fixtures
from test_calibration import run_config
from panner_mcp import screening as screening
from panner_mcp.calibration import calibrate_compile
from panner_mcp.citations import confirm_citations, preview_citations
from panner_mcp.dedup import deduplicate
from panner_mcp.evaluate import evaluate_rules
from panner_mcp.export import export_screening
from panner_mcp.protocol import freeze_compile, preview_compile
from panner_mcp.workspace import workspace_state


class GroupSafetyTests(unittest.TestCase):
    rule = {"id": "R1", "kind": "disqualifier", "unit": "arms", "quantifier": "all",
            "condition": {"signal": "S1", "on": "each", "expect": "miss"}}

    def test_empty_and_incomplete_ranges_cannot_prove_all_groups_fail(self):
        for entities in ({"arms": []}, {"arms": ["A"], "complete": False}):
            with self.subTest(entities=entities):
                result = evaluate_rules([self.rule], {"entities": entities,
                    "answers": [{"signal": "S1", "on": "A", "answer": "miss"}]})
                self.assertEqual(result["status"], "retained")
                self.assertEqual(len(result["undetermined"]), 1)
        any_rule = {**self.rule, "quantifier": "any"}
        self.assertEqual(evaluate_rules([any_rule], {"entities": {"arms": ["A"], "complete": False},
            "answers": [{"signal": "S1", "on": "A", "answer": "miss"}]})["status"], "excluded_candidate")

    def test_group_count_obeys_the_confidence_floor_and_keeps_evidence(self):
        signal = {"id": "S1", "unit": "arm", "question": "Does this group receive treatment?",
                  "answer_criteria": {value: value for value in screening.ANSWERS}}
        for count in ("2", "unclear", "more", "0"):
            with self.subTest(count=count), patch.object(screening, "_calibrated_call", return_value=({
                screening.GROUP_COUNT_ID: {"choice": count, "confidence": 0.01},
                "S1__group_1": {"choice": "miss", "confidence": 0.99},
                "S1__group_2": {"choice": "miss", "confidence": 0.99}}, {})):
                judged = screening._judge_citation_calibrated({"model": "test", "retry": 0, "confidence_floor": 0.9},
                    [signal], {"title": "Trial", "abstract": "Incomplete groups"}, "offline")
                self.assertEqual(judged["entities"]["arms"], [])
                self.assertEqual(judged["entities"]["count_confidence"], 0.01)
                self.assertEqual(evaluate_rules([self.rule], judged)["status"], "retained")


class RunSafetyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ScreenTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.f = self.fixture

    def config(self):
        return fixtures.run_config(self.f.root, backend="calibrated", retry=0,
            endpoint="https://openrouter.ai/api/alpha/decisions")

    def run_screen(self, config):
        plan = screening.preview_screen(self.f.workspace, self.f.protocol_path, self.f.pool, config)
        return screening.confirm_screen(self.f.workspace, self.f.protocol_path, self.f.pool, config,
            plan["plan_hash"], "offline test")

    def test_group_all_rule_does_not_skip_semantic_judgment(self):
        draft = copy.deepcopy(fixtures.DRAFT)
        draft["signals"][1]["unit"] = "arm"
        draft["decision_rules"][1]["unit"] = "arms"
        draft["decision_rules"][1]["condition"]["on"] = "each"
        path = self.f.root / "group-draft.json"
        path.write_text(json.dumps(draft))
        criteria = str(Path(self.f.workspace) / workspace_state(self.f.workspace)[1]["active_eligibility_criteria"])
        plan = preview_compile(self.f.workspace, criteria, str(path))
        self.f.protocol_path = freeze_compile(self.f.workspace, criteria, str(path), plan["plan_hash"], "test")["protocol_path"]
        with patch.object(screening, "_calibrated_call", return_value=(
            {screening.GROUP_COUNT_ID: {"choice": "unclear"}}, {})) as backend:
            result = self.run_screen(self.config())
        self.assertEqual(backend.call_count, 3)
        self.assertEqual(result["counts"]["retained"], 3)
        self.assertEqual(result["counts"]["excluded"], 1)  # explicit case-report field rule

    def test_numeric_fields_and_per_record_failures(self):
        signal = {"field_rule": {"field": "year", "matches": [{"value": "2024", "answer": "hit"}]}}
        self.assertEqual(screening._field_rule_answer(signal, {"year": 2024}), "hit")
        self.assertEqual(screening._field_rule_answer(signal, {"year": None}), "unclear")
        original = screening._field_rule_answer
        def field_rule(signal, citation):
            if citation["id"] == "C000001":
                raise ValueError("invalid structured field")
            return original(signal, citation)
        with patch.object(screening, "_field_rule_answer", side_effect=field_rule), patch.object(
            screening, "_calibrated_call", return_value=({"S002": {"choice": "hit"}}, {})):
            result = self.run_screen(self.config())
        self.assertEqual(result["counts"]["failed"], 1)
        self.assertEqual(result["counts"]["pending"], 0)
        exported = export_screening(self.f.workspace, str(self.f.root / "failed-export"))
        self.assertEqual(exported["counts"]["retained_ris"], 3)

    def test_default_export_rejects_stale_pool_and_explicit_history_is_labelled(self):
        with patch.object(screening, "_calibrated_call", return_value=({"S002": {"choice": "hit"}}, {})):
            result = self.run_screen(self.config())
        source = self.f.root / "stage/citation.jsonl"
        with source.open("a") as stream:
            stream.write(json.dumps(fixtures.citation(5, title="New trial")) + "\n")
        preview = preview_citations(str(source), self.f.workspace, "direct", {"sources/s.txt": 5})
        saved = confirm_citations(str(source), self.f.workspace, "direct", {"sources/s.txt": 5}, preview["plan_hash"], "test")
        with self.assertRaisesRegex(ValueError, "stale"):
            export_screening(self.f.workspace, str(self.f.root / "before-dedup"))
        target = str(self.f.root / "pool-v2")
        plan = deduplicate(saved["citation_path"], target)
        deduplicate(saved["citation_path"], target, confirm=plan["plan_hash"])
        with self.assertRaisesRegex(ValueError, "stale"):
            export_screening(self.f.workspace, str(self.f.root / "stale-export"))
        history = export_screening(self.f.workspace, str(self.f.root / "history"), result["results_path"])
        self.assertFalse(history["is_current"])
        from openpyxl import load_workbook
        workbook = load_workbook(history["files"]["screening-report.xlsx"])
        summary = dict(workbook["Summary"].values)
        self.assertEqual(summary["Version context"], "historical")
        workbook.close()

    def test_previous_run_is_historical_even_when_input_versions_are_the_same(self):
        config = self.config()
        with patch.object(screening, "_calibrated_call", return_value=({"S002": {"choice": "hit"}}, {})):
            first = self.run_screen(config)
            values = json.loads(Path(config).read_text())
            values["concurrency"] = 1
            Path(config).write_text(json.dumps(values))
            self.run_screen(config)
        history = export_screening(self.f.workspace, str(self.f.root / "old-run"), first["results_path"])
        self.assertFalse(history["is_current"])
        self.assertIn("historical screening", Path(history["files"]["screening-retained.ris"]).read_text())

    def test_declaring_labels_requires_a_calibrated_protocol_version(self):
        config = self.config()
        with patch.object(screening, "_calibrated_call", return_value=({"S002": {"choice": "hit"}}, {})):
            self.run_screen(config)
        labels = Path(self.f.workspace) / "labels"
        labels.mkdir()
        (labels / "included.txt").write_text("title:Trial of aspirin in adults\n")
        with self.assertRaisesRegex(ValueError, "calibration"):
            screening.preview_screen(self.f.workspace, self.f.protocol_path, self.f.pool, config)
        self.assertEqual(screening.screen_status(self.f.workspace)["status"], "stale")
        with self.assertRaisesRegex(ValueError, "stale"):
            export_screening(self.f.workspace, str(self.f.root / "new-labels-export"))


class CalibrationSafetyTests(unittest.TestCase):
    setUp = calibration_fixtures.CalibrationTests.setUp
    tearDown = calibration_fixtures.CalibrationTests.tearDown
    scripted = calibration_fixtures.CalibrationTests.scripted

    def calibrate(self):
        with patch("panner_mcp.calibration._judge_citation_calibrated", side_effect=self.scripted(set())):
            return calibrate_compile(self.workspace, self.criteria, str(self.draft), self.config)

    def freeze(self):
        plan = preview_compile(self.workspace, self.criteria, str(self.draft))
        return freeze_compile(self.workspace, self.criteria, str(self.draft), plan["plan_hash"], "test")

    def test_changed_labels_and_pool_require_fresh_calibration(self):
        self.calibrate()
        labels = self.labels / "included.txt"
        labels.write_text(labels.read_text() + "pmid:3\n")
        with self.assertRaisesRegex(ValueError, "calibration"):
            self.freeze()
        self.calibrate()
        _, state = workspace_state(self.workspace)
        pool = Path(state["active_pool"])
        pool.write_text(pool.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "calibration"):
            self.freeze()

    def test_frozen_calibration_is_bound_to_judgment_config_and_continuation(self):
        report = self.calibrate()
        frozen = self.freeze()
        _, state = workspace_state(self.workspace)
        pool = state["active_pool"]
        run_config(self.root, model="another-model")
        with self.assertRaisesRegex(ValueError, "calibration"):
            screening.preview_screen(self.workspace, frozen["protocol_path"], pool, self.config)
        run_config(self.root, concurrency=1, batch_size=1, cost_cap=1)
        with patch.object(screening, "_judge_citation_calibrated", side_effect=self.scripted(set())):
            plan = screening.preview_screen(self.workspace, frozen["protocol_path"], pool, self.config)
            screening.confirm_screen(self.workspace, frozen["protocol_path"], pool, self.config, plan["plan_hash"], "test")
            labels = self.labels / "included.txt"
            labels.write_text(labels.read_text() + "pmid:3\n")
            with self.assertRaisesRegex(ValueError, "calibration"):
                screening.continue_screen(self.workspace, plan["plan_hash"])
            self.assertEqual(screening.screen_status(self.workspace)["status"], "stale")
            with self.assertRaisesRegex(ValueError, "stale"):
                export_screening(self.workspace, str(self.root / "changed-labels-export"))
        self.assertTrue(Path(report["report_path"]).is_file())

    def test_freeze_checks_model_changes_and_preview_binds_the_read(self):
        before = preview_compile(self.workspace, self.criteria, str(self.draft))
        self.calibrate()
        with self.assertRaisesRegex(ValueError, "preview|changed"):
            freeze_compile(self.workspace, self.criteria, str(self.draft), before["plan_hash"], "test")
        run_config(self.root, model="another-model")
        with self.assertRaisesRegex(ValueError, "calibration"):
            self.freeze()

    def test_new_confirmed_pool_needs_its_own_calibration(self):
        self.calibrate()
        source = self.root / "stage/citation.jsonl"
        rows = [json.loads(line) for line in source.read_text().splitlines()]
        rows[1]["abstract"] = "A revised abstract in the newly confirmed input."
        source.write_text("".join(json.dumps(row) + "\n" for row in rows))
        plan = preview_citations(str(source), self.workspace, "direct", {"sources/s.txt": 3})
        saved = confirm_citations(str(source), self.workspace, "direct", {"sources/s.txt": 3}, plan["plan_hash"], "test")
        target = str(self.root / "pool-v2")
        plan = deduplicate(saved["citation_path"], target)
        deduplicate(saved["citation_path"], target, confirm=plan["plan_hash"])
        with self.assertRaisesRegex(ValueError, "calibration"):
            self.freeze()

    def test_all_judgment_settings_are_bound_but_execution_settings_are_independent(self):
        self.calibrate()
        frozen = self.freeze()
        _, state = workspace_state(self.workspace)
        for change in ({"model": "other"}, {"endpoint": "https://openrouter.ai/another/decisions"},
                       {"confidence_floor": 0.9}, {"judge_channel": "another-judge"}):
            with self.subTest(change=change):
                run_config(self.root, **change)
                with self.assertRaisesRegex(ValueError, "calibration"):
                    screening.preview_screen(self.workspace, frozen["protocol_path"], state["active_pool"], self.config)
        run_config(self.root, concurrency=1, batch_size=1, cost_cap=1, retry=0, price_per_1m_input=0)
        plan = screening.preview_screen(self.workspace, frozen["protocol_path"], state["active_pool"], self.config)
        self.assertEqual(plan["status"], "planned")

    def test_continuation_uses_snapshot_and_frozen_evidence(self):
        self.calibrate()
        frozen = self.freeze()
        _, state = workspace_state(self.workspace)
        run_config(self.root, batch_size=1)
        with patch.object(screening, "_judge_citation_calibrated", side_effect=self.scripted(set())):
            plan = screening.preview_screen(self.workspace, frozen["protocol_path"], state["active_pool"], self.config)
            result = screening.confirm_screen(self.workspace, frozen["protocol_path"], state["active_pool"], self.config,
                                              plan["plan_hash"], "test")
            Path(self.config).unlink()
            result = screening.continue_screen(self.workspace, plan["plan_hash"])
            self.assertEqual(result["counts"]["pending"], 1)
            evidence = Path(frozen["protocol_path"]).parent / "calibration.json"
            evidence.write_text(evidence.read_text() + "\n")
            with self.assertRaisesRegex(ValueError, "calibration"):
                screening.continue_screen(self.workspace, plan["plan_hash"])
            with self.assertRaisesRegex(ValueError, "calibration"):
                export_screening(self.workspace, str(self.root / "broken-evidence-export"))

    def test_input_changes_during_calibration_do_not_publish_a_report(self):
        answer = self.scripted(set())
        def changing(config, signals, citation, key):
            labels = self.labels / "included.txt"
            labels.write_text(labels.read_text() + "pmid:3\n")
            return answer(config, signals, citation, key)
        with patch("panner_mcp.calibration._judge_citation_calibrated", side_effect=changing):
            with self.assertRaisesRegex(ValueError, "calibration"):
                calibrate_compile(self.workspace, self.criteria, str(self.draft), self.config)
        self.assertEqual(list((Path(self.workspace) / "calibration").glob("*.json")), [])

    def test_empty_or_subset_custom_labels_cannot_replace_declared_set(self):
        for content in ("", "pmid:1\n"):
            custom = self.root / "custom.txt"
            custom.write_text(content)
            with self.subTest(content=content), patch("panner_mcp.calibration._judge_citation_calibrated", side_effect=self.scripted(set())), self.assertRaisesRegex(ValueError, "labels|included set"):
                calibrate_compile(self.workspace, self.criteria, str(self.draft), self.config, str(custom))

    def test_unmapped_and_legacy_reports_cannot_approve_freezing(self):
        labels = self.labels / "included.txt"
        labels.write_text("pmid:404\n")
        report = self.calibrate()
        with self.assertRaisesRegex(ValueError, "unmapped"):
            self.freeze()
        labels.write_text("pmid:1\n")
        report = self.calibrate()
        path = Path(report["report_path"])
        value = json.loads(path.read_text())
        value.pop("binding", None)
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "calibration"):
            self.freeze()

    def test_calibration_reports_are_preserved_and_freeze_copies_its_evidence(self):
        first = self.calibrate()
        original = Path(first["report_path"]).read_bytes()
        second = self.calibrate()
        self.assertNotEqual(first["report_path"], second["report_path"])
        self.assertEqual(Path(first["report_path"]).read_bytes(), original)
        frozen = self.freeze()
        snapshot = Path(frozen["protocol_path"]).parent / "calibration.json"
        self.assertEqual(json.loads(snapshot.read_text())["binding"], second["binding"])
