"""Offline safeguards for finalized AI-profile Batch operations."""
from __future__ import annotations

import json
import fcntl
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ai_ecosystem.calibration import run_ai_profile_validation_batch as runner


class FakeBatches:
    def __init__(self):
        self.remote = []
        self.creates = 0
        self.fail_create = False

    def list(self, **kwargs):
        return SimpleNamespace(data=self.remote, has_more=False)

    def create(self, **kwargs):
        self.creates += 1
        if self.fail_create:
            raise ConnectionError("ambiguous timeout")
        row = {"id": f"batch-{self.creates}", "status": "validating",
               "input_file_id": kwargs["input_file_id"], "metadata": kwargs["metadata"]}
        self.remote.append(row)
        return row

    def retrieve(self, batch_id):
        return next(row for row in self.remote if row["id"] == batch_id)


class FakeFiles:
    def __init__(self):
        self.uploads = 0
        self.contents = {}

    def create(self, **kwargs):
        self.uploads += 1
        return {"id": f"file-{self.uploads}"}

    def content(self, file_id):
        return SimpleNamespace(text=self.contents[file_id])


class BatchWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.root = self.directory
        self.identity = {"frozen": "identity"}
        runner.atomic_json(self.directory / "preparation_identity.json", self.identity)
        runner.atomic_json(self.directory / "config_snapshot.json",
                           {"contract_id": "company_ai_profile_v4", "mapping_version": "company_ai_profile_mapping_v4"})
        runner.atomic_json(self.directory / "schema_snapshot.json", {})
        self.lines = []
        self.records = {}
        for cid, split in (("A", "selection"), ("B", "holdout")):
            body = {"input": [{"role": "user", "content": json.dumps({"company_id": cid, "pages": []})}]}
            line = {"custom_id": f"request-{cid}", "method": "POST", "url": "/v1/responses", "body": body}
            self.lines.append(line)
            self.records[cid] = {"company_id": cid, "split": split,
                                 "request_hash": runner.digest(body), "batch_line": line,
                                 "estimated_input_tokens": 100}
        self.name = "batch_0001.jsonl"
        (self.directory / self.name).write_text("\n".join(json.dumps(x) for x in self.lines) + "\n", encoding="utf-8")
        self.file = {"sha256": runner.file_hash(self.directory / self.name),
                     "estimated_input_tokens": 200}
        self.summary = {"queue_headroom_tokens": 240, "included": 2,
                        "quote_validation_version": "quote_edge_whitespace_v2"}
        self.client = SimpleNamespace(batches=FakeBatches(), files=FakeFiles())
        self.validation = patch.object(runner, "validate_run", return_value=(self.summary, self.records,
                                                                              {self.name: self.file}))
        self.validation.start()
        self.addCleanup(self.validation.stop)

    def test_submit_refuses_duplicate_and_other_active_batch(self):
        self.client.batches.remote.append({"id": "unrelated", "status": "in_progress", "metadata": None})
        with self.assertRaisesRegex(RuntimeError, "Other active"):
            runner.submit(self.root, self.directory, confirm_paid=True, client=self.client)
        self.client.batches.remote.clear()
        submitted = runner.submit(self.root, self.directory, confirm_paid=True, client=self.client)
        self.assertEqual(len(submitted), 1)
        with self.assertRaises(FileExistsError):
            runner.submit(self.root, self.directory, confirm_paid=True, client=self.client)
        self.assertEqual(self.client.batches.creates, 1)

    def test_concurrent_local_submit_is_refused(self):
        (self.directory / "submission").mkdir()
        with (self.directory / "submission" / ".lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError, "Another local submission"):
                runner.submit(self.root, self.directory, confirm_paid=True, client=self.client)
        self.assertEqual(self.client.files.uploads, 0)

    def test_ambiguous_submission_requires_reconciliation(self):
        self.client.batches.fail_create = True
        with self.assertRaises(ConnectionError):
            runner.submit(self.root, self.directory, confirm_paid=True, client=self.client)
        self.assertEqual(json.loads(runner.state_path(self.directory, self.name).read_text())["phase"], "submitting")
        with self.assertRaisesRegex(RuntimeError, "reconcile"):
            runner.submit(self.root, self.directory, confirm_paid=True, client=self.client)
        metadata = json.loads(runner.state_path(self.directory, self.name).read_text())["metadata"]
        self.client.batches.remote.append({"id": "batch-recovered", "status": "in_progress",
                                           "input_file_id": "file-1", "metadata": metadata})
        state = runner.reconcile(self.root, self.directory, self.name, client=self.client)
        self.assertEqual(state["batch_id"], "batch-recovered")
        self.assertEqual(self.client.batches.creates, 1)

    def test_absent_remote_requires_explicit_confirmation_before_retry(self):
        self.client.batches.fail_create = True
        with self.assertRaises(ConnectionError):
            runner.submit(self.root, self.directory, confirm_paid=True, client=self.client)
        with self.assertRaisesRegex(RuntimeError, "confirm absence"):
            runner.reconcile(self.root, self.directory, self.name, client=self.client)
        runner.reconcile(self.root, self.directory, self.name, confirm_remote_absent=True,
                         client=self.client)
        self.client.batches.fail_create = False
        result = runner.submit(self.root, self.directory, confirm_paid=True, client=self.client)
        self.assertEqual(result[0]["batch_id"], "batch-2")
        self.assertEqual(self.client.files.uploads, 1)

    def test_ingest_rejects_unknown_id_and_hides_holdout_labels(self):
        runner.submit(self.root, self.directory, confirm_paid=True, client=self.client)
        batch = self.client.batches.remote[0]
        batch.update({"status": "completed", "output_file_id": "out-1", "error_file_id": None})
        self.client.files.contents["out-1"] = json.dumps({"custom_id": "wrong-id", "response": None}) + "\n"
        with self.assertRaisesRegex(ValueError, "Unknown or duplicate"):
            runner.ingest(self.root, self.directory, client=self.client)
        raw_path = self.directory / "raw_results" / f"{self.name}.output.jsonl"
        raw_path.unlink()
        self.client.files.contents["out-1"] = "\n".join(json.dumps({
            "custom_id": line["custom_id"], "response": {"status_code": 200,
                "body": {"status": "completed", "id": line["custom_id"]}}}) for line in self.lines) + "\n"
        def parsed(raw, package, schema, **kwargs):
            return {"status": "valid", "profile": {}, "derived": {
                "label": "KEEP" if package["company_id"] == "A" else "EXCLUDE"}}
        with patch.object(runner, "parse_response", side_effect=parsed):
            summary = runner.ingest(self.root, self.directory, client=self.client)
            repeated = runner.ingest(self.root, self.directory, client=self.client)
        self.assertEqual(summary, repeated)
        self.assertEqual(summary["selection_valid_labels"], {"KEEP": 1})
        self.assertEqual(summary["holdout_ingested"], 1)
        self.assertNotIn("EXCLUDE", json.dumps(summary))
        self.assertTrue((self.directory / "holdout_results" / "B.json").exists())

    def test_api_error_and_missing_response_remain_unlabelled(self):
        runner.submit(self.root, self.directory, confirm_paid=True, client=self.client)
        batch = self.client.batches.remote[0]
        batch.update({"status": "expired", "output_file_id": None, "error_file_id": "errors-1"})
        self.client.files.contents["errors-1"] = json.dumps({
            "custom_id": "request-A", "response": None,
            "error": {"code": "batch_expired", "message": "not executed"}}) + "\n"
        summary = runner.ingest(self.root, self.directory, client=self.client)
        self.assertEqual(summary["selection_statuses"], {"api_failure": 1})
        self.assertEqual(summary["selection_valid_labels"], {})
        self.assertIsNone(json.loads((self.directory / "selection_results" / "A.json").read_text())["derived"]["label"])
        self.assertIsNone(json.loads((self.directory / "holdout_results" / "B.json").read_text())["derived"]["label"])


if __name__ == "__main__":
    unittest.main()
