#!/usr/bin/env python3
"""Quick test script to verify agent execution and see debug logs."""

from agent import run_campaign_agent
import sys

# Load sample brief
try:
    with open("sample_brief.txt", "r") as f:
        brief = f.read()
except FileNotFoundError:
    print("sample_brief.txt not found!")
    sys.exit(1)

print("\n" + "="*60, file=sys.stderr)
print("Testing agent with sample brief...", file=sys.stderr)
print("="*60, file=sys.stderr)

try:
    result = run_campaign_agent(brief)
    print("\n✓ SUCCESS! Agent completed all steps.", file=sys.stderr)
    print(f"Result keys: {result.keys()}", file=sys.stderr)
except Exception as e:
    print(f"\n✗ FAILED: {e}", file=sys.stderr)
    sys.exit(1)
