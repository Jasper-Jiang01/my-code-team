"""Agent Harness 评测集回归。"""

from codepilot.core.agent_loader import load_agent_harness
from codepilot.core.eval_runner import run_all_evals


def test_all_harness_eval_cases_pass():
    load_agent_harness.cache_clear()
    failed = [row for row in run_all_evals() if not row.passed]
    assert failed == [], [f"{row.harness}:{row.case_id} {row.message}" for row in failed]
