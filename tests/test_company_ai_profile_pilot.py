"""Offline contract and pilot-runner regression tests."""
from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ai_ecosystem.calibration.company_ai_profile_contract import (
    canonical, derive_label, digest, prompt_and_schema, quote_match_status, request_body, validate_profile,
)
from ai_ecosystem.calibration.company_ai_profile_packages import (
    DEFAULT_OUTPUT, _visible, ai_profile_page_sort_key, allocate_profile_candidates,
    choose_companies, enforce_exact_evidence_budget, is_product_service_solution_url,
    load_config, package_company, prepare_profile_candidates, tokens_upper_bound, utility_reason,
)
from ai_ecosystem.calibration.build_company_evidence_packages import calibration_page_sort_key
from ai_ecosystem.calibration.company_ai_profile_tokens import (
    count_payload, local_tokens, non_evidence_breakdown, token_breakdown,
)
from ai_ecosystem.calibration.run_company_ai_profile_pilot import (
    actual_cost, body_for, dry_run, export_batch, load_prepared, package_for, parse_response,
    prepare_rerun, quote_diagnostic, report, revalidate_saved, reservation, run, save_json,
)
from ai_ecosystem.db.init_db import find_project_root


def assessment(value, basis="none", status="not_applicable", evidence_ids=None):
    return {"value": value, "confidence": 3, "basis": basis, "status": status,
            "evidence_ids": evidence_ids or [], "reason": "fixture"}


def profile(active=False, adjacent=False, attributed=True, adequacy="sufficient"):
    return {"ai_active": assessment(active), "ai_adjacent": assessment(adjacent),
            "attribution": {"value": attributed, "confidence": 3, "evidence_ids": [], "reason": "fixture"},
            "evidence_adequacy": {"value": adequacy, "confidence": 3, "reason": "fixture"},
            "ai_summary": "", "roles": [], "technologies": [], "entities": [], "relationships": [],
            "evidence": [], "unmapped_findings": []}


def package():
    return {"company_id": "AT1", "company_name": "Example GmbH", "normalized_domain": "example.at",
            "shared_website": False, "coverage": {"available_unique_pages": 2, "included_pages": 2,
                "excluded_utility_pages": 0, "duplicate_page_records": 0, "omitted_pages": 0,
                "text_truncated_pages": 0},
            "pages": [{"page_id": "1", "url": "https://example.at/ai", "title": "AI", "text": "Example GmbH develops AI software.", "truncated": False},
                      {"page_id": "2", "url": "https://example.at/about", "title": "About", "text": "Example GmbH sells chairs.", "truncated": False}]}


def config():
    return {"model": "gpt-6-luna", "reasoning_effort": "low", "max_output_tokens": 8000,
            "max_context_tokens": 1050000, "pilot_spend_allowance_usd": 2.0,
            "website_evidence_token_cap": 250000,
            "pricing": {"input_per_million": .1, "cached_input_per_million": .01,
                        "output_per_million": .5, "long_context_input_threshold": 272000,
                        "long_context_input_multiplier": 2, "long_context_output_multiplier": 1.5}}


def evidence():
    return {"id": "q1", "page_id": "1", "quote": "Example GmbH develops AI software."}


