from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from app_models import BagelState, PizzaState
from ollama_client import OllamaClient


CASES_PATH = Path(__file__).resolve().parents[1] / "evals" / "extraction_cases.json"


def main() -> int:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    client = OllamaClient()
    exact_matches = 0
    matched_fields = 0
    total_fields = 0
    total_seconds = 0.0

    for index, case in enumerate(cases, start=1):
        state = PizzaState() if case["flow"] == "pizza" else BagelState()
        extract = client.extract_order_update if case["flow"] == "pizza" else client.extract_bagel_update
        started_at = time.perf_counter()
        updates, metadata = extract(case["message"], state)
        elapsed = time.perf_counter() - started_at
        total_seconds += elapsed

        expected: dict[str, Any] = case["expected"]
        mismatches = {
            key: {"expected": value, "actual": updates.get(key)}
            for key, value in expected.items()
            if updates.get(key) != value
        }
        matched_fields += len(expected) - len(mismatches)
        total_fields += len(expected)
        passed = not mismatches
        exact_matches += int(passed)
        result = "PASS" if passed else "FAIL"
        print(f"{result} case {index} ({case['flow']}, {elapsed:.2f}s)")
        if mismatches:
            print(f"  mismatches: {json.dumps(mismatches, ensure_ascii=False)}")
        if metadata.get("success") is False:
            print(f"  extractor error: {metadata.get('error')}")

    case_count = len(cases)
    print(
        "Summary: "
        f"exact_match={exact_matches}/{case_count} ({exact_matches / case_count:.0%}), "
        f"field_match={matched_fields}/{total_fields} ({matched_fields / total_fields:.0%}), "
        f"mean_latency={total_seconds / case_count:.2f}s"
    )
    return 0 if exact_matches == case_count else 1


if __name__ == "__main__":
    raise SystemExit(main())
