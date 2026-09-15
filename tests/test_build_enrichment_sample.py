from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ai_ecosystem.calibration.build_company_evidence_packages import select_companies as select_package_companies
from ai_ecosystem.calibration.build_enrichment_sample import build_enrichment_sample
from ai_ecosystem.calibration.enrichment_config import load_enrichment_config
from ai_ecosystem.db.init_db import init_database
from ai_ecosystem.discovery.discovery_utils import get_sample_company_ids, get_sample_domains


TEST_ENRICHMENT_CONFIG = """
sampling_id: onace_ai_enrichment
sampling_version: v1
taxonomy_id: oenace_2008_ai_enrichment
taxonomy_version: v1
taxonomy_source:
  authority: Statistik Austria
  title: ÖNACE 2008
  url: https://example.test/oenace
  source_field: ONACE 2008 Haupttätigkeit 4-stellig - Code
default_sample_id: ai_enriched_v1
default_sample_name: AI-Enriched Calibration v1
default_target_size: 6
default_seed: 42
default_exclude_samples:
  - pilot_v0_2
default_output_csv: data/interim/ai_enriched_calibration_v1.csv
eligibility:
  require_normalized_domain: true
  exclude_marked_domains: true
strata:
  HIGH_POTENTIAL:
    target_size: 3
    per_code_cap: 2
    rationale: high
    codes:
      - code: J620
        description: IT services
        rationale: high IT
      - code: J631
        description: Data processing
        rationale: high data
  AI_ADJACENT:
    target_size: 2
    per_code_cap: 1
    rationale: adjacent
    codes:
      - code: M711
        description: Engineering
        rationale: engineering
      - code: M712
        description: Testing
        rationale: testing
  CONVENTIONAL_CONTROL:
    target_size: 1
    per_code_cap: 1
    rationale: control
    codes:
      - code: I551
        description: Hotels
        rationale: accommodation
      - code: I561
        description: Restaurants
        rationale: food service
"""


class EnrichmentSampleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        (self.root / "config").mkdir()
        (self.root / "data" / "interim").mkdir(parents=True)
        (self.root / "config" / "config.yaml").write_text(
            "database:\n  path: db/test.sqlite\n", encoding="utf-8"
        )
        (self.root / "config" / "calibration_enrichment.yaml").write_text(
            TEST_ENRICHMENT_CONFIG, encoding="utf-8"
        )
        self.db_path = init_database(self.root)
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute(
                """
                INSERT INTO sample_sets (sample_id, sample_name, sample_size, created_at)
                VALUES ('pilot_v0_2', 'Pilot', 1, 'now')
                """
            )
            connection.execute(
                "INSERT INTO sample_domains (sample_id, domain, sample_rank) VALUES ('pilot_v0_2', 'pilot.example', 1)"
            )
            self._insert_candidates(connection)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    @staticmethod
    def _insert_firm(
        connection: sqlite3.Connection,
        company_id: str,
        code: str,
        domain: str | None,
        *,
        firm_name: str | None = None,
        excluded: bool = False,
        firm_count: int = 1,
        representation_level: str = "single_firm",
    ) -> None:
        if domain:
            connection.execute(
                """
                INSERT OR IGNORE INTO domains (
                    domain, firm_count, representation_level, domain_category, is_excluded
                ) VALUES (?, ?, ?, 'company_or_group', ?)
                """,
                (domain, firm_count, representation_level, int(excluded)),
            )
        connection.execute(
            """
            INSERT INTO firms (bvd_id, firm_name, nace_code, normalized_domain)
            VALUES (?, ?, ?, ?)
            """,
            (company_id, firm_name or company_id, code, domain),
        )

    def _insert_candidates(self, connection: sqlite3.Connection) -> None:
        for index in range(4):
            self._insert_firm(connection, f"HIGHIT{index}", "J620", f"high-it-{index}.example")
        self._insert_firm(connection, "HIGHDATA1", "J631", "shared.example", firm_count=2, representation_level="shared_domain")
        self._insert_firm(connection, "HIGHDATA2", "J631", "shared.example", firm_count=2, representation_level="shared_domain")
        for index in range(2):
            self._insert_firm(connection, f"ADJENG{index}", "M711", f"adj-eng-{index}.example")
            self._insert_firm(connection, f"ADJTEST{index}", "M712", f"adj-test-{index}.example")
        self._insert_firm(connection, "CONTROLHOTEL", "I551", "hotel.example")
        self._insert_firm(connection, "CONTROLFOOD", "I561", "food.example")
        self._insert_firm(connection, "PILOTFIRM", "J620", "pilot.example")
        self._insert_firm(connection, "NODOMAIN", "J620", None)
        self._insert_firm(connection, "EXCLUDED", "J620", "facebook.example", excluded=True)

    def _build(self, **kwargs: object) -> dict[str, object]:
        return build_enrichment_sample(
            project_root=self.root,
            output_csv="data/interim/enrichment.csv",
            **kwargs,
        )

    def test_config_loads_and_validates_exact_strata(self) -> None:
        config = load_enrichment_config(self.root)
        self.assertEqual(config.default_target_size, 6)
        self.assertEqual([stratum.name for stratum in config.strata], ["HIGH_POTENTIAL", "AI_ADJACENT", "CONVENTIONAL_CONTROL"])
        self.assertEqual(config.codes_by_value["J620"][0].name, "HIGH_POTENTIAL")

        invalid = (self.root / "config" / "calibration_enrichment.yaml")
        invalid.write_text(TEST_ENRICHMENT_CONFIG.replace("- code: J631", "- code: J620"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "configured more than once"):
            load_enrichment_config(self.root)

    def test_builds_deterministic_company_sample_and_domain_projection(self) -> None:
        first = self._build(sample_id="enrichment_a")
        self.assertEqual(first["selected_company_count"], 6)
        self.assertEqual(first["target_shortfall"], 0)
        self.assertEqual(first["strata"]["HIGH_POTENTIAL"]["selected_count"], 3)
        self.assertEqual(first["strata"]["AI_ADJACENT"]["selected_count"], 2)
        self.assertEqual(first["strata"]["CONVENTIONAL_CONTROL"]["selected_count"], 1)
        self.assertEqual(first["eligibility_exclusions"]["existing_sample_overlap"], 1)
        self.assertEqual(first["eligibility_exclusions"]["missing_normalized_domain"], 1)
        self.assertEqual(first["eligibility_exclusions"]["marked_excluded_domain"], 1)
        self.assertEqual(first["firm_size"]["available"], False)

        with sqlite3.connect(self.db_path) as connection:
            selected_rows = connection.execute(
                """
                SELECT company_id, sample_rank, enrichment_stratum, normalized_domain
                FROM sample_companies WHERE sample_id = 'enrichment_a' ORDER BY sample_rank
                """
            ).fetchall()
            sample_set = connection.execute(
                """
                SELECT sample_size, sample_unit, domain_count, config_id, config_version, config_hash
                FROM sample_sets WHERE sample_id = 'enrichment_a'
                """
            ).fetchone()
            self.assertNotIn("PILOTFIRM", [row[0] for row in selected_rows])
            self.assertNotIn("NODOMAIN", [row[0] for row in selected_rows])
            self.assertEqual(len(selected_rows), len({row[0] for row in selected_rows}))
            self.assertEqual([row[1] for row in selected_rows], list(range(1, 7)))
            self.assertEqual(sample_set[0], 6)
            self.assertEqual(sample_set[1], "company")
            self.assertEqual(sample_set[2], len(get_sample_domains(connection, "enrichment_a")))
            self.assertEqual(sample_set[3:5], ("onace_ai_enrichment", "v1"))
            self.assertTrue(sample_set[5])
            self.assertEqual(get_sample_company_ids(connection, "enrichment_a"), [row[0] for row in selected_rows])

        output_path = self.root / "data" / "interim" / "enrichment.csv"
        audit_path = self.root / "data" / "interim" / "enrichment_audit.json"
        with output_path.open(encoding="utf-8", newline="") as file:
            rows = list(csv.DictReader(file))
        self.assertEqual(len(rows), 6)
        self.assertEqual(set(rows[0]), {
            "sample_id", "sample_rank", "company_id", "company_name", "nace_code", "nace_description",
            "enrichment_stratum", "normalized_domain", "domain_category", "representation_level",
            "shared_domain", "shared_domain_firm_count", "sampling_seed", "selection_reason",
        })
        self.assertTrue(audit_path.is_file())
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        self.assertEqual(audit["sample_id"], "enrichment_a")
        shared_domains = [item["domain"] for item in audit["domains"]["largest_selected_shared_domain_groups"]]
        self.assertEqual(len(shared_domains), len(set(shared_domains)))

        first_ids = [row["company_id"] for row in rows]
        second = self._build(sample_id="enrichment_a", force_refresh=True)
        with output_path.open(encoding="utf-8", newline="") as file:
            second_ids = [row["company_id"] for row in csv.DictReader(file)]
        self.assertEqual(first_ids, second_ids)
        self.assertEqual(second["selected_company_count"], 6)

    def test_existing_output_requires_force_refresh(self) -> None:
        self._build(sample_id="enrichment_b")
        with self.assertRaisesRegex(FileExistsError, "Output already exists"):
            self._build(sample_id="enrichment_c")

    def test_company_membership_prevents_shared_domain_expansion_in_packages(self) -> None:
        self._build(sample_id="enrichment_shared", target_size=7)
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            selected_ids = get_sample_company_ids(connection, "enrichment_shared")
            records = select_package_companies(connection, sample_id="enrichment_shared")
            selected_domains = get_sample_domains(connection, "enrichment_shared")
        self.assertEqual({record.company_id for record in records}, set(selected_ids))
        self.assertEqual(len(records), len(selected_ids))
        self.assertTrue({"HIGHDATA1", "HIGHDATA2"}.issubset(set(selected_ids)))
        self.assertLess(len(selected_domains), len(selected_ids))

    def test_existing_domain_sample_registry_migrates_to_company_membership_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            legacy_root = Path(temporary_directory)
            (legacy_root / "config").mkdir()
            (legacy_root / "db").mkdir()
            (legacy_root / "config" / "config.yaml").write_text(
                "database:\n  path: db/legacy.sqlite\n", encoding="utf-8"
            )
            legacy_path = legacy_root / "db" / "legacy.sqlite"
            with sqlite3.connect(legacy_path) as connection:
                connection.execute(
                    """
                    CREATE TABLE sample_sets (
                        sample_id TEXT PRIMARY KEY, sample_name TEXT NOT NULL,
                        sample_description TEXT, source_csv TEXT, sampling_strategy TEXT,
                        sampling_seed INTEGER, sample_size INTEGER, created_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE sample_domains (
                        sample_id TEXT NOT NULL, domain TEXT NOT NULL, sample_rank INTEGER,
                        UNIQUE(sample_id, domain)
                    )
                    """
                )
            init_database(legacy_root)
            with sqlite3.connect(legacy_path) as connection:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(sample_sets)")}
                company_columns = {row[1] for row in connection.execute("PRAGMA table_info(sample_companies)")}
            self.assertTrue({"sample_unit", "config_id", "config_version", "config_hash", "domain_count"}.issubset(columns))
            self.assertTrue({"sample_id", "company_id", "sample_rank", "enrichment_stratum"}.issubset(company_columns))

    def test_sample_build_does_not_create_pipeline_records(self) -> None:
        self._build(sample_id="enrichment_safe")
        with sqlite3.connect(self.db_path) as connection:
            for table in ("discovery_runs", "acquisition_runs", "extraction_runs", "scoring_runs", "calibration_runs"):
                self.assertEqual(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