def response(value, status="completed"):
    return {"id": "resp_fixture", "model": "gpt-6-luna", "status": status,
            "output": [{"content": [{"type": "output_text", "text": json.dumps(value)}]}],
            "usage": {"input_tokens": 100, "output_tokens": 50, "input_tokens_details": {"cached_tokens": 10},
                      "output_tokens_details": {"reasoning_tokens": 20}}}


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prompt, cls.schema = prompt_and_schema()

    def test_explicit_ai_and_inferred_adjacency(self):
        p = profile()
        p["ai_active"] = assessment(True, "explicit", "operational", ["q1"])
        p["ai_summary"] = "AI software."
        p["evidence"] = [evidence()]
        valid = validate_profile(p, package(), self.schema)
        self.assertEqual(derive_label(valid)["label"], "KEEP")
        p = profile(active=None)
        p["ai_adjacent"] = assessment(True, "inferred_compute", "operational", ["q1"])
        p["ai_summary"] = "Compute."
        p["evidence"] = [evidence()]
        valid = validate_profile(p, package(), self.schema)
        self.assertTrue(derive_label(valid)["inferred_adjacency_only"])

    def test_negative_unknown_and_unmapped(self):
        p = validate_profile(profile(), package(), self.schema)
        self.assertEqual(derive_label(p)["label"], "EXCLUDE")
        self.assertEqual(derive_label(profile(attributed=None))["label"], "UNKNOWN")
        p = profile(active=None)
        p["evidence"] = [evidence()]
        p["unmapped_findings"] = [{"kind": "role", "description": "Unresolved", "source_name": None,
            "target_name": None, "affects_eligibility": True, "confidence": 2, "evidence_ids": ["q1"]}]
        self.assertEqual(derive_label(validate_profile(p, package(), self.schema))["label"], "UNKNOWN")

    def test_planned_or_historical_only(self):
        for status in ("planned_only", "historical_only"):
            p = profile()
            p["ai_active"]["status"] = status
            self.assertEqual(derive_label(validate_profile(p, package(), self.schema))["label"], "EXCLUDE")
            p["ai_active"] = assessment(None, status=status)
            self.assertEqual(derive_label(validate_profile(p, package(), self.schema))["label"], "UNKNOWN")

    def test_exact_quote_and_page_binding(self):
        p = profile()
        p["ai_active"] = assessment(True, "explicit", "operational", ["q1"])
        p["ai_summary"] = "AI software."
        p["evidence"] = [evidence()]
        for bad in ({**evidence(), "page_id": "2"},
                    {**evidence(), "quote": "Example GmbH developed AI software."}):
            p["evidence"] = [bad]
            with self.assertRaisesRegex(ValueError, "ungrounded_quote"):
                validate_profile(p, package(), self.schema)
        p["evidence"] = [evidence()]
        p["ai_active"]["evidence_ids"] = ["missing"]
        with self.assertRaisesRegex(ValueError, "evidence_reference"):
            validate_profile(p, package(), self.schema)

    def test_quote_diagnostics_do_not_relax_validation(self):
        pkg = package()
        pkg["pages"][0]["text"] = "First line\nsecond line."
        wrong_page = {"id": "q1", "page_id": "2", "quote": "First line\nsecond line."}
        whitespace = {"id": "q1", "page_id": "1", "quote": "First line second line."}
        self.assertEqual(quote_diagnostic(pkg, wrong_page), ("WRONG_PAGE_ID", ["1"]))
        self.assertEqual(quote_diagnostic(pkg, whitespace), ("WHITESPACE_ONLY_MISMATCH", []))
        self.assertEqual(quote_diagnostic(pkg, {**whitespace, "quote": "Not present"}),
                         ("MISSING_QUOTE", []))
        self.assertEqual(quote_diagnostic(pkg, {**wrong_page, "page_id": "1"}), ("PASS_EXACT", []))
        item = profile()
        item["ai_active"] = assessment(True, "explicit", "operational", ["q1"])
        item["ai_summary"] = "AI activity."
        for invalid in (wrong_page, whitespace):
            item["evidence"] = [invalid]
            with self.assertRaisesRegex(ValueError, "ungrounded_quote"):
                validate_profile(item, pkg, self.schema)

    def test_only_edge_whitespace_is_accepted_without_mutating_quote(self):
        pkg = package()
        pkg["pages"][0]["text"] = "Source line\nbreak and AI."
        item = profile()
        item["ai_active"] = assessment(True, "explicit", "operational", ["q1"])
        item["ai_summary"] = "AI."
        original = "\u00a0Source line\nbreak and AI.\u00a0"
        item["evidence"] = [{"id": "q1", "page_id": "1", "quote": original}]
        self.assertEqual(quote_match_status(original, pkg["pages"][0]["text"]),
                         "PASS_EDGE_WHITESPACE_TRIMMED")
        self.assertEqual(quote_diagnostic(pkg, item["evidence"][0]),
                         ("PASS_EDGE_WHITESPACE_TRIMMED", []))
        self.assertEqual(validate_profile(item, pkg, self.schema)["evidence"][0]["quote"], original)
        for bad in ("\u00a0 \t", "Source line break and AI.", "Source line\nbreak and AI. revised"):
            item["evidence"][0]["quote"] = bad
            with self.assertRaisesRegex(ValueError, "ungrounded_quote"):
                validate_profile(item, pkg, self.schema)
        item["evidence"][0] = {"id": "q1", "page_id": "2", "quote": original}
        with self.assertRaisesRegex(ValueError, "ungrounded_quote"):
            validate_profile(item, pkg, self.schema)

    def test_graph_endpoints_and_symmetric_dedup(self):
        p = profile()
        p["ai_active"] = assessment(True, "explicit", "operational", ["q1"])
        p["ai_summary"] = "AI software."
        p["evidence"] = [evidence()]
        p["entities"] = [{"id": "e1", "name": "Partner", "type": "organization",
                          "confidence": 3, "evidence_ids": ["q1"]}]
        edge = {"source": "e1", "type": "partners_with", "target": "focal_company",
                "ai_context": "general_ecosystem", "status": "current", "date_text": None,
                "context": "Joint work", "confidence": 3, "evidence_ids": ["q1"]}
        p["relationships"] = [edge, {**edge, "source": "focal_company", "target": "e1"}]
        valid = validate_profile(p, package(), self.schema)
        self.assertEqual(len(valid["relationships"]), 1)
        self.assertEqual(valid["relationships"][0]["source"], "focal_company")
        p["relationships"] = [{**edge, "target": "e99"}]
        with self.assertRaisesRegex(ValueError, "invalid_relationship_endpoint"):
            validate_profile(p, package(), self.schema)

    def test_technical_failures_do_not_receive_labels(self):
        for status in ("no_evidence", "api_failure", "refusal", "invalid_schema", "failed_exact_quote"):
            self.assertIsNone(derive_label(None, status=status)["label"])
        raw = response(profile(), status="incomplete")
        self.assertEqual(parse_response(raw, package(), self.schema)["status"], "incomplete_output")
        raw = response(profile())
        raw["output"] = [{"content": [{"type": "refusal", "refusal": "No"}]}]
        self.assertEqual(parse_response(raw, package(), self.schema)["status"], "refusal")

    def test_request_body_is_batch_body_and_blind(self):
        body = request_body(package(), config(), self.prompt, self.schema)
        line = {"custom_id": "test", "method": "POST", "url": "/v1/responses", "body": body}
        self.assertEqual(line["body"], body)
        self.assertNotIn("KEEP", json.dumps(body))
        self.assertEqual(body["text"]["format"]["strict"], True)

    def test_spend_uses_conservative_output_reservation(self):
        self.assertGreater(reservation(config(), 250000), actual_cost(config(), response(profile())["usage"]))
        self.assertEqual(tokens_upper_bound("ä"), 2)


class RevisedContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prompt, cls.schema = prompt_and_schema("company_ai_profile_v2")

    def validate(self, item, evidence_package):
        return validate_profile(item, evidence_package, self.schema, contract_id="company_ai_profile_v2")

    def test_trading_name_and_ordinary_redirect_are_not_disqualifying(self):
        for url in ("https://brand.at/", "https://www.brand.at/redirected"):
            with self.subTest(url=url):
                pkg = package()
                pkg["normalized_domain"] = "brand.at"
                pkg["pages"] = [{"page_id": "1", "url": url, "title": "Brand",
                                 "text": "We at Brand offer an AI planning service.", "truncated": False}]
                item = profile()
                item["ai_active"] = assessment(True, "explicit", "operational", ["q1"])
                item["attribution"]["evidence_ids"] = ["q1"]
                item["evidence"] = [{"id": "q1", "page_id": "1",
                                     "quote": "We at Brand offer an AI planning service."}]
                item["ai_summary"] = "Brand offers an AI planning service."
                self.assertEqual(derive_label(self.validate(item, pkg),
                    mapping_version="company_ai_profile_mapping_v2")["label"], "KEEP")
                item["attribution"]["evidence_ids"] = []
                with self.assertRaisesRegex(ValueError, "positive_assessment_requires_focal"):
                    self.validate(item, pkg)

    def test_different_entity_and_unconnected_shared_group_remain_unknown(self):
        for text in ("Imprint: Different Legal Entity GmbH. Group offers AI tools.",
                     "Group offers AI tools, but no subsidiary is named."):
            with self.subTest(text=text):
                pkg = package()
                pkg["shared_website"] = True
                pkg["pages"][0]["text"] = text
                item = profile(active=None, attributed=None, adequacy="unclear")
                item["evidence"] = [{"id": "q1", "page_id": "1", "quote": text}]
                item["ai_active"]["evidence_ids"] = ["q1"]
                item["attribution"]["evidence_ids"] = ["q1"]
                self.assertEqual(item["ai_active"]["basis"], "none")
                self.assertEqual(derive_label(self.validate(item, pkg),
                    mapping_version="company_ai_profile_mapping_v2")["label"], "UNKNOWN")
                item["ai_active"] = assessment(True, "explicit", "operational", ["q1"])
                item["ai_summary"] = "Group AI activity."
                with self.assertRaisesRegex(ValueError, "positive_assessment_requires_focal"):
                    self.validate(item, pkg)

    def test_informative_non_ai_and_third_party_mention(self):
        item = profile()
        self.assertEqual(derive_label(self.validate(item, package()),
            mapping_version="company_ai_profile_mapping_v2")["label"], "EXCLUDE")
        item["evidence"] = [evidence()]
        item["unmapped_findings"] = [{"kind": "role", "description": "Unresolved focal activity",
            "source_name": None, "target_name": None, "affects_eligibility": True,
            "confidence": 3, "evidence_ids": ["q1"]}]
        self.assertEqual(derive_label(self.validate(item, package()),
            mapping_version="company_ai_profile_mapping_v2")["label"], "UNKNOWN")
        pkg = package()
        item = profile(active=None, attributed=None, adequacy="unclear")
        pkg["pages"][0]["text"] = "Another Organization develops AI software."
        item["ai_active"] = assessment(None, "none", "unclear", ["q1"])
        item["evidence"] = [{"id": "q1", "page_id": "1", "quote": "Another Organization develops AI software."}]
        self.validate(item, pkg)
        self.assertEqual(item["ai_active"]["basis"], "none")
        item["ai_active"]["basis"] = "explicit"
        with self.assertRaisesRegex(ValueError, "invalid_nonpositive_basis"):
            self.validate(item, pkg)

    def test_hotel_mirror_uses_adopter_and_unspecified_ai(self):
        pkg = package()
        pkg["pages"][0]["text"] = "Our hotel offers guests an interactive KI fitness mirror."
        item = profile()
        item["ai_active"] = assessment(True, "explicit", "operational", ["q1"])
        item["attribution"]["evidence_ids"] = ["q1"]
        item["evidence"] = [{"id": "q1", "page_id": "1",
                             "quote": "Our hotel offers guests an interactive KI fitness mirror."}]
        item["ai_summary"] = "The hotel offers an AI-enabled mirror."
        item["roles"] = [{"role": "adopter", "status": "operational", "confidence": 4,
                          "evidence_ids": ["q1"]}]
        item["technologies"] = [{"category": "unspecified_ai", "term": "KI fitness mirror",
                                 "context": "uses", "confidence": 4, "evidence_ids": ["q1"]}]
        self.assertEqual(derive_label(self.validate(item, pkg),
            mapping_version="company_ai_profile_mapping_v2")["label"], "KEEP")
        self.assertIn("Making a third-party AI-enabled device", self.prompt)
        self.assertIn('Generic "AI" or "KI" does NOT establish machine learning', self.prompt)


class ScopedContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prompt, cls.schema = prompt_and_schema("company_ai_profile_v3")

    @staticmethod
    def scoped_profile():
        item = profile()
        item["ai_active"]["attribution_scope"] = None
        item["ai_adjacent"]["attribution_scope"] = None
        return item

    @staticmethod
    def scoped_package():
        pkg = package()
        pkg["pages"][0]["text"] = (
            "The Tennengau branch belongs to Maschinenring. "
            "Maschinenring uses AI in scheduling nationwide. "
            "The group supplies AI to Client AG. "
            "Another Region alone uses AI in procurement. "
            "Hospital St. Josef uses AI for diagnosis.")
        return pkg

    def validate(self, item, pkg=None):
        return validate_profile(item, pkg or self.scoped_package(), self.schema,
                                contract_id="company_ai_profile_v3")

    def test_website_business_alias_and_scope_export(self):
        item = self.scoped_profile()
        item["ai_active"] = assessment(True, "explicit", "operational", ["q1"])
        item["ai_active"]["attribution_scope"] = "website_business"
        item["ai_summary"] = "The website business uses AI."
        item["attribution"]["evidence_ids"] = ["q1"]
        pkg = package()
        pkg["company_name"] = "Different legal record name"
        pkg["pages"][0]["text"] = "We at Brand use AI to schedule work."
        item["evidence"] = [{"id": "q1", "page_id": "1", "quote": pkg["pages"][0]["text"]}]
        result = derive_label(self.validate(item, pkg), mapping_version="company_ai_profile_mapping_v3")
        self.assertEqual(result["label"], "KEEP")
        self.assertEqual(result["attribution_scopes"]["ai_active"], "website_business")
        self.assertFalse(result["group_attributed"])
        parsed = parse_response(response(item), pkg, self.schema,
                                contract_id="company_ai_profile_v3",
                                mapping_version="company_ai_profile_mapping_v3")
        self.assertEqual(parsed["derived"]["attribution_scopes"]["ai_active"], "website_business")
        self.assertEqual(parsed["profile"]["ai_active"]["attribution_scope"], "website_business")
        self.assertIn("NOT KEEP/UNKNOWN/EXCLUDE labels", self.prompt)

    def test_distinct_smaller_business_does_not_inherit_hospital_ai(self):
        item = self.scoped_profile()
        item["evidence"] = [{"id": "hospital", "page_id": "1",
                             "quote": "Hospital St. Josef uses AI for diagnosis."}]
        item["ai_active"]["evidence_ids"] = ["hospital"]
        item["ai_active"]["reason"] = "Hospital activity, not the separately identified car park."
        self.assertEqual(derive_label(self.validate(item),
            mapping_version="company_ai_profile_mapping_v3")["label"], "EXCLUDE")
        item["ai_active"]["attribution_scope"] = "focal_unit"
        with self.assertRaisesRegex(ValueError, "invalid_nonpositive_attribution_scope"):
            self.validate(item)

    def test_regional_group_scope_and_other_region_only(self):
        item = self.scoped_profile()
        item["evidence"] = [
            {"id": "member", "page_id": "1", "quote": "The Tennengau branch belongs to Maschinenring."},
            {"id": "activity", "page_id": "1", "quote": "Maschinenring uses AI in scheduling nationwide."},
        ]
        item["attribution"]["evidence_ids"] = ["member"]
        item["ai_active"] = assessment(True, "explicit", "operational", ["member", "activity"])
        item["ai_active"]["attribution_scope"] = "group"
        item["ai_summary"] = "Group-wide AI use; regional use not directly demonstrated."
        result = derive_label(self.validate(item), mapping_version="company_ai_profile_mapping_v3")
        self.assertEqual(result["label"], "KEEP")
        self.assertTrue(result["group_attributed"])
        item["ai_active"]["evidence_ids"] = ["activity"]
        with self.assertRaisesRegex(ValueError, "group_scope_requires_membership_reference"):
            self.validate(item)
        item["ai_active"] = assessment(None, "none", "unclear", ["activity"])
        item["ai_active"]["attribution_scope"] = None
        item["ai_summary"] = ""
        item["evidence"][1]["quote"] = "Another Region alone uses AI in procurement."
        self.assertEqual(derive_label(self.validate(item),
            mapping_version="company_ai_profile_mapping_v3")["label"], "UNKNOWN")

    def test_group_edge_keeps_group_endpoint_and_requires_membership(self):
        item = self.scoped_profile()
        item["evidence"] = [
            {"id": "member", "page_id": "1", "quote": "The Tennengau branch belongs to Maschinenring."},
            {"id": "activity", "page_id": "1", "quote": "Maschinenring uses AI in scheduling nationwide."},
            {"id": "edge", "page_id": "1", "quote": "The group supplies AI to Client AG."},
        ]
        item["attribution"]["evidence_ids"] = ["member"]
        item["ai_active"] = assessment(True, "explicit", "operational", ["member", "activity"])
        item["ai_active"]["attribution_scope"] = "group"
        item["ai_summary"] = "Group AI activity."
        item["entities"] = [
            {"id": "e1", "name": "Maschinenring", "type": "group_organization",
             "confidence": 4, "evidence_ids": ["member"]},
            {"id": "e2", "name": "Client AG", "type": "organization",
             "confidence": 4, "evidence_ids": ["edge"]},
        ]
        item["relationships"] = [{"source": "e1", "type": "supplies_ai_to", "target": "e2",
            "ai_context": "explicit_ai", "status": "current", "date_text": None,
            "context": "Group supplies AI", "confidence": 4, "evidence_ids": ["edge"]}]
        valid = self.validate(item)
        self.assertEqual((valid["relationships"][0]["source"], valid["relationships"][0]["target"]),
                         ("e1", "e2"))
        item["entities"][0]["evidence_ids"] = ["activity"]
        with self.assertRaisesRegex(ValueError, "group_entity_requires_focal_membership_evidence"):
            self.validate(item)
        item["entities"][0]["evidence_ids"] = ["member"]
        item["relationships"][0]["source"] = "e2"
        item["relationships"][0]["target"] = "e3"
        item["entities"].append({"id": "e3", "name": "Unrelated", "type": "organization",
                                 "confidence": 4, "evidence_ids": ["edge"]})
        with self.assertRaisesRegex(ValueError, "invalid_relationship_endpoint"):
            self.validate(item)

    def test_schema_scope_and_legacy_contract_are_separate(self):
        item = self.scoped_profile()
        self.validate(item)
        root = find_project_root()
        old_config = load_config(root, root / "config/company_ai_profile_pilot_v3.yaml")
        new_config = load_config(root, root / "config/company_ai_profile_pilot_v4.yaml")
        for key in ("model", "reasoning_effort", "max_output_tokens", "website_evidence_token_cap"):
            self.assertEqual(old_config[key], new_config[key])
        self.assertEqual(request_body(package(), new_config, self.prompt, self.schema)["text"]["format"]["name"],
                         "company_ai_profile_v3")
        _, old_schema = prompt_and_schema("company_ai_profile_v2")
        with self.assertRaisesRegex(ValueError, "invalid_schema"):
            validate_profile(item, package(), old_schema, contract_id="company_ai_profile_v2")
        item["ai_active"].pop("attribution_scope")
        with self.assertRaisesRegex(ValueError, "invalid_schema"):
            self.validate(item)


