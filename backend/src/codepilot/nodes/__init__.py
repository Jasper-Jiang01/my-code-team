"""工作流图的节点函数。"""

from codepilot.nodes.chitchat import chitchat
from codepilot.nodes.classify_task import classify_task
from codepilot.nodes.critic import critic
from codepilot.nodes.execute_data import execute_data
from codepilot.nodes.execute_produce import execute_produce
from codepilot.nodes.execute_qa import execute_qa
from codepilot.nodes.execute_research import execute_research
from codepilot.nodes.execute_review import execute_review
from codepilot.nodes.fast_qa import fast_qa
from codepilot.nodes.human_confirm import human_confirm
from codepilot.nodes.judge import judge
from codepilot.nodes.loop_control import (
    after_qa,
    mark_decision_snapshot,
    mark_production_snapshot,
    route_after_qa,
)
from codepilot.nodes.producer import producer
from codepilot.nodes.production_steps import (
    build,
    compare,
    explore,
    generate,
    guard,
    verify,
)
from codepilot.nodes.researcher import researcher
from codepilot.nodes.review_steps import (
    finalize_review,
    fix_agent,
    function_gate,
    loop_condition,
    panel,
    rehearsal_gate,
    review_fan_out,
    visual_gate,
)
from codepilot.nodes.route_task import route_after_decision, route_after_production, route_after_research, route_task
from codepilot.nodes.synthesize_results import synthesize_results
from codepilot.nodes.tournament import (
    candidate_producer,
    fan_out_candidates,
    filter_candidates,
    tournament,
)
from codepilot.nodes.triage import triage

__all__ = [
    "after_qa",
    "build",
    "chitchat",
    "classify_task",
    "compare",
    "critic",
    "execute_data",
    "execute_produce",
    "execute_qa",
    "execute_research",
    "execute_review",
    "explore",
    "fast_qa",
    "finalize_review",
    "fix_agent",
    "function_gate",
    "generate",
    "guard",
    "human_confirm",
    "judge",
    "loop_condition",
    "mark_decision_snapshot",
    "mark_production_snapshot",
    "panel",
    "producer",
    "rehearsal_gate",
    "researcher",
    "review_fan_out",
    "route_after_qa",
    "route_after_decision",
    "route_after_production",
    "route_after_research",
    "route_task",
    "synthesize_results",
    "tournament",
    "triage",
    "candidate_producer",
    "fan_out_candidates",
    "filter_candidates",
    "verify",
    "visual_gate",
]
