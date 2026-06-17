import os

from agents.intake_agent import intake_agent, is_valid_docebo_brief, check_persona_match
from builders.campaign_builder import (
    load_knowledge_graph,
    extract_brief_structure,
    generate_messaging,
    generate_rollout,
)
from builders.deck_builder   import distill_for_deck
from builders.asana_builder  import push_to_asana

__all__ = [
    "run_intake",
    "is_valid_docebo_brief",
    "check_persona_match",
    "load_knowledge_graph",
    "extract_brief_structure",
    "generate_messaging",
    "generate_rollout",
    "distill_for_deck",
    "push_to_asana",
    "run_campaign_agent",
]


def run_intake(brief: str, force_proceed: bool = False) -> dict:
    """Entry point for the intake check. Returns proceed + enriched_brief or question."""
    return intake_agent(brief, force_proceed=force_proceed)


def run_campaign_agent(brief: str) -> dict:
    """Full pipeline in one call: parse → messaging → rollout → distill."""
    ctx       = load_knowledge_graph(brief)
    structure = extract_brief_structure(brief, ctx)
    messaging = generate_messaging(structure, ctx)
    rollout   = generate_rollout(structure, messaging, ctx)
    campaign  = {
        "structure":        structure,
        "messaging":        messaging,
        "rollout":          rollout,
        "knowledge_context": ctx,
    }
    campaign["deck_content"] = distill_for_deck(campaign)
    return campaign
