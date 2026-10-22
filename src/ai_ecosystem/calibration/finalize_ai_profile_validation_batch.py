"""Finalize the reviewed independent-validation Batch without API calls."""
from __future__ import annotations

import argparse
from pathlib import Path

from ai_ecosystem.db.init_db import find_project_root
from .prepare_ai_profile_validation_batch import DEFAULT_OUTPUT, prepare


FINAL_OUTPUT = "data/interim/calibration/ai_profile_validation_batch_v2_final"
REVIEW = "data/interim/calibration/ai_profile_overlap_review_v1/decision_table.csv"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(FINAL_OUTPUT))
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    root = find_project_root()
    output = args.output if args.output.is_absolute() else root / args.output
    result = prepare(root, output, resume=args.resume, review=root / REVIEW, previous=root / DEFAULT_OUTPUT)
    print(f"Finalized {result['included']} requests in {len(result['files'])} Batch files: {output}")


if __name__ == "__main__":
    main()
