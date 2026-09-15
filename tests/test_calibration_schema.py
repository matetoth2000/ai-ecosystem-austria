from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from ai_ecosystem.db.init_db import init_database


class CalibrationSchemaTest(unittest.TestCase):
    def test_calibration_schema_is_created_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "config").mkdir(); (root / "config" / "config.yaml").write_text("database:\n  path: db/test.sqlite\n", encoding="utf-8")
            db_path = init_database(root)
            init_database(root)
            with sqlite3.connect(db_path) as connection:
                run_columns = {row[1] for row in connection.execute("PRAGMA table_info(calibration_runs)")}
                result_columns = {row[1] for row in connection.execute("PRAGMA table_info(calibration_results)")}
                indexes = {row[1] for row in connection.execute("PRAGMA index_list(calibration_results)")}
            self.assertTrue({"calibration_run_id", "prompt_hash", "reasoning_effort", "input_tokens", "reasoning_tokens"}.issubset(run_columns))
            self.assertTrue({"calibration_run_id", "company_id", "label", "lexical_signals_json", "raw_response_json", "status"}.issubset(result_columns))
            self.assertIn("idx_calibration_results_run_status", indexes)


if __name__ == "__main__":
    unittest.main()
