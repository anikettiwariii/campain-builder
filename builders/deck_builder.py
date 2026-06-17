import anthropic
import json
import os
import time
from datetime import date

from builders.campaign_builder import parse_json, PIPELINE_TIMINGS

client = anthropic.Anthropic()
MODEL  = "claude-haiku-4-5-20251001"

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_prompt(name: str) -> str:
    p    = os.path.join(_ROOT, "prompts")
    ctx  = open(os.path.join(p, "system_context.txt")).read().strip()
    role = open(os.path.join(p, name)).read().strip()
    return f"{ctx}\n\n{role}"


_DECK_SCHEMA = """{
  "meta": {
    "date": "Month DD YYYY",
    "product": "exact product name",
    "evidence_gaps": "· separated list or 'All evidence verified'"
  },
  "title_slide": {
    "product": "exact product name",
    "goal": "primary metric + target + timeframe",
    "timeline": "X weeks",
    "motion": "campaign motion"
  },
  "status_slide": {
    "icp": "persona + Docebo relationship, max 12 words",
    "motion": "same as title_slide.motion",
    "goal": "same as title_slide.goal",
    "timeline": "X weeks · Month YYYY"
  },
  "pillars_slide": {
    "positioning": "verbatim positioning statement",
    "pillars": [
      {
        "title": "pillar title max 5 words",
        "one_liner": "max 8 words",
        "proof_status": "verified | stat | needed",
        "proof": "proof text max 10 words, or exact placeholder if needed"
      }
    ]
  },
  "asset_slide": {
    "asset_count": 0,
    "evidence_note": "string or null",
    "assets": [
      {
        "name": "asset type",
        "format": "max 4 words",
        "owner": "team name",
        "purpose": "funnel role max 8 words"
      }
    ]
  },
  "rollout_slide": {
    "phases": [
      {
        "name": "phase name max 4 words",
        "days": "Days X-Y",
        "milestone": "max 10 words",
        "tasks": ["Owner — description · Day X"],
        "checkpoint": "Day N: Team A + Team B — decision"
      }
    ]
  },
  "metrics_slide": {
    "metrics": [{"label": "string", "value": "string", "verified": true}],
    "checkpoints": [{"day": "Day N", "teams": "Team A + Team B", "action": "max 12 words"}]
  }
}"""


def distill_for_deck(campaign_output: dict, readiness: dict = None) -> dict:
    """Compress full campaign output into slide-ready JSON for the PPTX generator.

    readiness: the dict returned by compute_readiness_score(). When provided,
    numeric scores and status label are injected into meta and status_slide so
    the AI doesn't need to invent them.
    """
    source = {
        "structure": campaign_output.get("structure", {}),
        "messaging": campaign_output.get("messaging", {}),
        "rollout":   campaign_output.get("rollout", {}),
    }

    today = date.today().strftime("%B %d %Y")

    _t0 = time.perf_counter()
    r = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        temperature=0.3,
        system=_load_prompt("deck_distiller.txt"),
        messages=[{"role": "user", "content": (
            f"Today's date is {today}. "
            f"Distill this campaign output into the schema below.\n\n"
            f"SCHEMA:\n{_DECK_SCHEMA}\n\n"
            f"CAMPAIGN OUTPUT:\n{json.dumps(source, indent=2)}"
        )}]
    )
    PIPELINE_TIMINGS["04_deck"] = round(time.perf_counter() - _t0, 2)
    deck = parse_json(r.content[0].text)

    # Inject system-computed readiness scores so the AI doesn't need to derive them
    if readiness:
        rs   = readiness.get("readiness", 0)
        ss   = readiness.get("structure_score", 0)
        es   = readiness.get("evidence_score", 0)
        stat = readiness.get("status", "Hypothesis")

        meta = deck.setdefault("meta", {})
        meta["readiness_score"]  = rs
        meta["structure_score"]  = ss
        meta["evidence_score"]   = es
        meta["status_label"]     = stat

        status_slide = deck.setdefault("status_slide", {})
        status_slide["readiness_score"]  = rs
        status_slide["structure_score"]  = ss
        status_slide["evidence_score"]   = es
        status_slide["status_label"]     = stat

    return deck
