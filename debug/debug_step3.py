#!/usr/bin/env python3
"""Debug script to capture the full raw response from step 3."""

from agent import extract_brief_structure, generate_messaging
import json
import sys

try:
    with open("sample_brief.txt", "r") as f:
        brief = f.read()
except FileNotFoundError:
    print("sample_brief.txt not found!")
    sys.exit(1)

print("[DEBUG] Step 1: Extract brief structure", file=sys.stderr)
structure = extract_brief_structure(brief)

print("[DEBUG] Step 2: Generate messaging", file=sys.stderr)
messaging = generate_messaging(structure)

print("\n[DEBUG] Step 3: Attempting to generate rollout...", file=sys.stderr)

# Import required modules
import anthropic
import os

api_key = os.environ.get("ANTHROPIC_API_KEY", "")
client = anthropic.Anthropic(api_key=api_key, timeout=60.0)

response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=2500,
    system="""You are a B2B SaaS marketing operations lead.
Generate a phased campaign rollout plan with Asana-ready tasks.
Return ONLY valid JSON with no preamble, no markdown, no backticks.
Keys:
  phases (array of 3 objects: {phase, weeks, milestone, tasks: [{task, owner, due_day}]}),
  success_metrics (array of 4-5 strings, each a specific measurable outcome),
  human_review_checkpoints (array of strings — moments where human must approve before proceeding)""",
    messages=[{"role": "user", "content": f"Generate a rollout plan for this campaign.\n\nBrief structure:\n{json.dumps(structure, indent=2)}\n\nMessaging:\n{json.dumps(messaging, indent=2)}"}]
)

raw = response.content[0].text
print(f"\n[DEBUG] Raw response length: {len(raw)} characters", file=sys.stderr)
print(f"[DEBUG] Raw response:\n{raw}", file=sys.stderr)

# Try to find the error position
print(f"\n[DEBUG] Response ends with: {repr(raw[-100:])}", file=sys.stderr)