class PackageTests(unittest.TestCase):
    def test_prepared_candidates_match_public_package_builder_at_multiple_caps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("AI tools and products. " * 80, encoding="utf-8")
            (root / "b.txt").write_text("About the company. " * 80, encoding="utf-8")
            records = [
                {"website_id": "x.at", "usable": True, "scraped_page_id": number,
                 "url": url, "final_url": url, "title": title, "text_path": path,
                 "content_id": path}
                for number, url, title, path in (
                    (1, "https://x.at/products", "Products", "a.txt"),
                    (2, "https://x.at/about", "About", "b.txt"),
                    (3, "https://x.at/product-copy", "Copy", "a.txt"))]
            website = {"website_id": "x.at", "mapped_company_count": 1}
            company = {"company_id": "C", "company_name": "C", "normalized_domain": "x.at"}
            for cap in (500, 1500, 10000):
                cfg = {"website_evidence_token_cap": cap, "utility_path_components": [],
                       "utility_exceptions": []}
                candidates, excluded = prepare_profile_candidates(list(records), root, cfg)
                prepared = allocate_profile_candidates(company, website, records, candidates, excluded, cfg)
                direct = package_company(company, {"websites": [website], "pages": records}, root, cfg)
                self.assertEqual(prepared, direct)

    def test_package_budget_gives_product_page_text_priority(self):
        cfg = {"website_evidence_token_cap": 1500, "utility_path_components": [], "utility_exceptions": []}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "generic.txt").write_text("N" * 1000, encoding="utf-8")
            (root / "product.txt").write_text("P" * 1000, encoding="utf-8")
            pages = [
                {"website_id": "x.at", "usable": True, "scraped_page_id": 1,
                 "url": "https://x.at/news", "final_url": "https://x.at/news", "title": "News",
                 "text_path": "generic.txt", "content_id": "generic"},
                {"website_id": "x.at", "usable": True, "scraped_page_id": 2,
                 "url": "https://x.at/produkte/tool", "final_url": "https://x.at/produkte/tool",
                 "title": "Tool", "text_path": "product.txt", "content_id": "product"},
            ]
            source = {"websites": [{"website_id": "x.at", "mapped_company_count": 1}], "pages": pages}
            result = package_company({"company_id": "C", "company_name": "C",
                                      "normalized_domain": "x.at"}, source, root, cfg)
            self.assertEqual([page["page_id"] for page in result["pages"]], ["2"])
            self.assertEqual(result["coverage"]["omitted_pages"], 1)

    def test_profile_url_ordering_keeps_root_about_then_product_before_other(self):
        urls = [
            (9, "https://example.at/other"),
            (8, "https://example.at/produkte/deep/item"),
            (7, "https://example.at/unternehmen"),
            (6, "https://example.at/"),
            (5, "https://example.at/services"),
            (4, "https://example.at/products"),
            (3, "https://example.at/other/deep"),
        ]
        rows = [{"scraped_page_id": number, "url": url} for number, url in urls]
        self.assertEqual([row["scraped_page_id"] for row in sorted(rows, key=ai_profile_page_sort_key)],
                         [6, 7, 4, 5, 8, 9, 3])
        self.assertEqual(calibration_page_sort_key(rows[1])[0], 2)
        self.assertEqual(calibration_page_sort_key(rows[0])[0], 2)

    def test_product_service_solution_matching_uses_complete_path_segments(self):
        for segment in ("products", "produkte", "services", "dienstleistungen", "leistungen",
                        "solutions", "loesungen", "l%C3%B6sungen", "PRODUCT"):
            with self.subTest(segment=segment):
                self.assertTrue(is_product_service_solution_url(f"https://example.at/{segment}/detail?x=1#part"))
        for path in ("productivity", "our-products", "myservices", "leistungen-old", "blog?topic=products"):
            with self.subTest(path=path):
                self.assertFalse(is_product_service_solution_url(f"https://example.at/{path}"))
        root = {"scraped_page_id": 4, "url": "https://example.at/?q=services#x"}
        self.assertEqual(ai_profile_page_sort_key(root)[0], 0)

    def test_profile_order_uses_final_url_and_page_id_tie_breaker(self):
        rows = [
            {"scraped_page_id": 12, "url": "https://example.at/old", "final_url": "https://example.at/solutions"},
            {"scraped_page_id": 8, "url": "https://example.at/services?q=1#x"},
            {"scraped_page_id": 5, "url": "https://example.at/produkte"},
        ]
        self.assertEqual([row["scraped_page_id"] for row in sorted(rows, key=ai_profile_page_sort_key)],
                         [5, 8, 12])

    def test_prompt_schema_framing_allowances_are_separate_and_additive(self):
        cfg = config()
        prompt, schema = prompt_and_schema()
        body = request_body(package(), cfg, prompt, schema)
        client = Mock()
        client.responses.input_tokens.count.side_effect = lambda **kwargs: SimpleNamespace(
            input_tokens=7 + len(kwargs["input"][0]["content"]) +
                         (11 if "text" in kwargs else 0) + len(kwargs["input"][1]["content"]))
        result = non_evidence_breakdown(client, body)
        self.assertEqual(result["framing_input_tokens_exact"], 7)
        self.assertEqual(result["prompt_input_tokens_exact"], len(prompt))
        self.assertEqual(result["schema_input_tokens_exact"], 11)
        self.assertEqual(result["non_evidence_input_tokens_exact"], sum(
            result[key] for key in ("framing_input_tokens_exact", "prompt_input_tokens_exact",
                                    "schema_input_tokens_exact")))

    def test_local_allocation_counts_tokens_not_utf8_bytes(self):
        cfg = {"token_counting": {"allocation_encoding": "o200k_base"}}
        self.assertLess(local_tokens("Österreichische Unternehmen", cfg),
                        tokens_upper_bound("Österreichische Unternehmen"))

    def test_exact_count_budget_removes_page_and_keeps_request_consistent(self):
        pkg = package()
        cfg = config()
        cfg["token_counting"] = {"allocation_encoding": "o200k_base"}
        cfg["website_evidence_token_cap"] = len(json.dumps(_visible(pkg), ensure_ascii=False,
            sort_keys=True, separators=(",", ":"))) - 1
        pkg["audit"] = {"omitted": [], "token_estimator": "local_proxy"}
        client = Mock()
        client.responses.input_tokens.count.side_effect = lambda **kwargs: SimpleNamespace(
            input_tokens=100 + len(kwargs["input"][1]["content"]))
        prompt, schema = prompt_and_schema()
        original = request_body(pkg, cfg, prompt, schema)
        self.assertEqual(count_payload(original).keys(), {"model", "reasoning", "input", "text"})
        counts = enforce_exact_evidence_budget(pkg, cfg, prompt, schema, client, 100)
        self.assertEqual(len(pkg["pages"]), 1)
        self.assertEqual(pkg["coverage"]["omitted_pages"], 1)
        self.assertEqual(pkg["audit"]["omitted"][0]["page_id"], "2")
        self.assertLessEqual(counts["website_evidence_tokens_exact"], cfg["website_evidence_token_cap"])
        final = request_body(pkg, cfg, prompt, schema)
        self.assertEqual(counts, token_breakdown(client, final, 100))
        self.assertEqual(counts["website_evidence_tokens_exact"], pkg["audit"]["website_evidence_tokens_exact"])

    def test_utility_path_is_structural_and_exception_works(self):
        cfg = {"utility_path_components": ["privacy", "cart"], "utility_exceptions": []}
        self.assertEqual(utility_reason("https://x.at/privacy", cfg), "privacy")
        self.assertIsNone(utility_reason("https://x.at/news/privacy-in-ai", cfg))
        cfg["utility_exceptions"] = ["privacy"]
        self.assertIsNone(utility_reason("https://x.at/privacy", cfg))

    def test_group_selection_keeps_domains_unique(self):
        buckets = {k: [{"bvd_id": f"{k}-{i}", "company_name": "C", "normalized_domain": f"{k}-{i}.at"}
                       for i in range(5)] for k in ("both_top_100", "reference_only_top_100",
                            "challenger_only_top_100", "outside_both_top_100")}
        selected = choose_companies(buckets)
        self.assertEqual(len(selected), 10)
        self.assertEqual(len({x["normalized_domain"] for x in selected}), 10)

    def test_whole_page_continues_after_oversized_and_duplicate_provenance(self):
        cfg = {"website_evidence_token_cap": 1800, "utility_path_components": [], "utility_exceptions": []}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "long.txt").write_text("A" * 5000)
            (root / "short.txt").write_text("Small useful page")
            pages = []
            for ident, path, url in ((1, "long.txt", "https://x.at/"), (2, "short.txt", "https://x.at/about"),
                                     (3, "short.txt", "https://x.at/contact")):
                pages.append({"website_id": "x.at", "usable": True, "scraped_page_id": ident,
                              "final_url": url, "url": url, "title": "T", "text_path": path,
                              "content_id": path})
            source = {"websites": [{"website_id": "x.at", "mapped_company_count": 1}], "pages": pages}
            result = package_company({"company_id": "C", "company_name": "C", "normalized_domain": "x.at"}, source, root, cfg)
            self.assertEqual([p["page_id"] for p in result["pages"]], ["2"])
            self.assertEqual(len(result["audit"]["page_provenance"]["2"]), 2)
            self.assertEqual(result["coverage"]["omitted_pages"], 1)


