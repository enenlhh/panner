import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from panner_mcp.citations import confirm_citations, preview_citations, read_citations
from panner_mcp.dedup import _derive, _pairs_and_clusters, deduplicate


def citation(number: int, *, doi: str = "10.1234/example", abstract: str = "An example abstract") -> dict:
    return {
        "id": f"C{number:06d}",
        "source": {"file": "sources/search.txt", "position": number, "id": ""},
        "title": "Treatment of alpha disease in adults",
        "abstract": abstract,
        "authors": "Alice Smith; Bob Jones",
        "year": 2024,
        "journal": "Test Journal",
        "container_title": "",
        "volume": "1",
        "issue": "1",
        "pages": "1-10",
        "publication_types": ["Journal Article"],
        "doi": doi,
        "pmid": "",
        "entry_type": "",
    }


def bundle(root: Path, rows: list[dict]) -> Path:
    stage = root / "stage"
    (stage / "sources").mkdir(parents=True)
    (stage / "sources/search.txt").write_bytes(b"original source bytes\r\n")
    path = stage / "citation.jsonl"
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    return path


class CitationPoolTests(unittest.TestCase):
    def test_citation_snapshot_requires_reconciled_input_and_preserves_original(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = bundle(root, [citation(1), citation(2)])
            workspace = str(root / "review")
            counts = {"sources/search.txt": 2}
            mismatch = preview_citations(str(path), workspace, "direct mapping", {"sources/search.txt": 1})
            self.assertEqual(mismatch["status"], "draft")
            preview = preview_citations(str(path), workspace, "direct mapping", counts)
            self.assertEqual(preview["status"], "ready_for_confirmation")
            self.assertEqual(preview["input_lines"], 2)
            self.assertEqual(preview["source_coverage"]["sources/search.txt"]["abstract_nonempty"], 2)
            original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            saved = confirm_citations(str(path), workspace, "direct mapping", counts, preview["plan_hash"], "来源及对账正确")
            self.assertEqual(Path(saved["citation_path"]).read_bytes(), path.read_bytes())
            self.assertEqual(saved["citation_sha256"], original_hash)
            self.assertEqual(
                (Path(workspace) / "citations/v0001/sources/search.txt").read_bytes(), b"original source bytes\r\n"
            )

    def test_real_bibdedupe_matches_same_source_but_routes_identifier_conflict(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            staged = bundle(root, [citation(1), citation(2)])
            with self.assertRaisesRegex(ValueError, "confirmed PANNER"):
                deduplicate(str(staged), str(root / "unconfirmed-pool"))
            intake = preview_citations(str(staged), str(root / "review"), "direct mapping", {"sources/search.txt": 2})
            saved_input = confirm_citations(
                str(staged), str(root / "review"), "direct mapping", {"sources/search.txt": 2}, intake["plan_hash"], "confirmed"
            )
            path = Path(saved_input["citation_path"])
            input_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            output = root / "auto-pool"
            plan = deduplicate(str(path), str(output))
            self.assertEqual(plan["counts"]["duplicates"], 1)
            self.assertFalse(output.exists())
            saved = deduplicate(str(path), str(output), confirm=plan["plan_hash"])
            pool = [json.loads(line) for line in Path(saved["citation_pool_path"]).read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["duplicate_of"] for row in pool], [None, "C000001"])
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), input_hash)
            manifest = json.loads(Path(saved["manifest_path"]).read_text(encoding="utf-8"))
            self.assertEqual(manifest["engine_version"], "0.11.0")
            self.assertEqual(manifest["policy_version"], "panner-dedup-v1")
            (path.parent / "sources/search.txt").write_text("modified source", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source snapshot differs"):
                deduplicate(str(path), str(root / "tampered-pool"))

            conflict_stage = bundle(root / "conflict", [citation(1, doi="10.1234/first"), citation(2, doi="10.1234/second")])
            conflict_intake = preview_citations(
                str(conflict_stage), str(root / "conflict/review"), "direct mapping", {"sources/search.txt": 2}
            )
            conflict_path = confirm_citations(
                str(conflict_stage), str(root / "conflict/review"), "direct mapping", {"sources/search.txt": 2},
                conflict_intake["plan_hash"], "confirmed"
            )["citation_path"]
            conflict_plan = deduplicate(str(conflict_path), str(root / "conflict-pool"))
            self.assertEqual(conflict_plan["counts"]["duplicates"], 0)
            self.assertEqual(conflict_plan["counts"]["pending_groups"], 1)
            conflict = deduplicate(str(conflict_path), str(root / "conflict-pool"), confirm=conflict_plan["plan_hash"])
            review = [json.loads(line) for line in Path(conflict["review_path"]).read_text(encoding="utf-8").splitlines()]
            self.assertTrue(any(item["kind"] == "identifier_conflict" and item["field"] == "doi" for item in review))

            resolution = root / "decisions.jsonl"
            resolution.write_text(
                json.dumps({"group": "G000001", "partitions": [{"members": ["C000001", "C000002"], "screen_id": "C000001"}], "reason": "用户核对原文后确认同一出版物"}) + "\n",
                encoding="utf-8",
            )
            manual_plan = deduplicate(str(conflict_path), str(root / "manual-pool"), str(resolution))
            self.assertEqual(manual_plan["counts"]["duplicates"], 1)
            self.assertEqual(manual_plan["counts"]["resolved_groups"], 1)

    def test_transitive_maybe_pair_prevents_auto_merge(self):
        rows = [citation(1), citation(2), citation(3)]
        pairs = [
            {"id_1": "C000001", "id_2": "C000002", "label": "duplicate"},
            {"id_1": "C000002", "id_2": "C000003", "label": "duplicate"},
            {"id_1": "C000001", "id_2": "C000003", "label": "maybe"},
        ]
        pool, review, _, counts = _derive(rows, pairs, [["C000001", "C000002", "C000003"]], [])
        self.assertEqual(counts["duplicates"], 0)
        self.assertEqual(counts["pending_groups"], 1)
        self.assertTrue(all(row["review_group"] == "G000001" for row in pool))
        self.assertEqual(review[0]["kind"], "intragroup_conflict")

    def test_maybe_between_components_preserves_safe_internal_merges(self):
        rows = [citation(number) for number in range(1, 5)]
        pairs = [
            {"id_1": "C000001", "id_2": "C000002", "label": "duplicate"},
            {"id_1": "C000003", "id_2": "C000004", "label": "duplicate"},
            {"id_1": "C000002", "id_2": "C000003", "label": "maybe"},
        ]
        pool, _, _, counts = _derive(rows, pairs, [["C000001", "C000002"], ["C000003", "C000004"]], [])
        self.assertEqual(counts["duplicates"], 2)
        self.assertEqual(counts["pending_groups"], 1)
        self.assertEqual([row["duplicate_of"] for row in pool], [None, "C000001", None, "C000003"])

    def test_malformed_citation_lines_are_reported_without_drop(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = bundle(root, [citation(1)])
            with path.open("a", encoding="utf-8") as stream:
                stream.write('{"id":"C000002","id":"C000002"}\n')
            _, rows, issues = read_citations(str(path))
            self.assertEqual(len(rows), 1)
            self.assertTrue(any("duplicate JSON key" in issue for issue in issues))
            preview = preview_citations(str(path), str(root / "review"), "direct mapping", {"sources/search.txt": 2})
            self.assertEqual(preview["status"], "draft")
            self.assertEqual(preview["input_lines"], 2)
            self.assertEqual(preview["citations"], 1)


if __name__ == "__main__":
    unittest.main()
