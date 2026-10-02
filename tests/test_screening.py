import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import panner_mcp.screening as fp
from mcp.client import Client
from panner_mcp.citations import confirm_citations, preview_citations
from panner_mcp.criteria import confirm_criteria, preview_criteria
from panner_mcp.screening import confirm_screen, continue_screen, screen_status, load_run_config, preview_screen
from panner_mcp.export import export_screening
from panner_mcp.evaluate import evaluate_rules
from panner_mcp.protocol import freeze_compile, preview_compile
from panner_mcp.server import mcp

CRITERIA = (
    "# Review objective\n研究目标。\n\n# Eligibility criteria\n- 仅纳入 18 岁及以上成年人\n- 排除病例报告\n"
)

DRAFT = {
    "format": "panner-protocol-v1",
    "signals": [
        {
            "id": "S001", "unit": "citation_text", "question": "Is this publication a case report?",
            "core": False, "evaluation_mode": "field_rule", "evidence_fields": ["publication_types"],
            "field_rule": {"field": "publication_types", "matches": [{"value": "case reports", "answer": "hit"}]},
            "answer_criteria": {"hit": "lists case reports", "partial": "mixed", "miss": "names none", "unclear": "absent"},
            "criterion_refs": [{"criterion_id": "E002", "quote": "排除病例报告"}],
        },
        {
            "id": "S002", "unit": "population", "question": "Is the study population adults aged 18 years or older?",
            "core": True, "evaluation_mode": "semantic", "evidence_fields": ["title", "abstract"],
            "answer_criteria": {"hit": "all participants are adults", "partial": "mixed ages", "miss": "every participant is a minor", "unclear": "unstated"},
            "criterion_refs": [{"criterion_id": "E001", "quote": "仅纳入 18 岁及以上成年人"}],
        },
    ],
    "decision_rules": [
        {"id": "R001", "kind": "disqualifier", "unit": "record", "quantifier": "all",
         "condition": {"signal": "S001", "on": "record", "expect": "hit"},
         "criterion_refs": [{"criterion_id": "E002", "quote": "排除病例报告"}]},
        {"id": "R002", "kind": "disqualifier", "unit": "record", "quantifier": "all",
         "condition": {"signal": "S002", "on": "record", "expect": "miss"},
         "criterion_refs": [{"criterion_id": "E001", "quote": "仅纳入 18 岁及以上成年人"}]},
    ],
    "criteria_map": [
        {"id": "E001", "quote": "仅纳入 18 岁及以上成年人", "occurrence": 1, "role": "eligibility", "target": "population", "signal_ids": ["S002"], "rule_ids": ["R002"], "handoffs": []},
        {"id": "E002", "quote": "排除病例报告", "occurrence": 1, "role": "eligibility", "target": "citation_text", "signal_ids": ["S001"], "rule_ids": ["R001"], "handoffs": []},
    ],
    "clarifications": [],
    "relevance": {"enabled": True, "facets": [{"id": "F001", "aspect": "population", "question": "Does the citation concern adult study populations?"}], "note": ""},
    "screening_review": [],
}


def citation(number: int, publication_types=None, title="Trial of aspirin in adults", abstract="Adults aged 40-70 were randomised.") -> dict:
    return {
        "id": f"C{number:06d}", "source": {"file": "sources/s.txt", "position": number, "id": ""},
        "title": title, "abstract": abstract, "authors": f"Author {number}; B", "year": 2020 + number, "journal": f"Journal {number}",
        "container_title": "", "volume": "", "issue": "", "pages": "",
        "publication_types": publication_types or [], "doi": "", "pmid": "", "entry_type": "",
    }


def run_config(root: Path, **overrides) -> str:
    config = {
        "backend": "llm", "provider": "openrouter", "model": "test-model", "temperature": 0,
        "concurrency": 2, "cost_cap": 50, "price_per_1m_input": 0.3, "price_per_1m_output": 2.5,
    }
    config.update(overrides)
    path = root / "run-config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return str(path)


