import json
from pathlib import Path

import pytest

from planner import BagelPlanner, PizzaPlanner


CASES_PATH = Path(__file__).resolve().parents[1] / "evals" / "extraction_cases.json"
EVALUATION_CASES = json.loads(CASES_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", EVALUATION_CASES, ids=lambda case: case["message"])
def test_offline_extraction_examples(case):
    planner = PizzaPlanner() if case["flow"] == "pizza" else BagelPlanner()

    assert planner._fallback_extract_from_text(case["message"]) == case["expected"]
