#!/usr/bin/env python3
"""Write raw step 3 response to file for inspection."""

import anthropic
import json
import os

api_key = os.environ.get("ANTHROPIC_API_KEY", "")
client = anthropic.Anthropic(api_key=api_key, timeout=60.0)

# Simple test structure
structure = {
    "icp": "VP of Learning & Development",
    "pain_point": "Manual learning programs",
    "product_angle": "AI-powered learning",
    "campaign_goal": "Drive demos",
    "tone": "Confident",
    "timeline_weeks": 8
}

messaging = {
    "positioning_statement": "AgentHub turns Docebo into an autonomous learning engine.",
    "pillars": [{"title": "P1", "one_liner": "L1", "proof_point": "PP1"}],
    "cta_by_persona": {"VP": "Demo CTA"},
    "asset_plan": [{"asset_type": "Email", "format": "HTML", "owner": "Marketing", "purpose": "Nurture"}]
}

print("Requesting Step 3 response...")
response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=2500,
    system="""You are a B2B SaaS marketing operations lead.
Generate a phased campaign rollout plan with Asana-ready tasks.
Return ONLY valid JSON with no preamble, no markdown, no backticks.
Keys:
  phases (array of 3 objects: {phase, weeks, milestone, tasks: [{task, owner, due_day}]}),
  success_metrics (array of 4-5 strings, each a specific measurable outcome),
  human_review_checkpoints (array of strings)""",
    messages=[{"role": "user", "content": f"Generate a rollout plan for this campaign.\n\nBrief structure:\n{json.dumps(structure, indent=2)}\n\nMessaging:\n{json.dumps(messaging, indent=2)}"}]
)

raw = response.content[0].text

# Write to file
with open("raw_response.txt", "w") as f:
    f.write(raw)

print(f"Response saved to raw_response.txt ({len(raw)} chars)")
print(f"Ends with: {repr(raw[-100:])}")

# Try to parse it
try:
    json.loads(raw)
    print("✓ Raw response is valid JSON")
except json.JSONDecodeError as e:
    print(f"✗ JSON parse error: {e}")
    print(f"Error at position {e.pos}")
    if e.pos:
        print(f"Context around error: {repr(raw[max(0, e.pos-80):min(len(raw), e.pos+80)])}")
