"""Offline validation Batch preparation invariants."""
import json
import tempfile
import unittest
from pathlib import Path

from ai_ecosystem.calibration.prepare_ai_profile_validation_batch import (
    MAX_FILE_BYTES, MAX_REQUESTS, assign_splits, atomic_json, extend_splits,
    host_family_overlap, pack_files,
)


class ValidationBatchTests(unittest.TestCase):
    def test_split_keeps_shared_website_group_together(self):
        ids = [f"C{i:03d}" for i in range(40)]
        groups = {cid: cid for cid in ids}
        groups["C000"] = groups["C001"] = "shared"
        sectors = {cid: "C" if int(cid[1:]) % 2 else "F" for cid in ids}
        first = assign_splits(ids, groups, sectors)
        self.assertEqual(first, assign_splits(ids, groups, sectors))
        self.assertEqual(first["C000"], first["C001"])
        self.assertTrue(set(first.values()) <= {"selection", "holdout"})

    def test_family_overlap_excludes_subdomains_not_unrelated_hosts(self):
        known = {"example.at", "other.at"}
        self.assertTrue(host_family_overlap("shop.example.at", known))
        self.assertTrue(host_family_overlap("example.at", known))
        self.assertFalse(host_family_overlap("notexample.at", known))

    def test_restored_groups_preserve_prior_splits_and_detect_conflict(self):
        ids = ["A", "B", "C", "D", "E"]
        groups = {"A": "shared", "B": "other", "C": "shared", "D": "new", "E": "new"}
        sectors = {cid: "C" for cid in ids}
        previous = {"A": "selection", "B": "holdout"}
        result = extend_splits(ids, groups, sectors, previous)
        self.assertEqual(result, extend_splits(ids, groups, sectors, previous))
        self.assertEqual(result["A"], "selection")
        self.assertEqual(result["B"], "holdout")
        self.assertEqual(result["C"], "selection")
        self.assertEqual(result["D"], result["E"])
        groups["B"] = "shared"
        with self.assertRaisesRegex(ValueError, "opposite frozen splits"):
            extend_splits(ids, groups, sectors, previous)

    def test_batch_files_are_bounded_and_exact(self):
        records = [{"estimated_input_tokens": 5_000_000,
                    "batch_line": {"custom_id": f"r{i}", "method": "POST",
                                   "url": "/v1/responses", "body": {"model": "gpt-6-luna", "input": "x"}}}
                   for i in range(5)]
        with tempfile.TemporaryDirectory() as directory:
            files = pack_files(records, Path(directory))
            self.assertEqual(len(files), 3)
            self.assertEqual([f["requests"] for f in files], [2, 2, 1])
            for item in files:
                path = Path(item["path"])
                self.assertEqual(path.stat().st_size, item["bytes"])
                self.assertLessEqual(item["bytes"], MAX_FILE_BYTES)
                self.assertLessEqual(item["requests"], MAX_REQUESTS)
                self.assertEqual(len(path.read_text().splitlines()), item["requests"])
            self.assertEqual(files, pack_files(records, Path(directory)))
            Path(files[0]["path"]).write_text("changed\n")
            with self.assertRaisesRegex(ValueError, "Incompatible existing Batch file"):
                pack_files(records, Path(directory))

    def test_atomic_checkpoint_can_be_read_on_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "company.json"
            first = {"identity_hash": "h1", "company_id": "C1"}
            atomic_json(path, first)
            self.assertEqual(json.loads(path.read_text()), first)
            self.assertFalse(path.with_suffix(".json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
