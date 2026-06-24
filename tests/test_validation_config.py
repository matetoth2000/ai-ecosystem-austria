from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_ecosystem.validation.config import (
    OpenAIAPIKeyMissingError,
    load_openai_api_key,
    load_validation_config,
)


def write_validation_config(project_root: Path) -> None:
    (project_root / "config").mkdir()
    (project_root / "config" / "config.yaml").write_text(
        "\n".join(
            [
                "database:",
                "  path: db/test.sqlite",
                "validation:",
                "  enabled: false",
                "  model: gpt-5-mini",
                "  temperature: 0",
                "  max_input_chars: 2000",
                "  smoke_sample_size_per_group: 5",
                "  main_sample_size_per_group: 100",
                "  max_pages_per_domain: 3",
                "  prompt_version: v1",
                "  random_seed: 20260608",
            ]
        ),
        encoding="utf-8",
    )


class ValidationConfigTest(unittest.TestCase):
    def test_load_validation_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            write_validation_config(project_root)

            validation = load_validation_config(project_root)

        self.assertFalse(validation["enabled"])
        self.assertEqual(validation["model"], "gpt-5-mini")
        self.assertEqual(validation["temperature"], 0)
        self.assertEqual(validation["max_input_chars"], 2000)
        self.assertEqual(validation["prompt_version"], "v1")
        self.assertEqual(validation["random_seed"], 20260608)

    def test_openai_api_key_loads_from_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True):
                self.assertEqual(load_openai_api_key(project_root), "test-key")

    def test_openai_api_key_loads_from_local_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            (project_root / ".env").write_text("OPENAI_API_KEY=file-key\n", encoding="utf-8")

            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(load_openai_api_key(project_root), "file-key")

    def test_missing_openai_api_key_raises_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)

            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(OpenAIAPIKeyMissingError, "Missing OPENAI_API_KEY"):
                    load_openai_api_key(project_root)


if __name__ == "__main__":
    unittest.main()