class ResumeTests(unittest.TestCase):
    def test_offline_revalidation_and_numbered_report_preserve_originals(self):
        prompt, schema = prompt_and_schema()
        pkg = package()
        pkg["audit"] = {"website_evidence_tokens_exact": 100}
        item = profile()
        item["ai_active"] = assessment(True, "explicit", "operational", ["q1"])
        item["ai_summary"] = "AI software."
        item["evidence"] = [{"id": "q1", "page_id": "1",
                             "quote": "Example GmbH develops AI software.\u00a0"}]
        raw = response(item)
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            raw_path = directory / "raw_responses" / "AT1" / "0001.json"
            original_path = directory / "results" / "AT1.json"
            save_json(raw_path, raw)
            save_json(original_path, {"status": "failed_exact_quote", "request_hash": "request1",
                                      "raw_path": "raw_responses/AT1/0001.json"})
            original_bytes = original_path.read_bytes()
            manifest = [{"company_id": "AT1", "company_name": "Example GmbH",
                         "normalized_domain": "example.at", "selection_group": "both_top_100"}]
            prepared = (config() | {"contract_id": "company_ai_profile_v1",
                                    "mapping_version": "company_ai_profile_mapping_v1"},
                        prompt, schema, manifest, [{"company_id": "AT1", "request_hash": "request1"}])
            output = directory / "revalidation" / "quote_edge_whitespace_v2"
            with patch("ai_ecosystem.calibration.run_company_ai_profile_pilot.load_prepared",
                       return_value=prepared), patch(
                       "ai_ecosystem.calibration.run_company_ai_profile_pilot.package_for", return_value=pkg):
                summary = revalidate_saved(directory, directory, output)
                self.assertEqual(summary["results"][0]["status"], "valid")
                self.assertEqual(summary["results"][0]["label"], "KEEP")
                revised = json.loads((output / "results" / "AT1.json").read_text())
                self.assertEqual(revised["profile"]["evidence"][0]["quote"], item["evidence"][0]["quote"])
                self.assertEqual(revised["quote_validation_version"], "quote_edge_whitespace_v2")
                self.assertEqual(original_path.read_bytes(), original_bytes)
                with self.assertRaises(FileExistsError):
                    revalidate_saved(directory, directory, output)
                first = report(directory, directory, results_directory=output / "results")
                first_bytes = first.read_bytes()
                second = report(directory, directory, results_directory=output / "results")
                self.assertEqual(second.name, "review_report_2.md")
                self.assertEqual(first.read_bytes(), first_bytes)
                self.assertIn("PASS_EDGE_WHITESPACE_TRIMMED", second.read_text())

    def test_four_case_rerun_preserves_frozen_evidence_and_versions_contract(self):
        real_root = find_project_root()
        old_cfg = config() | {"contract_id": "company_ai_profile_v1",
                              "mapping_version": "company_ai_profile_mapping_v1"}
        new_cfg = load_config(real_root, real_root / "config/company_ai_profile_pilot_v5.yaml")
        old_prompt, old_schema = prompt_and_schema()
        new_prompt, new_schema = prompt_and_schema("company_ai_profile_v4")
        self.assertEqual(new_schema, prompt_and_schema("company_ai_profile_v3")[1])
        for key in ("model", "reasoning_effort", "max_output_tokens", "website_evidence_token_cap"):
            self.assertEqual(new_cfg[key], old_cfg[key])
        self.assertIn("NEVER add it to entities", new_prompt)
        self.assertIn("EXACT cited page_id", new_prompt)
        self.assertIn("attribution_scope=group", new_prompt)
        self.assertIn("NOT KEEP/UNKNOWN/EXCLUDE labels", new_prompt)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / DEFAULT_OUTPUT
            target = root / "pilot_v6"
            source.mkdir(parents=True)
            save_json(source / "config_snapshot.json", old_cfg)
            (source / "prompt_snapshot.txt").write_text(old_prompt, encoding="utf-8")
            save_json(source / "response.schema.json", old_schema)
            manifest, index = [], []
            for number in range(10):
                cid = f"AT{number}"
                pkg = package() | {"company_id": cid}
                save_json(source / "packages" / f"{cid}.json", pkg)
                manifest.append({"company_id": cid, "company_name": "Example GmbH",
                                 "normalized_domain": "example.at", "selection_group": "both_top_100"})
                index.append({"company_id": cid,
                              "request_hash": digest(request_body(pkg, old_cfg, old_prompt, old_schema)),
                              "request_input_tokens_exact": 1000})
            save_json(source / "selection_manifest.json", {"companies": manifest})
            save_json(source / "requests.json", index)
            client = Mock()
            client.responses.input_tokens.count.side_effect = lambda **kwargs: SimpleNamespace(input_tokens=1000)
            selected = ["AT8", "AT1", "AT5", "AT3"]
            with patch("ai_ecosystem.calibration.run_company_ai_profile_pilot.load_config",
                       side_effect=lambda _root, path: new_cfg if path.name == "company_ai_profile_pilot_v5.yaml" else old_cfg):
                summary = prepare_rerun(root, source, target, contract_id="company_ai_profile_v4",
                                        company_ids=selected, token_client=client)
                cfg, prompt, schema, selected_manifest, requests = load_prepared(root, target)
            self.assertEqual(summary["companies"], 4)
            self.assertEqual([r["company_id"] for r in requests], ["AT1", "AT3", "AT5", "AT8"])
            self.assertEqual([r["company_id"] for r in selected_manifest], ["AT1", "AT3", "AT5", "AT8"])
            self.assertFalse((target / "packages").exists())
            self.assertFalse((target / "results").exists())
            self.assertEqual(prompt, new_prompt)
            self.assertEqual(schema, new_schema)
            for row in requests:
                cid = row["company_id"]
                self.assertEqual(package_for(target, cid), package_for(source, cid))
                self.assertEqual(body_for(target, cfg, prompt, schema, cid)["input"][1],
                                 request_body(package_for(source, cid), old_cfg, old_prompt, old_schema)["input"][1])
            save_json(source / "packages" / "AT1.json", package() | {"company_id": "AT1", "changed": True})
            with self.assertRaisesRegex(ValueError, "Frozen source package changed"):
                package_for(target, "AT1")

    def test_scoped_rerun_keeps_exact_v3_source_and_new_request_identity(self):
        real_root = find_project_root()
        new_cfg = load_config(real_root, real_root / "config/company_ai_profile_pilot_v4.yaml")
        old_cfg = config() | {"contract_id": "company_ai_profile_v1",
                              "mapping_version": "company_ai_profile_mapping_v1"}
        old_prompt, old_schema = prompt_and_schema()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / DEFAULT_OUTPUT
            target = root / "pilot_v5"
            source.mkdir(parents=True)
            save_json(source / "config_snapshot.json", old_cfg)
            (source / "prompt_snapshot.txt").write_text(old_prompt, encoding="utf-8")
            save_json(source / "response.schema.json", old_schema)
            manifest, index = [], []
            for number in range(10):
                pkg = package() | {"company_id": f"AT{number}"}
                save_json(source / "packages" / f"AT{number}.json", pkg)
                body = request_body(pkg, old_cfg, old_prompt, old_schema)
                manifest.append({"company_id": f"AT{number}", "company_name": "Example GmbH",
                                 "normalized_domain": "example.at", "selection_group": "both_top_100"})
                index.append({"company_id": f"AT{number}", "request_hash": digest(body),
                              "request_input_tokens_exact": 1000})
            save_json(source / "selection_manifest.json", {"companies": manifest})
            save_json(source / "requests.json", index)
            client = Mock()
            client.responses.input_tokens.count.side_effect = lambda **kwargs: SimpleNamespace(
                input_tokens=10 + len(kwargs["input"][0]["content"]) // 5 +
                len(kwargs["input"][1]["content"]) // 5 + (20 if "text" in kwargs else 0))
            def cfg_for(_root, path):
                return new_cfg if path.name == "company_ai_profile_pilot_v4.yaml" else old_cfg
            with patch("ai_ecosystem.calibration.run_company_ai_profile_pilot.load_config", side_effect=cfg_for):
                summary = prepare_rerun(root, source, target,
                                        contract_id="company_ai_profile_v3", token_client=client)
                new_config, new_prompt, new_schema, new_manifest, new_index = load_prepared(root, target)
                self.assertEqual(summary["companies"], 10)
                self.assertEqual(manifest, new_manifest)
                self.assertEqual(new_config["contract_id"], "company_ai_profile_v3")
                self.assertFalse((target / "packages").exists())
                self.assertFalse((target / "results").exists())
                self.assertTrue(all(old["request_hash"] != new["request_hash"]
                                    for old, new in zip(index, new_index)))
                self.assertTrue(all(body_for(target, new_config, new_prompt, new_schema, cid)["input"][1] ==
                                    request_body(package_for(source, cid), old_cfg, old_prompt, old_schema)["input"][1]
                                    for cid in (row["company_id"] for row in index)))
                with self.assertRaisesRegex(ValueError, "frozen v3 product-priority"):
                    prepare_rerun(root, root / "not_the_source", root / "other",
                                  contract_id="company_ai_profile_v3", token_client=client)

    def test_revised_rerun_reuses_frozen_packages_and_rejects_source_mutation(self):
        root = find_project_root()
        old_cfg = load_config(root)
        old_prompt, old_schema = prompt_and_schema()
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / "v3", Path(tmp) / "v4"
            source.mkdir()
            save_json(source / "config_snapshot.json", old_cfg)
            (source / "prompt_snapshot.txt").write_text(old_prompt, encoding="utf-8")
            save_json(source / "response.schema.json", old_schema)
            manifest, index = [], []
            for number in range(10):
                pkg = package() | {"company_id": f"AT{number}"}
                save_json(source / "packages" / f"AT{number}.json", pkg)
                body = request_body(pkg, old_cfg, old_prompt, old_schema)
                manifest.append({"company_id": f"AT{number}", "selection_group": "both_top_100"})
                index.append({"company_id": f"AT{number}", "request_hash": digest(body),
                              "request_input_tokens_exact": 1000,
                              "website_evidence_tokens_exact": 100,
                              "model_count_payload_sha256": hashlib.sha256(
                                  canonical(count_payload(body)).encode()).hexdigest(),
                              "allocation_local_tokens": local_tokens(body["input"][1]["content"], old_cfg)})
            save_json(source / "selection_manifest.json", {"companies": manifest})
            save_json(source / "requests.json", index)
            client = Mock()
            client.responses.input_tokens.count.side_effect = lambda **kwargs: SimpleNamespace(
                input_tokens=10 + len(kwargs["input"][0]["content"]) // 4 +
                len(kwargs["input"][1]["content"]) // 4 + (20 if "text" in kwargs else 0))
            summary = prepare_rerun(root, source, target, token_client=client)
            self.assertEqual(summary["companies"], 10)
            self.assertFalse((target / "packages").exists())
            self.assertFalse((target / "results").exists())
            self.assertEqual(package_for(target, "AT0"), package() | {"company_id": "AT0"})
            self.assertNotEqual(json.loads((source / "requests.json").read_text())[0]["request_hash"],
                                json.loads((target / "requests.json").read_text())[0]["request_hash"])
            load_prepared(root, target)
            with self.assertRaises(FileExistsError):
                prepare_rerun(root, source, target, token_client=client)
            pkg = json.loads((source / "packages" / "AT0.json").read_text())
            pkg["pages"][0]["text"] += " changed"
            save_json(source / "packages" / "AT0.json", pkg)
            with self.assertRaisesRegex(ValueError, "Frozen source package changed"):
                load_prepared(root, target)

    def test_frozen_package_matches_ordinary_and_batch_request_body(self):
        root = find_project_root()
        cfg = load_config(root)
        prompt, schema = prompt_and_schema()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            save_json(directory / "config_snapshot.json", cfg)
            (directory / "prompt_snapshot.txt").write_text(prompt, encoding="utf-8")
            save_json(directory / "response.schema.json", schema)
            manifest, index = [], []
            for number in range(10):
                pkg = package()
                cid = f"AT{number}"
                pkg["company_id"] = cid
                save_json(directory / "packages" / f"{cid}.json", pkg)
                body = request_body(pkg, cfg, prompt, schema)
                manifest.append({"company_id": cid, "selection_group": "both_top_100"})
                index.append({"company_id": cid, "custom_id": f"profile-{cid}",
                              "request_hash": digest(body),
                              "model_count_payload_sha256": hashlib.sha256(
                                  canonical(count_payload(body)).encode("utf-8")).hexdigest(),
                              "request_input_tokens_exact": 10000,
                              "website_evidence_tokens_exact": 1000,
                              "allocation_local_tokens": local_tokens(body["input"][1]["content"], cfg)})
            save_json(directory / "selection_manifest.json", {"companies": manifest})
            save_json(directory / "requests.json", index)
            load_prepared(root, directory)
            self.assertEqual(dry_run(root, directory)["input_tokens"], 100000)
            output = directory / "batch.jsonl"
            export_batch(root, directory, output)
            lines = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(lines), 10)
            self.assertEqual(lines[0]["body"], request_body(package() | {"company_id": "AT0"}, cfg, prompt, schema))
            pkg = json.loads((directory / "packages" / "AT0.json").read_text(encoding="utf-8"))
            pkg["pages"][0]["text"] += " modified"
            save_json(directory / "packages" / "AT0.json", pkg)
            with self.assertRaisesRegex(ValueError, "Prepared request changed"):
                load_prepared(root, directory)

    def test_saved_valid_is_not_recharged_and_identity_is_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root / "pilot"
            directory.mkdir()
            save_json(directory / "packages" / "AT1.json", package())
            cfg = config()
            prompt, schema = prompt_and_schema()
            manifest = [{"company_id": "AT1", "company_name": "Example GmbH"}]
            index = [{"company_id": "AT1", "request_hash": "stable", "request_token_upper_bound": 1000}]
            save_json(directory / "pilot_started.json", {"mode": "ordinary_responses"})
            save_json(directory / "results" / "AT1.json", {"request_hash": "stable", "status": "valid"})
            client = Mock()
            with patch("ai_ecosystem.calibration.run_company_ai_profile_pilot.load_prepared",
                       return_value=(cfg, prompt, schema, manifest, index)):
                run(root, directory, resume=True, client=client)
                client.responses.create.assert_not_called()
                index[0]["request_hash"] = "changed"
                with self.assertRaisesRegex(ValueError, "identity differs"):
                    run(root, directory, resume=True, client=client)

    def test_response_persisted_then_resume_skips_paid_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root / "pilot"
            directory.mkdir()
            (directory / "packages").mkdir()
            prompt, schema = prompt_and_schema()
            cfg = config()
            pkg = package()
            save_json(directory / "packages" / "AT1.json", pkg)
            body = request_body(pkg, cfg, prompt, schema)
            manifest = [{"company_id": "AT1", "company_name": "Example GmbH", "selection_group": "both_top_100"}]
            index = [{"company_id": "AT1", "request_hash": digest(body),
                      "request_token_upper_bound": tokens_upper_bound(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")))}]
            p = profile()
            p["ai_active"] = assessment(True, "explicit", "operational", ["q1"])
            p["ai_summary"] = "AI software."
            p["evidence"] = [evidence()]
            client = Mock()
            client.responses.create.return_value = response(p)
            with patch("ai_ecosystem.calibration.run_company_ai_profile_pilot.load_prepared",
                       return_value=(cfg, prompt, schema, manifest, index)):
                run(root, directory, client=client)
                self.assertEqual(client.responses.create.call_count, 1)
                result = json.loads((directory / "results" / "AT1.json").read_text())
                self.assertEqual(result["derived"]["label"], "KEEP")
                self.assertTrue((directory / "raw_responses" / "AT1" / "0001.json").exists())
                run(root, directory, resume=True, client=client)
                self.assertEqual(client.responses.create.call_count, 1)

    def test_recover_raw_response_without_recharge(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root / "pilot"
            directory.mkdir()
            (directory / "packages").mkdir()
            cfg = config()
            prompt, schema = prompt_and_schema()
            pkg = package()
            save_json(directory / "packages" / "AT1.json", pkg)
            body = request_body(pkg, cfg, prompt, schema)
            row = {"company_id": "AT1", "request_hash": digest(body), "request_token_upper_bound": tokens_upper_bound(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")))}
            save_json(directory / "pilot_started.json", {"mode": "ordinary_responses"})
            save_json(directory / "attempts" / "AT1" / "0001.json", {"request_hash": row["request_hash"],
                      "attempt": 1, "status": "in_flight", "reserved_usd": .01})
            save_json(directory / "raw_responses" / "AT1" / "0001.json", response(profile()))
            client = Mock()
            with patch("ai_ecosystem.calibration.run_company_ai_profile_pilot.load_prepared",
                       return_value=(cfg, prompt, schema, [{"company_id": "AT1"}], [row])):
                run(root, directory, resume=True, client=client)
            client.responses.create.assert_not_called()
            self.assertEqual(json.loads((directory / "results" / "AT1.json").read_text())["derived"]["label"], "EXCLUDE")

    def test_no_evidence_skips_call_and_spend_guard_blocks_call(self):
        for has_pages, allowance in ((False, 2.0), (True, 0.000001)):
            with self.subTest(has_pages=has_pages):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    directory = root / "pilot"
                    directory.mkdir()
                    cfg = config()
                    cfg["pilot_spend_allowance_usd"] = allowance
                    prompt, schema = prompt_and_schema()
                    pkg = package()
                    if not has_pages:
                        pkg["pages"] = []
                    save_json(directory / "packages" / "AT1.json", pkg)
                    body = request_body(pkg, cfg, prompt, schema)
                    row = {"company_id": "AT1", "request_hash": digest(body),
                           "request_token_upper_bound": tokens_upper_bound(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")))}
                    manifest = [{"company_id": "AT1", "company_name": "Example GmbH", "selection_group": "both_top_100"}]
                    client = Mock()
                    with patch("ai_ecosystem.calibration.run_company_ai_profile_pilot.load_prepared",
                               return_value=(cfg, prompt, schema, manifest, [row])):
                        if has_pages:
                            with self.assertRaisesRegex(ValueError, "exceeds spend allowance"):
                                run(root, directory, client=client)
                        else:
                            run(root, directory, client=client)
                            result = json.loads((directory / "results" / "AT1.json").read_text())
                            self.assertEqual(result["status"], "NO_EVIDENCE")
                            self.assertIsNone(result["derived"]["label"])
                    client.responses.create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