class ScreenTests(unittest.TestCase):
    def setUp(self):
        # The network layer is stubbed in these tests, but confirm_screen still
        # enforces the key-from-environment rule; provide a placeholder.
        self._previous_key = os.environ.get("PANNER_LLM_API_KEY")
        os.environ["PANNER_LLM_API_KEY"] = "test-only-placeholder-key"
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.workspace = str(self.root / "review")

        preview = preview_criteria(self.workspace, CRITERIA, "原文", [], [])
        confirmed = confirm_criteria(self.workspace, CRITERIA, "原文", [], [], preview["plan_hash"], "全文准确")

        stage = self.root / "stage"
        (stage / "sources").mkdir(parents=True)
        (stage / "sources/s.txt").write_text("x", encoding="utf-8")
        rows = [
            citation(1), citation(2, publication_types=["Case Reports"], title="Case report of aspirin-induced rash", abstract="A 55-year-old adult developed a rash."),
            citation(3, title="Paediatric trial", abstract="Children aged 5-10 were enrolled."),
            citation(4, title="Adults were enrolled and randomised", abstract="randomised adults; explicitly stated"),
        ]
        (stage / "citation.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        intake = preview_citations(str(stage / "citation.jsonl"), self.workspace, "direct", {"sources/s.txt": 4})
        saved = confirm_citations(str(stage / "citation.jsonl"), self.workspace, "direct", {"sources/s.txt": 4}, intake["plan_hash"], "确认")
        self.pool_path = str(self.root / "pool")
        plan = preview_citations(str(stage / "citation.jsonl"), self.workspace, "direct", {"sources/s.txt": 4})
        from panner_mcp.dedup import deduplicate

        dedup_plan = deduplicate(saved["citation_path"], self.pool_path)
        self.pool = deduplicate(saved["citation_path"], self.pool_path, confirm=dedup_plan["plan_hash"])["citation_pool_path"]

        draft_file = self.root / "draft.json"
        draft_file.write_text(json.dumps(DRAFT, ensure_ascii=False), encoding="utf-8")
        compiled = preview_compile(self.workspace, confirmed["criteria_path"], str(draft_file))
        frozen = freeze_compile(self.workspace, confirmed["criteria_path"], str(draft_file), compiled["plan_hash"], "确认")
        self.protocol_path = frozen["protocol_path"]

    def tearDown(self):
        self._temporary.cleanup()
        if self._previous_key is None:
            os.environ.pop("PANNER_LLM_API_KEY", None)
        else:
            os.environ["PANNER_LLM_API_KEY"] = self._previous_key

    def fake_backend(self, answers_by_title, confirmations="confirmed"):
        """Monkeypatch the chat call; answers keyed by a substring of the title."""

        def fake(config, system, user, api_key):
            payload = json.loads(user)
            if "statements" in payload:
                return {"verifications": [{"index": i, "result": confirmations} for i in range(len(payload["statements"]))]}, {"prompt_tokens": 50, "completion_tokens": 20}
            title = payload["citation"]["title"]
            for key, answers in answers_by_title.items():
                if key in title:
                    return answers, {"prompt_tokens": 100, "completion_tokens": 40}
            raise AssertionError(f"no scripted answer for {title!r}")

        return fake

    def test_run_config_validation(self):
        path = run_config(self.root)
        _, config = load_run_config(path, experimental=True)
        self.assertEqual(config["concurrency"], 2)
        bad = run_config(self.root, temperature=0.7)
        with self.assertRaisesRegex(ValueError, "temperature 0"):
            load_run_config(bad, experimental=True)
        with self.assertRaisesRegex(ValueError, "backend must be calibrated"):
            load_run_config(path)
        unapproved = run_config(self.root, backend="calibrated", endpoint="https://unapproved.example/decisions")
        with self.assertRaisesRegex(ValueError, "not allowed"):
            load_run_config(unapproved)
        scoped = run_config(self.root, backend="calibrated", endpoint="https://openrouter.ai/api/alpha/decisions",
                            scope=["C000001"])
        with self.assertRaisesRegex(ValueError, "complete active citation pool"):
            load_run_config(scoped)

    def test_judge_channel_records_the_judge_and_rejects_an_empty_label(self):
        _, config = load_run_config(run_config(self.root), experimental=True)
        self.assertEqual(config["judge_channel"], fp.DEFAULT_JUDGE_CHANNEL)
        local = run_config(self.root, judge_channel="local-llm")
        _, config = load_run_config(local, experimental=True)
        self.assertEqual(config["judge_channel"], "local-llm")
        with self.assertRaisesRegex(ValueError, "judge_channel"):
            load_run_config(run_config(self.root, judge_channel="   "), experimental=True)
        with self.assertRaisesRegex(ValueError, "confidence_floor"):
            load_run_config(run_config(self.root, confidence_floor=1.5), experimental=True)

    def test_preview_reports_counts_and_cost(self):
        path = run_config(self.root)
        preview = preview_screen(self.workspace, self.protocol_path, self.pool, path, experimental=True)
        self.assertEqual(preview["status"], "planned")
        self.assertEqual(preview["counts"]["to_screen"], 4)
        self.assertEqual(preview["counts"]["semantic_signals"], 1)
        self.assertEqual(preview["counts"]["field_rule_signals"], 1)
        self.assertGreater(preview["estimated_calls"], 0)

    def test_execution_with_case_report_exclusion_pediatric_and_confirmed_adult(self):
        # C1: adult hit (confirmed) -> retained; C2: case report via field_rule -> excluded (no confirmation call for field_rule-only basis)
        # C3: pediatric -> S002 miss, confirmation confirms -> excluded; C4: adult hit confirmed -> retained.
        answers = {
            "Trial of aspirin": {"entities": {"arms": []}, "answers": [{"signal": "S002", "on": "record", "answer": "hit"}]},
            "Paediatric trial": {"entities": {"arms": []}, "answers": [{"signal": "S002", "on": "record", "answer": "miss"}]},
            "Adults were enrolled": {"entities": {"arms": []}, "answers": [{"signal": "S002", "on": "record", "answer": "hit"}]},
            "Case report": {"entities": {"arms": []}, "answers": [{"signal": "S002", "on": "record", "answer": "hit"}]},
        }
        fake = self.fake_backend(answers)
        original = fp._chat_call
        fp._chat_call = fake
        try:
            path = run_config(self.root)
            plan = preview_screen(self.workspace, self.protocol_path, self.pool, path, experimental=True)
            result = confirm_screen(self.workspace, self.protocol_path, self.pool, path, plan["plan_hash"], "确认", experimental=True)
        finally:
            fp._chat_call = original
        self.assertEqual(result["counts"], {"excluded": 2, "retained": 2, "failed": 0, "pending": 0, "to_screen": 4})
        self.assertFalse(result["stopped_early"])
        results = {json.loads(l)["citation_id"]: json.loads(l) for l in Path(result["results_path"]).read_text(encoding="utf-8").splitlines()}
        self.assertEqual(results["C000002"]["status"], "excluded")
        self.assertEqual(results["C000003"]["status"], "excluded")
        status = screen_status(self.workspace)
        self.assertEqual(status["recomputation_mismatches"], [])

    def test_calibrated_explicit_answer_is_the_gate(self):
        config = run_config(self.root, backend="calibrated", provider="openrouter",
                            model="~typesafe/jev-latest", endpoint="https://openrouter.ai/api/alpha/decisions")
        _, loaded = load_run_config(config)
        self.assertNotIn("gate", loaded)

        def fake_calibrated(config, signals, citation, key):
            answer = "miss" if "Paediatric" in citation["title"] else "hit"
            return ({signal["id"]: {"choice": answer} for signal in signals}, {"cost": 0.00002})

        original = fp._calibrated_call
        fp._calibrated_call = fake_calibrated
        try:
            plan = preview_screen(self.workspace, self.protocol_path, self.pool, config)
            result = confirm_screen(self.workspace, self.protocol_path, self.pool, config, plan["plan_hash"], "确认")
        finally:
            fp._calibrated_call = original
        self.assertEqual(result["counts"]["excluded"], 2)
        results = {json.loads(l)["citation_id"]: json.loads(l) for l in Path(result["results_path"]).read_text(encoding="utf-8").splitlines()}
        self.assertEqual(results["C000003"]["status"], "excluded")
        judgments = {json.loads(l)["citation_id"]: json.loads(l) for l in Path(result["judgments_path"]).read_text(encoding="utf-8").splitlines()}
        for item in judgments["C000003"]["answers"]:
            self.assertNotIn("confidence", item)
            self.assertNotIn("probabilities", item)
        manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
        self.assertGreater(manifest["cost_usd"], 0)
        status = screen_status(self.workspace)
        self.assertEqual(status["recomputation_mismatches"], [])

    def test_calibrated_group_answers_bind_to_distinct_groups_in_one_call(self):
        signal = {
            "id": "S003", "unit": "arm", "question": "Does this group receive the target treatment?",
            "answer_criteria": {"hit": "explicitly yes", "partial": "mixed", "miss": "explicitly no", "unclear": "unknown"},
        }
        sent = []

        def fake(config, questions, citation, key):
            sent.append([item["id"] for item in questions])
            return {
                "PANNER_GROUP_COUNT": {"choice": "2"},
                "S003__group_1": {"choice": "hit"},
                "S003__group_2": {"choice": "miss"},
            }, {"cost": 0.00002}

        with patch.object(fp, "_calibrated_call", side_effect=fake):
            judged = fp._judge_citation_calibrated(
                {"model": "test", "retry": 0}, [signal], {"title": "A versus B", "abstract": "A and B were compared."}, "test"
            )
        self.assertEqual(len(sent), 1)
        self.assertEqual(judged["entities"]["arms"], ["group_1", "group_2"])
        self.assertEqual(judged["answers"], [
            {"signal": "S003", "on": "group_1", "answer": "hit"},
            {"signal": "S003", "on": "group_2", "answer": "miss"},
        ])
        with patch.object(fp, "_calibrated_call", return_value=(
            {"PANNER_GROUP_COUNT": {"choice": "unclear"}, "S003__group_1": {"choice": "miss"}}, {"cost": 0.00002}
        )):
            uncertain = fp._judge_citation_calibrated(
                {"model": "test", "retry": 0}, [signal], {"title": "Trial", "abstract": "The groups are incompletely described."}, "test"
            )
        qualification = {"id": "R001", "kind": "qualification", "unit": "comparison", "quantifier": "exists",
                         "condition": {"signal": "S003", "on": "member0", "expect": "hit"}}
        self.assertEqual(evaluate_rules([qualification], uncertain)["status"], "retained")



    def test_answers_outside_four_values_become_unclear(self):
        answers = {
            "Trial of aspirin": {"entities": {"arms": []}, "answers": [{"signal": "S002", "on": "record", "answer": "definitely"}]},
            "Paediatric trial": {"entities": {"arms": []}, "answers": [{"signal": "S002", "on": "record", "answer": "miss"}]},
            "Adults were enrolled": {"entities": {"arms": []}, "answers": [{"signal": "S002", "on": "record", "answer": "hit"}]},
            "Case report": {"entities": {"arms": []}, "answers": []},
        }
        fake = self.fake_backend(answers)
        original = fp._chat_call
        fp._chat_call = fake
        try:
            path = run_config(self.root)
            plan = preview_screen(self.workspace, self.protocol_path, self.pool, path, experimental=True)
            result = confirm_screen(self.workspace, self.protocol_path, self.pool, path, plan["plan_hash"], "确认", experimental=True)
        finally:
            fp._chat_call = original
        judgments = {json.loads(l)["citation_id"]: json.loads(l) for l in Path(result["judgments_path"]).read_text(encoding="utf-8").splitlines()}
        by_signal = {item["signal"]: item for item in judgments["C000001"]["answers"]}
        self.assertEqual(by_signal["S002"]["answer"], "unclear")
        self.assertTrue(any("outside the four values" in flag for flag in judgments["C000001"]["flags"]))
        results = {json.loads(line)["citation_id"]: json.loads(line) for line in Path(result["results_path"]).read_text(encoding="utf-8").splitlines()}
        self.assertEqual(results["C000001"]["status"], "retained")
        self.assertEqual(results["C000001"]["review_priority"], "topic_unclear")

    def test_bounded_calibrated_batches_preserve_every_citation(self):
        config = run_config(
            self.root, backend="calibrated", provider="openrouter", model="~typesafe/jev-latest",
            endpoint="https://openrouter.ai/api/alpha/decisions", batch_size=2, cost_cap=1,
            concurrency=2, retry=0,
        )

        def fake_calibrated(config, signals, citation, key):
            return ({signal["id"]: {"choice": "hit"} for signal in signals}, {"cost": 0.00002})

        original = fp._calibrated_call
        fp._calibrated_call = fake_calibrated
        try:
            plan = preview_screen(self.workspace, self.protocol_path, self.pool, config)
            first = confirm_screen(self.workspace, self.protocol_path, self.pool, config, plan["plan_hash"], "确认")
            self.assertEqual(first["status"], "partial")
            self.assertEqual(first["counts"]["pending"], 2)
            self.assertEqual(screen_status(self.workspace)["plan_hash"], plan["plan_hash"])
            handoff = export_screening(self.workspace, str(self.root / "partial-export"))
            self.assertEqual(handoff["counts"]["retained_ris"], 3)
            Path(config).unlink()  # continuation uses the confirmed snapshot, not this temporary input file
            second = continue_screen(self.workspace, plan["plan_hash"])
            self.assertEqual(second["counts"]["pending"], 1)
            final = continue_screen(self.workspace, plan["plan_hash"])
        finally:
            fp._calibrated_call = original
        self.assertEqual(final["status"], "confirmed")
        self.assertEqual(final["counts"]["pending"], 0)
        self.assertEqual(json.loads(Path(final["manifest_path"]).read_text(encoding="utf-8"))["calls"], 3)
        results = [json.loads(line) for line in Path(final["results_path"]).read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(results), 4)
        self.assertEqual(screen_status(self.workspace)["recomputation_mismatches"], [])

    def test_pilot_mode_and_sample_size_validation(self):
        ok = run_config(self.root, backend="calibrated", provider="openrouter", model="~typesafe/jev-latest",
                        endpoint="https://openrouter.ai/api/alpha/decisions", scope=["C000001"], pilot=True)
        _, config = load_run_config(ok)
        self.assertTrue(config["pilot"])
        scoped = run_config(self.root, backend="calibrated", provider="openrouter", model="~typesafe/jev-latest",
                            endpoint="https://openrouter.ai/api/alpha/decisions", scope=["C000001"])
        with self.assertRaisesRegex(ValueError, "pilot"):
            load_run_config(scoped)
        bad_size = run_config(self.root, backend="calibrated", provider="openrouter", model="~typesafe/jev-latest",
                              endpoint="https://openrouter.ai/api/alpha/decisions", sample_size=0)
        with self.assertRaisesRegex(ValueError, "sample_size"):
            load_run_config(bad_size)

    def test_retry_failed_requeues_infrastructure_failures(self):
        config = run_config(
            self.root, backend="calibrated", provider="openrouter", model="~typesafe/jev-latest",
            endpoint="https://openrouter.ai/api/alpha/decisions", batch_size=4, cost_cap=50,
            concurrency=2, retry=0,
        )

        def failing(config, signals, citation, key):
            if "randomised" in citation["title"]:
                raise RuntimeError("[SSL] EOF occurred in violation of protocol")
            return ({signal["id"]: {"choice": "miss"} for signal in signals}, {"cost": 0.0})

        original = fp._calibrated_call
        fp._calibrated_call = failing
        try:
            plan = preview_screen(self.workspace, self.protocol_path, self.pool, config)
            result = confirm_screen(self.workspace, self.protocol_path, self.pool, config, plan["plan_hash"], "确认")
            while result["counts"]["pending"]:
                result = continue_screen(self.workspace, plan["plan_hash"])
            self.assertEqual(result["counts"]["failed"], 1)
            self.assertEqual(result["status"], "partial")

            def healthy(config, signals, citation, key):
                return ({signal["id"]: {"choice": "miss"} for signal in signals}, {"cost": 0.0})

            fp._calibrated_call = healthy
            result = continue_screen(self.workspace, plan["plan_hash"], retry_failed=True)
        finally:
            fp._calibrated_call = original
        self.assertEqual(result["counts"]["failed"], 0)
        self.assertEqual(result["counts"]["pending"], 0)
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["counts"]["retained"] + result["counts"]["excluded"],
                         result["counts"]["to_screen"])


class ScreenMCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirm_returns_partial_status_through_mcp(self):
        fixture = ScreenTests(methodName="test_preview_reports_counts_and_cost")
        fixture.setUp()
        try:
            config = run_config(
                fixture.root, backend="calibrated", provider="openrouter", model="~typesafe/jev-latest",
                endpoint="https://openrouter.ai/api/alpha/decisions", batch_size=2, cost_cap=1,
                concurrency=2, retry=0,
            )
            plan = preview_screen(fixture.workspace, fixture.protocol_path, fixture.pool, config)

            def fake_calibrated(config, signals, citation, key):
                return ({signal["id"]: {"choice": "hit"} for signal in signals}, {"cost": 0.00002})

            updates = []

            async def on_progress(progress, total, message):
                updates.append((progress, total))

            with patch.object(fp, "_calibrated_call", side_effect=fake_calibrated):
                async with Client(mcp, raise_exceptions=True) as client:
                    result = await client.call_tool(
                        "panner_screen_confirm",
                        {"workspace_path": fixture.workspace, "protocol_path": fixture.protocol_path,
                         "pool_path": fixture.pool, "run_config_path": config,
                         "plan_hash": plan["plan_hash"], "user_confirmation": "确认"},
                        progress_callback=on_progress,
                    )
            self.assertEqual(result.structured_content["status"], "partial")
            self.assertEqual(result.structured_content["counts"]["pending"], 2)
            self.assertEqual(updates[-1], (2, 2))
        finally:
            fixture.tearDown()


class JsonlLineBoundaryTest(unittest.TestCase):
    """JSONL is terminated by "\\n" only; Unicode line boundaries stay inside values."""

    def test_unicode_line_separators_do_not_split_a_record(self):
        separators = ("\u2028", "\u2029", "\x85", "\x0b", "\x0c")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "citation-pool.jsonl"
            rows = [
                {"id": f"C{number:06d}", "title": f"record{number}", "abstract": f"before{separator}after"}
                for number, separator in enumerate(separators, 1)
            ]
            path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
            loaded = fp._load_jsonl(path)
        self.assertEqual([row["id"] for row in loaded], [row["id"] for row in rows])
        self.assertEqual(loaded[0]["abstract"], "before\u2028after")


if __name__ == "__main__":
    unittest.main()
