"""Initialize the project SQLite database."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import yaml


def find_project_root() -> Path:
    """Return the repository root containing the project config file."""
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "config" / "config.yaml").is_file():
            return parent
    raise FileNotFoundError("Could not find project root containing config/config.yaml")


def load_config(project_root: Path) -> dict:
    config_path = project_root / "config" / "config.yaml"
    with config_path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def init_database() -> Path:
    project_root = find_project_root()
    config = load_config(project_root)

    db_path_value = config.get("database", {}).get("path")
    if not db_path_value:
        raise ValueError("Missing database.path in config/config.yaml")

    db_path = Path(db_path_value)
    if not db_path.is_absolute():
        db_path = project_root / db_path
    db_path = db_path.resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    schema_path = Path(__file__).resolve().with_name("schema.sql")
    schema_sql = schema_path.read_text(encoding="utf-8")

    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON;")
        connection.executescript(schema_sql)

    return db_path


def main() -> None:
    db_path = init_database()
    print(f"Initialized SQLite database at {db_path}")


if __name__ == "__main__":
    main()
