#!/usr/bin/env python3
"""Test API key validity and connection."""

import os
import sys
import anthropic

api_key = os.environ.get("ANTHROPIC_API_KEY", "")

print(f"[TEST] API key: {api_key[:30]}...{api_key[-10:]}")

try:
    print("[TEST] Initializing Anthropic client...", file=sys.stderr)
    client = anthropic.Anthropic(api_key=api_key, timeout=30.0)
    
    print("[TEST] Testing API connection with simple request...", file=sys.stderr)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=100,
        messages=[{"role": "user", "content": "Say 'API works' in JSON format: {\"status\": \"...\"}"}]
    )
    
    print(f"[TEST] ✓ API Response: {response.content[0].text}", file=sys.stderr)
    print("[TEST] ✓ API connection successful!")
    
except anthropic.AuthenticationError as e:
    print(f"[TEST] ✗ AUTHENTICATION FAILED - API key is invalid: {e}", file=sys.stderr)
    sys.exit(1)
except anthropic.APIError as e:
    print(f"[TEST] ✗ API ERROR: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print(f"[TEST] ✗ ERROR: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(1)
