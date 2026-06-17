import streamlit as st
import json
import os
import re
import copy
import subprocess
import tempfile
import streamlit.components.v1 as _components

# ── Load secrets into env before any module that creates API clients ──────────
for _sk in ["ANTHROPIC_API_KEY", "ASANA_TOKEN", "ASANA_WORKSPACE_GID",
            "APP_USERNAME", "APP_PASSWORD",
            "NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD"]:
    try:
        if _sk in st.secrets and not os.environ.get(_sk):
            os.environ[_sk] = st.secrets[_sk]
    except Exception:
        pass

from main import (
    run_intake,
    is_valid_docebo_brief,
    check_persona_match,
    load_knowledge_graph,
    extract_brief_structure,
    generate_messaging,
    generate_rollout,
    distill_for_deck,
    push_to_asana,
)
from builders.campaign_builder import compute_readiness_score as _compute_readiness
from builders.campaign_builder import _patch_icp_from_brief
from auth import check_credentials as _check_creds
from db import (
    save_campaign          as _db_save,
    update_status          as _db_update_status,
    update_asana           as _db_update_asana,
    get_all                as _db_get_all,
    get_one                as _db_get_one,
    pipeline_summary       as _db_summary,
    _derive_status         as _db_derive_status,
    backfill_readiness_scores as _db_backfill,
)

# ── Em-dash sanitizer ─────────────────────────────────────────────────────────
def _sanitize_emdash(text):
    """Replace em dashes with context-appropriate punctuation before any string hits the UI."""
    if not isinstance(text, str) or not text:
        return text
    # " — " before an uppercase letter = new sentence
    text = re.sub(r' — (?=[A-Z])', '. ', text)
    # " — " before anything else = connecting comma
    text = re.sub(r' — ', ', ', text)
    # bare em dash (no surrounding spaces)
    text = re.sub(r'—', ', ', text)
    return text


def _deep_sanitize(obj):
    """Recursively apply _sanitize_emdash to every string in a nested dict/list."""
    if isinstance(obj, str):
        return _sanitize_emdash(obj)
    if isinstance(obj, dict):
        return {k: _deep_sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_sanitize(item) for item in obj]
    return obj


# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Campaign Builder Agent",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ── Design system ─────────────────────────────────────────────────────────────
st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap');

  html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
    background-color: #0A0A0F;
    color: #A0A0B0;
  }

  #MainMenu, footer, header { visibility: hidden; }
  .block-container { padding: 2.5rem 3rem 4rem 3rem; max-width: 1000px; }

  [data-testid="stSidebar"] {
    background: #0A0A0F;
    border-right: 1px solid #1E1E2E;
  }

  /* Sidebar collapse/expand toggle — keep it visible on the dark bg */
  [data-testid="collapsedControl"] {
    background: #13131A !important;
    border: 1px solid #1E1E2E !important;
    border-radius: 0 4px 4px 0 !important;
    color: #A0A0B0 !important;
  }
  [data-testid="collapsedControl"] svg { fill: #A0A0B0 !important; }

  /* ── Header ─────────────────────────────────────────────────────────────── */
  .agent-header {
    border-bottom: 1px solid #1E1E2E;
    padding-bottom: 1.5rem;
    margin-bottom: 2.5rem;
  }
  .agent-header h1 {
    font-size: 1.75rem;
    font-weight: 600;
    color: #FFFFFF;
    letter-spacing: -0.02em;
    margin: 0 0 0.25rem 0;
  }
  .agent-header p { font-size: 1.125rem; color: #A0A0B0; margin: 0; }

  /* ── Badge ──────────────────────────────────────────────────────────────── */
  .badge {
    display: inline-block;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 1.0625rem;
    font-weight: 500;
    padding: 0.2rem 0.6rem;
    border-radius: 3px;
    background: #13131A;
    border: 1px solid #1E1E2E;
    color: #A0A0B0;
    letter-spacing: 0.05em;
    margin-bottom: 1.5rem;
  }
  .badge.done { border-color: #2D8B5B; color: #2D8B5B; background: #0D1F14; }

  /* ── Step tracker ───────────────────────────────────────────────────────── */
  .step-row { display: flex; gap: 0.75rem; margin-bottom: 2rem; flex-wrap: wrap; }
  .step-pill {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 1.0625rem;
    padding: 0.3rem 0.75rem;
    border-radius: 2px;
    border: 1px solid #1E1E2E;
    color: #A0A0B0;
    background: #13131A;
  }
  .step-pill.active   { border-color: #6B2D8B; color: #6B2D8B; background: #1A0D24; }
  .step-pill.complete { border-color: #1E1E2E; color: #7A7A8A; background: #13131A; }

  /* ── Cards ──────────────────────────────────────────────────────────────── */
  .section-card {
    background: #13131A;
    border: 1px solid #1E1E2E;
    border-radius: 6px;
    padding: 1.25rem;
    margin-bottom: 1.5rem;
  }
  .section-label {
    font-size: 1.0625rem;
    font-weight: 600;
    letter-spacing: 0.1em;
    color: #A0A0B0;
    text-transform: uppercase;
    margin-bottom: 0.75rem;
  }

  /* ── Positioning ────────────────────────────────────────────────────────── */
  .positioning {
    font-size: 1.65rem;
    font-weight: 500;
    color: #FFFFFF;
    line-height: 1.5;
    letter-spacing: -0.02em;
    padding: 1.25rem 0;
    border-top: 1px solid #1E1E2E;
    border-bottom: 1px solid #1E1E2E;
    margin: 0.75rem 0 1.25rem 0;
  }

  /* ── Messaging pillars ──────────────────────────────────────────────────── */
  .pillar {
    background: #13131A;
    border-left: 3px solid #6B2D8B;
    padding: 1rem 1.25rem;
    margin-bottom: 0.75rem;
    border-radius: 0 4px 4px 0;
  }
  .pillar-title    { font-weight: 600; font-size: 1.375rem; color: #6B2D8B; margin-bottom: 0.3rem; }
  .pillar-oneliner { font-size: 1.125rem; color: #FFFFFF; margin-bottom: 0.4rem; }
  .pillar-proof    { font-size: 1.125rem; color: #A0A0B0; font-style: italic; }

  /* ── Asset plan ─────────────────────────────────────────────────────────── */
  .asset-row {
    display: grid;
    grid-template-columns: 1.5fr 1fr 1fr 2fr;
    gap: 0.5rem;
    padding: 0.6rem 0;
    border-bottom: 1px solid #1E1E2E;
    font-size: 1.125rem;
  }
  .asset-row.header {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 1.0625rem;
    color: #6B2D8B;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }
  .asset-type    { color: #FFFFFF; font-weight: 500; }
  .asset-format  { color: #A0A0B0; white-space: normal; word-break: break-word; }
  .asset-owner   { color: #A0A0B0; }
  .asset-purpose { color: #A0A0B0; }

  /* ── Phased rollout ─────────────────────────────────────────────────────── */
  .phase-block {
    background: #13131A;
    border: 1px solid #1E1E2E;
    padding: 1rem 1.25rem;
    margin-bottom: 0.75rem;
    border-radius: 6px;
  }
  .phase-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 0.75rem;
  }
  .phase-name  { font-weight: 600; font-size: 1.125rem; color: #FFFFFF; }
  .phase-weeks { font-family: 'IBM Plex Mono', monospace; font-size: 1.0625rem; color: #A0A0B0; }
  .phase-milestone {
    font-size: 1.125rem;
    color: #8B6B2D;
    font-style: italic;
    margin-bottom: 0.75rem;
    padding-bottom: 0.5rem;
    border-bottom: 1px solid #1E1E2E;
  }
  .task-row {
    display: grid;
    grid-template-columns: 2fr 1fr 0.75fr;
    font-size: 1.125rem;
    padding: 0.3rem 0;
    gap: 0.5rem;
    color: #A0A0B0;
  }
  .task-row .task-name { color: #FFFFFF; }

  /* ── Checkpoints ────────────────────────────────────────────────────────── */
  .checkpoint {
    display: flex;
    align-items: flex-start;
    gap: 0.6rem;
    padding: 0.6rem 0;
    border-bottom: 1px solid #1E1E2E;
    font-size: 1.125rem;
    color: #A0A0B0;
  }
  .checkpoint-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: #8B6B2D;
    margin-top: 5px;
    flex-shrink: 0;
  }

  /* ── Success metrics ────────────────────────────────────────────────────── */
  .metric-item {
    padding: 0.5rem 0;
    border-bottom: 1px solid #1E1E2E;
    font-size: 1.125rem;
    color: #A0A0B0;
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }
  .metric-item::before {
    content: "→";
    color: #2D8B5B;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 1.0625rem;
    flex-shrink: 0;
  }

  /* ── CTA by persona ─────────────────────────────────────────────────────── */
  .persona-cta {
    padding: 0.75rem 0;
    border-bottom: 1px solid #1E1E2E;
    display: grid;
    grid-template-columns: 1fr 2fr;
    gap: 1rem;
    align-items: center;
  }
  .persona-name {
    font-size: 1.0625rem;
    color: #A0A0B0;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }
  .persona-text { font-size: 1.125rem; color: #FFFFFF; }

  /* ── Inputs ─────────────────────────────────────────────────────────────── */
  textarea {
    background: #13131A !important;
    border: 1px solid #1E1E2E !important;
    color: #FFFFFF !important;
    border-radius: 4px !important;
    font-family: 'Inter', sans-serif !important;
    font-size: 1.07rem !important;
    resize: none !important;
  }
  textarea:focus { border-color: #6B2D8B !important; outline: none !important; }
  textarea::placeholder { color: #606070 !important; }

  [data-testid="stTextInput"] input {
    background: #13131A !important;
    border: 1px solid #1E1E2E !important;
    color: #FFFFFF !important;
    border-radius: 4px !important;
    font-family: 'Inter', sans-serif !important;
    font-size: 1.125rem !important;
  }
  [data-testid="stTextInput"] input::placeholder { color: #606070 !important; }
  [data-testid="stTextInput"] input:focus { border-color: #6B2D8B !important; outline: none !important; }

  /* ── Buttons ────────────────────────────────────────────────────────────── */
  .stButton > button {
    background: #6B2D8B !important;
    color: #FFFFFF !important;
    border: none !important;
    border-radius: 4px !important;
    font-family: 'Inter', sans-serif !important;
    font-size: 1.125rem !important;
    font-weight: 500 !important;
    padding: 0.6rem 2rem !important;
    transition: background 0.15s !important;
  }
  .stButton > button:hover { background: #7D35A3 !important; }

  /* ── Marketing Brain expander ───────────────────────────────────────────── */
  .brain-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 1.125rem;
    margin-bottom: 1.25rem;
  }
  .brain-table th {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 1.0625rem;
    letter-spacing: 0.1em;
    color: #6B2D8B;
    text-transform: uppercase;
    padding: 0.4rem 0.75rem 0.4rem 0;
    border-bottom: 1px solid #1E1E2E;
    text-align: left;
  }
  .brain-table td {
    padding: 0.55rem 0.75rem 0.55rem 0;
    border-bottom: 1px solid #1E1E2E;
    vertical-align: top;
    line-height: 1.5;
  }
  .brain-table td:first-child  { color: #A0A0B0; width: 30%; }
  .brain-table td:last-child   { color: #FFFFFF; }
  .pp-label {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 1.0625rem;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    margin-bottom: 0.5rem;
    margin-top: 0.25rem;
  }
  .pp-row { font-size: 1.125rem; padding: 0.3rem 0; border-bottom: 1px solid #1E1E2E; }
  .pp-row:last-child { border-bottom: none; }
  .pp-verified { color: #2D8B5B; }
  .pp-stat     { color: #8B6B2D; }
  .pp-missing  { color: #8B4040; font-style: italic; font-size: 1.0625rem; }

  /* ── Governance section ─────────────────────────────────────────────────── */
  .gov-header {
    margin-top: 2rem;
    padding-top: 1.5rem;
    border-top: 1px solid #1E1E2E;
    margin-bottom: 1.5rem;
  }
  .gov-title {
    font-size: 1.25rem;
    font-weight: 600;
    color: #FFFFFF;
    margin-bottom: 0.2rem;
  }
  .gov-subtitle { font-size: 1.125rem; color: #A0A0B0; }
  .gov-cp-card {
    background: #13131A;
    border: 1px solid #1E1E2E;
    border-left: 3px solid #8B6B2D;
    border-radius: 0 6px 6px 0;
    padding: 1rem 1.25rem;
    margin-bottom: 0.75rem;
  }
  .gov-cp-meta {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 1.0625rem;
    color: #8B6B2D;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    margin-bottom: 0.4rem;
  }
  .gov-cp-text {
    font-size: 1.125rem;
    color: #FFFFFF;
    line-height: 1.55;
    margin-bottom: 0.65rem;
  }
  .gov-team-badge {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    border: 1px solid #2E2E3E;
    border-radius: 3px;
    padding: 0.18rem 0.55rem;
    font-size: 1.0625rem;
    color: #A0A0B0;
    margin-right: 0.4rem;
    background: #0F0F1A;
  }
  .task-review-hdr {
    display: grid;
    grid-template-columns: 3fr 2fr 1fr;
    gap: 0.5rem;
    padding: 0.3rem 0 0.4rem 0;
    border-bottom: 1px solid #2E2E3E;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 1.0625rem;
    letter-spacing: 0.1em;
    color: #6B2D8B;
    text-transform: uppercase;
    margin-bottom: 0.25rem;
  }
  .task-phase-label {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 1.0625rem;
    color: #A0A0B0;
    letter-spacing: 0.08em;
    padding: 0.6rem 0 0.25rem 0;
    text-transform: uppercase;
  }
  /* General sidebar button font size (Load →, Sign out, New) */
  section[data-testid="stSidebar"] button {
    font-size: 1.125rem !important;
  }
  /* Sidebar section collapse headers — collapsible rows, not navigation buttons */
  [data-testid="stMarkdown"]:has(.sb-sec-hdr) + [data-testid="stButton"] button {
    background: #0D0D14 !important;
    border: 1px solid #1E1E2E !important;
    border-left: 2px solid #1E1E2E !important;
    border-radius: 4px !important;
    box-shadow: none !important;
    padding: 0.4rem 0.65rem !important;
    margin-bottom: 0.2rem !important;
    text-align: left !important;
    font-family: 'IBM Plex Mono', monospace !important;
    font-size: 1.0625rem !important;
    font-weight: 400 !important;
    text-transform: uppercase !important;
    letter-spacing: 0.1em !important;
    color: #A0A0B0 !important;
    width: 100% !important;
    transition: background 0.1s, border-color 0.1s, color 0.1s !important;
  }
  [data-testid="stMarkdown"]:has(.sb-sec-hdr) + [data-testid="stButton"] button:hover {
    background: #13131A !important;
    border-color: #2E2E3E !important;
    border-left-color: #2E2E3E !important;
    color: #A0A0B0 !important;
  }
  /* Active (expanded) state — purple left accent, lighter background */
  [data-testid="stMarkdown"]:has(.sb-sec-hdr[data-open="true"]) + [data-testid="stButton"] button {
    background: #110C1A !important;
    border-color: #1E1E2E !important;
    border-left-color: #6B2D8B !important;
    color: #C8B8D8 !important;
  }
  [data-testid="stMarkdown"]:has(.sb-sec-hdr[data-open="true"]) + [data-testid="stButton"] button:hover {
    background: #160F20 !important;
    border-left-color: #7D35A3 !important;
    color: #FFFFFF !important;
  }
</style>
""", unsafe_allow_html=True)


# ── Blocker detection ────────────────────────────────────────────────────────
def _get_blockers(camp: dict) -> list:
    """Return list of human-readable blocker strings for a saved campaign dict."""
    try:
        data      = json.loads(camp.get("full_campaign_json") or "{}")
        messaging = data.get("messaging", {})
        rollout   = data.get("rollout",   {})
        ctx       = data.get("ctx",       {})
    except Exception:
        return []

    blockers = []
    product_name = (
        (ctx.get("matched_products") or [{}])[0]
        .get("name", "this product")
        .replace("Docebo ", "")
    )

    # 1. Missing customer proof
    if any(
        p.get("proof_point", "").startswith("[PROOF POINT NEEDED")
        for p in messaging.get("pillars", [])
    ):
        blockers.append(
            f"Missing customer proof: no named case study for {product_name} in Marketing Brain"
        )

    # 2. Missing benchmarks — extract metric label from "Label: Benchmark unavailable…"
    for m in rollout.get("success_metrics", []):
        if "Benchmark unavailable" in m:
            metric_label = m.split(":")[0].strip() if ":" in m else "performance metrics"
            blockers.append(
                f"Missing benchmarks: no verified performance data for {metric_label} in Marketing Brain"
            )
            break

    # 3. Missing ICP validation
    if not ctx.get("matched_personas"):
        blockers.append("Missing ICP validation, firmographic assumptions unconfirmed")

    return blockers


def _blocker_summary(camp: dict) -> str:
    """One-sentence summary of what is blocking a campaign — for sidebar display."""
    try:
        data      = json.loads(camp.get("full_campaign_json") or "{}")
        messaging = data.get("messaging", {})
        rollout   = data.get("rollout",   {})
        ctx       = data.get("ctx",       {})
    except Exception:
        return "Unable to read campaign data."

    parts = []
    product_name = (
        (ctx.get("matched_products") or [{}])[0]
        .get("name", "product")
        .replace("Docebo ", "")
    )
    if any(p.get("proof_point", "").startswith("[PROOF POINT NEEDED") for p in messaging.get("pillars", [])):
        parts.append(f"no case study for {product_name}")
    if any("Benchmark unavailable" in m for m in rollout.get("success_metrics", [])):
        parts.append("no benchmark data")
    if not ctx.get("matched_personas"):
        parts.append("ICP unconfirmed")
    if not parts:
        return "Review required before activating."
    return "Missing: " + " · ".join(parts) + "."


# ── Auth state init (must precede login gate) ─────────────────────────────────
for _ak, _av in [
    ("logged_in_user",       None),
    ("viewing_campaign_id",  None),
    ("current_campaign_db_id", None),
]:
    if _ak not in st.session_state:
        st.session_state[_ak] = _av

# ── Login gate ────────────────────────────────────────────────────────────────
if not st.session_state.logged_in_user:
    st.markdown("""
<div style="max-width:360px;margin:6rem auto 0;padding:2rem;
     background:#0D0D14;border:1px solid #1E1E2E;border-radius:8px;">
  <div style="font-family:'IBM Plex Mono',monospace;font-size:1.5rem;
       letter-spacing:0.06em;color:#6B2D8B;text-transform:uppercase;
       margin-bottom:1.25rem;">Campaign Builder Agent</div>
  <div style="font-size:1.125rem;color:#A0A0B0;margin-bottom:1.5rem;">
    Sign in to access the pipeline.
  </div>
</div>
""", unsafe_allow_html=True)
    _lu = st.text_input("Username", key="login_username")
    _lp = st.text_input("Password", type="password", key="login_password")
    if st.button("Sign in", key="login_btn"):
        if _check_creds(_lu, _lp):
            st.session_state.logged_in_user = _lu.strip().lower()
            st.rerun()
        else:
            st.error("Invalid credentials.")
    st.stop()


# ── Campaign status constants ─────────────────────────────────────────────────
# Sidebar display order. Campaigns with system-derived statuses outside this list
# (Blocked, Incomplete) fall into the Hypothesis bucket via the .get() fallback.
_STATUSES       = ["Execution Ready", "Hypothesis", "In Review", "Pending", "Complete"]
_HUMAN_STATUSES = ["In Review", "Pending", "Complete"]  # manually selectable; system assigns the rest
_STATUS_C  = {
    "Execution Ready": "#3D7B5B",
    "Hypothesis":      "#8B7B3D",
    "In Review":       "#3D6B8B",
    "Pending":         "#3D5B8B",
    "Complete":        "#6B2D8B",
    # legacy / derive-status fallback; not shown as sidebar sections
    "Draft":           "#3D5B8B",
    "Blocked":         "#8B3D3D",
    "Incomplete":      "#8B6B2D",
}
_STATUS_EMOJI = {
    "Execution Ready": "🟢",
    "Hypothesis":      "🟡",
    "In Review":       "🔵",
    "Pending":         "🔵",
    "Complete":        "⚪",
    "Draft":           "🔵",
    "Blocked":         "🔴",
    "Incomplete":      "🟠",
}


def _days_ago(iso_str: str) -> str:
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        d = (datetime.now(timezone.utc) - dt).days
        return "today" if d == 0 else f"{d}d ago"
    except Exception:
        return ""

def _fmt_date(iso_str: str) -> str:
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.strftime("%b %-d")
    except Exception:
        return ""


def _render_saved_campaign_detail(camp_id: str):
    camp = _db_get_one(camp_id)
    if not camp:
        st.error("Campaign not found.")
        if st.button("← Back to pipeline"):
            st.session_state.viewing_campaign_id = None
            st.rerun()
        return

    stored    = json.loads(camp.get("full_campaign_json") or "{}")
    structure = stored.get("structure", {})
    messaging = stored.get("messaging", {})
    rollout   = stored.get("rollout",   {})

    # ── System status (read-only, derived from scores) ────────────────────────
    _h_rd_struct = stored.get("readiness_structure_score", 0) or 0
    _h_rd_evid   = stored.get("readiness_evidence_score",  0) or 0
    if _h_rd_struct >= 70 and _h_rd_evid >= 70:
        _h_sys_status = "Execution Ready"
    elif _h_rd_struct >= 70:
        _h_sys_status = "Hypothesis"
    elif _h_rd_evid >= 70:
        _h_sys_status = "Incomplete"
    else:
        _h_sys_status = "Blocked"
    _h_sys_sc    = _STATUS_C.get(_h_sys_status, "#606070")
    _h_sys_emoji = _STATUS_EMOJI.get(_h_sys_status, "")

    # ── Header banner ─────────────────────────────────────────────────────────
    _hcols = st.columns([3, 1, 1])
    with _hcols[0]:
        st.markdown(
            f'<div style="font-size:1.0625rem;color:#A0A0B0;margin-bottom:0.15rem;">'
            f'Saved campaign, read only</div>'
            f'<div style="font-size:1.35rem;color:#FFFFFF;font-weight:600;">'
            f'{camp["product"] or "Campaign"}'
            f'&nbsp;&nbsp;<span style="font-size:1.0625rem;font-weight:400;color:{_h_sys_sc};">'
            f'{_h_sys_emoji} {_h_sys_status}</span></div>'
            f'<div style="font-size:1.0625rem;color:#909098;margin-top:0.25rem;">'
            f'{camp["goal"] or ""}'
            + (f' · {camp["timeline"]}w' if camp.get("timeline") else "")
            + f' · {_days_ago(camp["created_at"])}</div>',
            unsafe_allow_html=True,
        )
    with _hcols[1]:
        _stored_s = camp["status"] or ""
        _cur_human_s = _stored_s if _stored_s in _HUMAN_STATUSES else "Pending"
        _sel_idx = _HUMAN_STATUSES.index(_cur_human_s)
        _new_s = st.selectbox(
            "Status",
            options=_HUMAN_STATUSES,
            index=_sel_idx,
            key=f"status_sel_{camp_id}",
            label_visibility="collapsed",
        )
        if _new_s != _stored_s:
            _db_update_status(camp_id, _new_s)
            st.rerun()
    with _hcols[2]:
        st.markdown('<div style="height:1.6rem;"></div>', unsafe_allow_html=True)
        if st.button("Re-run brief", key=f"rerun_{camp_id}"):
            st.session_state.brief_input           = camp.get("brief_text", "")
            st.session_state.viewing_campaign_id   = None
            st.session_state.pipeline_results      = None
            st.session_state.enriched_brief        = ""
            st.session_state.current_campaign_db_id = None
            st.rerun()

    if camp.get("asana_url"):
        st.markdown(
            f'<div style="font-size:1.0625rem;margin-top:0.4rem;">'
            f'<a href="{camp["asana_url"]}" target="_blank" '
            f'style="color:#6B2D8B;text-decoration:none;">Open in Asana →</a></div>',
            unsafe_allow_html=True,
        )

    st.markdown('<hr style="border-color:#1E1E2E;margin:0.75rem 0;">', unsafe_allow_html=True)

    # ── Readiness banner ──────────────────────────────────────────────────────
    _rd_score  = stored.get("readiness_score")
    _rd_struct = stored.get("readiness_structure_score", 0) or 0
    _rd_evid   = stored.get("readiness_evidence_score",  0) or 0
    if _rd_score is not None:
        # System-assigned status from scores
        if _rd_struct >= 70 and _rd_evid >= 70:
            _rd_status = "Execution Ready"
        elif _rd_struct >= 70:
            _rd_status = "Hypothesis"
        elif _rd_evid >= 70:
            _rd_status = "Incomplete"
        else:
            _rd_status = "Blocked"
        # Derive missing items
        _rd_missing = []
        _rd_pillars = messaging.get("pillars", [])
        _rd_named_missing = bool(_rd_pillars) and any(
            p.get("proof_point", "").startswith("[PROOF POINT NEEDED")
            for p in _rd_pillars
        )
        if _rd_named_missing:
            _rd_missing.append("named proof points")
        _rd_pct_re = re.compile(r'\d+(?:\.\d+)?\s*%')
        _rd_bench  = any(
            _rd_pct_re.search(pp.get("claim", ""))
            for pp in stored.get("ctx", {}).get("proof_points", [])
        )
        if not _rd_bench:
            _rd_missing.append("benchmark data")
        _rd_named_pts = 0 if _rd_named_missing else 50
        _rd_bench_pts = 25 if _rd_bench else 0
        if (_rd_evid - _rd_named_pts - _rd_bench_pts) < 25:
            _rd_missing.append("ICP firmographics")
        _rd_miss_str = ", ".join(_rd_missing) if _rd_missing else "none"
        _rd_sc = _STATUS_C.get(_rd_status, "#606070")
        st.markdown(
            f'<div style="font-size:1.0625rem;color:#8080A0;'
            f'font-family:\'IBM Plex Mono\',monospace;'
            f'padding:0.3rem 0;margin-bottom:0.5rem;line-height:1.6;">'
            f'Readiness: <span style="color:#A0A0B0;">{_rd_score}/100</span>'
            f' &nbsp;·&nbsp; <span style="color:{_rd_sc};">{_rd_status}</span>'
            f' &nbsp;·&nbsp; Missing: {_rd_miss_str}.</div>',
            unsafe_allow_html=True,
        )

    # ── Positioning ───────────────────────────────────────────────────────────
    _ps = messaging.get("positioning_statement", "")
    if _ps:
        st.markdown(f"""
<div class="section-card">
  <div class="section-label">Positioning</div>
  <div class="positioning">{_ps}</div>
</div>
""", unsafe_allow_html=True)

    # ── Messaging pillars ─────────────────────────────────────────────────────
    pillars = messaging.get("pillars", [])
    if pillars:
        _pills_html = ""
        for _pi, _p in enumerate(pillars, 1):
            _pp_color = "#FFFFFF" if str(_p.get("proof_point","")).startswith("[PROOF POINT NEEDED") else "#3D7B5B"
            _pills_html += (
                f'<div style="border:1px solid #1E1E2E;border-radius:6px;'
                f'padding:0.75rem 1rem;margin-bottom:0.6rem;background:#0D0D14;">'
                f'<div style="font-family:\'IBM Plex Mono\',monospace;font-size:1.0625rem;'
                f'color:#6B2D8B;text-transform:uppercase;margin-bottom:0.25rem;">Pillar {_pi}</div>'
                f'<div style="font-size:1.15rem;color:#FFFFFF;font-weight:600;margin-bottom:0.2rem;">'
                f'{_p.get("title","")}</div>'
                f'<div style="font-size:1.125rem;color:#C8C8D8;margin-bottom:0.35rem;">'
                f'{_p.get("one_liner","")}</div>'
                f'<div style="font-size:1.125rem;color:{_pp_color};">'
                f'{_p.get("proof_point","")}</div>'
                f'</div>'
            )
        st.markdown(f"""
<div class="section-card">
  <div class="section-label">Messaging Pillars</div>
  {_pills_html}
</div>
""", unsafe_allow_html=True)

    # ── CTAs ──────────────────────────────────────────────────────────────────
    ctas = messaging.get("cta_by_persona", {})
    if ctas:
        _cta_rows = "".join(
            f'<div style="display:flex;gap:0.75rem;padding:0.3rem 0;'
            f'border-bottom:1px solid #111118;">'
            f'<span style="font-size:1.125rem;color:#A0A0B0;min-width:160px;flex-shrink:0;">'
            f'{persona}</span>'
            f'<span style="font-size:1.125rem;color:#C8C8D8;">{cta}</span></div>'
            for persona, cta in ctas.items()
        )
        st.markdown(f"""
<div class="section-card">
  <div class="section-label">CTA by Persona</div>
  {_cta_rows}
</div>
""", unsafe_allow_html=True)

    # ── Asset plan ────────────────────────────────────────────────────────────
    assets = messaging.get("asset_plan", [])
    if assets:
        _asset_rows = "".join(
            f'<tr>'
            f'<td style="padding:0.3rem 0.6rem;font-size:1.125rem;color:#C8C8D8;border-bottom:1px solid #111118;">{a.get("asset_type","")}</td>'
            f'<td style="padding:0.3rem 0.6rem;font-size:1.125rem;color:#909098;border-bottom:1px solid #111118;">{a.get("format","")}</td>'
            f'<td style="padding:0.3rem 0.6rem;font-size:1.125rem;color:#6B2D8B;font-family:\'IBM Plex Mono\',monospace;border-bottom:1px solid #111118;">{a.get("owner","")}</td>'
            f'<td style="padding:0.3rem 0.6rem;font-size:1.125rem;color:#909098;border-bottom:1px solid #111118;">{a.get("purpose","")}</td>'
            f'</tr>'
            for a in assets
        )
        st.markdown(f"""
<div class="section-card">
  <div class="section-label">Asset Plan</div>
  <table style="width:100%;border-collapse:collapse;background:#080810;margin-top:0.4rem;">
    <thead><tr>
      <th style="padding:0.25rem 0.6rem;text-align:left;font-size:1.0625rem;font-family:'IBM Plex Mono',monospace;color:#8080A0;text-transform:uppercase;border-bottom:1px solid #2A1A3A;">Asset</th>
      <th style="padding:0.25rem 0.6rem;text-align:left;font-size:1.0625rem;font-family:'IBM Plex Mono',monospace;color:#8080A0;text-transform:uppercase;border-bottom:1px solid #2A1A3A;">Format</th>
      <th style="padding:0.25rem 0.6rem;text-align:left;font-size:1.0625rem;font-family:'IBM Plex Mono',monospace;color:#8080A0;text-transform:uppercase;border-bottom:1px solid #2A1A3A;">Owner</th>
      <th style="padding:0.25rem 0.6rem;text-align:left;font-size:1.0625rem;font-family:'IBM Plex Mono',monospace;color:#8080A0;text-transform:uppercase;border-bottom:1px solid #2A1A3A;">Purpose</th>
    </tr></thead>
    <tbody>{_asset_rows}</tbody>
  </table>
</div>
""", unsafe_allow_html=True)

    # ── Rollout phases ────────────────────────────────────────────────────────
    phases = rollout.get("phases", [])
    if phases:
        _phase_html = ""
        for _phi, _ph in enumerate(phases, 1):
            _task_rows = "".join(
                f'<div style="display:flex;gap:0.75rem;padding:0.25rem 0;'
                f'border-bottom:1px solid #0D0D18;">'
                f'<span style="font-size:1.0625rem;color:#C0C0D0;flex:1;">{t.get("task","")}</span>'
                f'<span style="font-size:1.0625rem;color:#6B2D8B;font-family:\'IBM Plex Mono\',monospace;'
                f'min-width:90px;flex-shrink:0;">{t.get("owner","")}</span>'
                f'<span style="font-size:1.0625rem;color:#8080A0;min-width:50px;flex-shrink:0;text-align:right;">'
                f'Day {t.get("due_day","?")}</span>'
                f'</div>'
                for t in _ph.get("tasks", [])
            )
            _phase_html += (
                f'<div style="margin-bottom:0.75rem;">'
                f'<div style="font-family:\'IBM Plex Mono\',monospace;font-size:1.0625rem;'
                f'color:#6B2D8B;text-transform:uppercase;margin-bottom:0.15rem;">Phase {_phi}</div>'
                f'<div style="font-size:1.0625rem;color:#FFFFFF;font-weight:500;margin-bottom:0.1rem;">'
                f'{_ph.get("name","")}</div>'
                + (f'<div style="font-size:1.0625rem;color:#8B7B3D;margin-bottom:0.35rem;">'
                   f'Milestone: {_ph["milestone"]}</div>' if _ph.get("milestone") else "")
                + f'<div style="padding:0.2rem 0;">{_task_rows}</div></div>'
            )
        _metrics = rollout.get("success_metrics", [])
        _m_html  = "".join(
            f'<div style="font-size:1.0625rem;color:#C8C8D8;padding:0.2rem 0;">{m}</div>'
            for m in _metrics
        )
        _metrics_block = (
            '<div style="font-family:\'IBM Plex Mono\',monospace;font-size:1.0625rem;color:#8080A0;'
            'text-transform:uppercase;margin-top:0.5rem;margin-bottom:0.2rem;">Success Metrics</div>'
            + _m_html
        ) if _metrics else ""
        st.markdown(f"""
<div class="section-card">
  <div class="section-label">Rollout Plan</div>
  {_phase_html}
  {_metrics_block}
</div>
""", unsafe_allow_html=True)


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:

    # ── User row + New Campaign ───────────────────────────────────────────────
    _sb_user = st.session_state.logged_in_user or ""
    _sb_cols = st.columns([3, 2])
    with _sb_cols[0]:
        st.markdown(
            f'<div style="font-family:\'IBM Plex Mono\',monospace;font-size:1.0625rem;'
            f'color:#A0A0B0;text-transform:uppercase;letter-spacing:0.1em;'
            f'padding-top:1.1rem;">{_sb_user}</div>',
            unsafe_allow_html=True,
        )
    with _sb_cols[1]:
        st.markdown('<div style="height:0.6rem;"></div>', unsafe_allow_html=True)
        if st.button("+ New", key="new_campaign_btn", help="Start a new campaign"):
            st.session_state.viewing_campaign_id    = None
            st.session_state.pipeline_results       = None
            st.session_state.enriched_brief         = ""
            st.session_state.current_campaign_db_id = None
            st.session_state.brief_input            = ""
            st.session_state.intake_qa_log          = []
            st.rerun()

    # ── Marketing Brain Architecture ──────────────────────────────────────────
    try:
        try:
            from knowledge.neo4j_connection import is_available as _neo4j_ok
            if _neo4j_ok():
                from knowledge.neo4j_viz import graph_stats_neo4j as _graph_stats_fn
                _brain_source = "Neo4j"
            else:
                raise RuntimeError("neo4j off")
        except Exception:
            from knowledge.graph_viz import graph_stats
            _graph_stats_fn = graph_stats
            _brain_source = "NetworkX"

        _stats = _graph_stats_fn()
        _source_badge = (
            f'<span style="font-size:1.0625rem;color:#9B5DBB;font-family:\'IBM Plex Mono\',monospace;'
            f'border:1px solid #3A1A4A;border-radius:3px;padding:1px 5px;margin-left:6px;">'
            f'{_brain_source}</span>'
        )
        st.markdown(f"""
<div style="border-top:1px solid #1E1E2E;padding-top:1rem;margin-top:0.5rem;">
  <div style="font-family:'IBM Plex Mono',monospace;font-size:1.0625rem;letter-spacing:0.12em;
       color:#6B2D8B;text-transform:uppercase;margin-bottom:0.5rem;">
    Marketing Brain Architecture{_source_badge}
  </div>
</div>
""", unsafe_allow_html=True)
        _nd        = _stats.get("nodes", {})
        _gap_count = _stats.get("proof_gaps", 0)
        _gap_icon  = "&#9888;" if _gap_count > 0 else "&#10003;"
        _gap_color = "#8B3D3D" if _gap_count > 0 else "#3D7B5B"
        st.markdown(f"""
<div style="background:#0D0D14;border:1px solid #1E1E2E;border-radius:4px;
     padding:0.6rem 0.8rem;margin-bottom:0.6rem;">
  <div style="display:flex;justify-content:space-between;padding:0.2rem 0;
       border-bottom:1px solid #1E1E2E;font-size:1.1rem;">
    <span style="color:#A0A0B0;">Personas</span>
    <span style="color:#A0A0B0;">{_nd.get("Persona", 0)}</span>
  </div>
  <div style="display:flex;justify-content:space-between;padding:0.2rem 0;
       border-bottom:1px solid #1E1E2E;font-size:1.1rem;">
    <span style="color:#A0A0B0;">Channels</span>
    <span style="color:#A0A0B0;">{_nd.get("Channel", 0)}</span>
  </div>
  <div style="display:flex;justify-content:space-between;padding:0.2rem 0;
       border-bottom:1px solid #1E1E2E;font-size:1.1rem;">
    <span style="color:#A0A0B0;">Products</span>
    <span style="color:#A0A0B0;">{_nd.get("Product", 0)}</span>
  </div>
  <div style="display:flex;justify-content:space-between;padding:0.2rem 0;
       border-bottom:1px solid #1E1E2E;font-size:1.1rem;">
    <span style="color:#A0A0B0;">Proof Gaps</span>
    <span style="color:{_gap_color};font-weight:600;">{_gap_icon} {_gap_count}</span>
  </div>
  <div style="display:flex;justify-content:space-between;padding:0.35rem 0 0;font-size:1.1rem;">
    <span style="color:#6B2D8B;font-family:'IBM Plex Mono',monospace;">{_stats['total_edges']} edges</span>
    <span style="color:#A0A0B0;">{_stats['total_nodes']} nodes</span>
  </div>
</div>
""", unsafe_allow_html=True)
    except Exception as _viz_err:
        st.markdown(
            f'<div style="font-size:1.1rem;color:#A0A0B0;">Graph unavailable: {_viz_err}</div>',
            unsafe_allow_html=True,
        )

    # ── Asana credentials ─────────────────────────────────────────────────────
    st.markdown('<div style="border-top:1px solid #1E1E2E;margin-top:0.5rem;"></div>',
                unsafe_allow_html=True)
    with st.expander("Asana Settings", expanded=False):
        _sb_asana_token = st.text_input(
            "Personal Access Token",
            value=st.session_state.get("asana_sidebar_token", ""),
            type="password",
            placeholder="Paste your Asana PAT…",
            key="asana_pat_input",
            help="Get yours at asana.com/0/my-apps → Personal access tokens",
        )
        if st.button("Connect", key="asana_connect_btn"):
            if _sb_asana_token.strip():
                from builders.asana_builder import fetch_workspace_gid as _fetch_ws
                _ws = _fetch_ws(_sb_asana_token.strip())
                if _ws:
                    st.session_state.asana_sidebar_token     = _sb_asana_token.strip()
                    st.session_state.asana_sidebar_workspace = _ws
                    st.rerun()
                else:
                    st.error("Could not reach Asana — check your token.")
            else:
                st.session_state.asana_sidebar_token     = ""
                st.session_state.asana_sidebar_workspace = ""
                st.rerun()
        if st.session_state.get("asana_sidebar_workspace", ""):
            st.markdown(
                f'<div style="font-size:1.0625rem;color:#3D7B5B;margin-top:0.3rem;">'
                f'Connected · workspace {st.session_state.get("asana_sidebar_workspace", "")}</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div style="font-size:1.0625rem;color:#A0A0B0;margin-top:0.3rem;">'
                'Not configured — using default workspace</div>',
                unsafe_allow_html=True,
            )

    # ── Backfill readiness scores for legacy campaigns (once per session) ────────
    if _sb_user and not st.session_state.get("_readiness_backfill_done"):
        try:
            _db_backfill(_sb_user)
        except Exception:
            pass
        st.session_state["_readiness_backfill_done"] = True

    # ── Pipeline summary ──────────────────────────────────────────────────────
    try:
        _sb_total, _sb_ready, _sb_blocked = _db_summary(_sb_user)
    except Exception:
        _sb_total = _sb_ready = _sb_blocked = 0

    st.markdown(f"""
<div style="display:flex;gap:0.4rem;margin-bottom:0.5rem;">
  <div style="flex:1;background:#0D0D14;border:1px solid #1E1E2E;border-radius:4px;
       padding:0.4rem 0.5rem;text-align:center;">
    <div style="font-size:1.35rem;color:#FFFFFF;font-weight:600;">{_sb_total}</div>
    <div style="font-size:1.0625rem;color:#A0A0B0;font-family:'IBM Plex Mono',monospace;">Total</div>
  </div>
  <div style="flex:1;background:#0D0D14;border:1px solid #1E1E2E;border-radius:4px;
       padding:0.4rem 0.5rem;text-align:center;">
    <div style="font-size:1.35rem;color:#3D7B5B;font-weight:600;">{_sb_ready}</div>
    <div style="font-size:1.0625rem;color:#A0A0B0;font-family:'IBM Plex Mono',monospace;">Ready</div>
  </div>
  <div style="flex:1;background:#0D0D14;border:1px solid #2A1A1A;border-radius:4px;
       padding:0.4rem 0.5rem;text-align:center;">
    <div style="font-size:1.35rem;color:#8B3D3D;font-weight:600;">{_sb_blocked}</div>
    <div style="font-size:1.0625rem;color:#A0A0B0;font-family:'IBM Plex Mono',monospace;">Blocked</div>
  </div>
</div>
""", unsafe_allow_html=True)

    # ── Campaign pipeline list ────────────────────────────────────────────────
    try:
        _sb_camps = _db_get_all(_sb_user)
    except Exception:
        _sb_camps = []

    _sb_by_status = {s: [] for s in _STATUSES}
    for _c in _sb_camps:
        _c_eff_status = _db_derive_status(
            _c.get("full_campaign_json", ""), _c.get("status", "Draft")
        )
        _sb_by_status.get(_c_eff_status, _sb_by_status["Hypothesis"]).append(_c)

    for _sb_status in _STATUSES:
        _sb_group = _sb_by_status[_sb_status]
        _sb_sc    = _STATUS_C.get(_sb_status, "#606070")
        _sb_ekey  = f"sb_exp_{_sb_status.replace(' ', '_')}"
        _sb_open  = st.session_state.get(_sb_ekey, False)
        _sb_arr   = "▾" if _sb_open else "▸"

        # Marker div — CSS uses :has(.sb-sec-hdr[data-status=...]) to colour
        # the adjacent toggle button without affecting Load/New/Sign-out buttons.
        st.markdown(
            f'<div class="sb-sec-hdr" data-status="{_sb_status}" data-open="{str(_sb_open).lower()}" '
            f'style="margin-top:0.4rem;"></div>',
            unsafe_allow_html=True,
        )
        if st.button(
            f"{_sb_arr} {_sb_status} ({len(_sb_group)})",
            key=f"sb_toggle_{_sb_status.replace(' ', '_')}",
            use_container_width=True,
        ):
            st.session_state[_sb_ekey] = not _sb_open
            st.rerun()

        if _sb_open:
            if not _sb_group:
                st.markdown(
                    '<div style="font-size:1.0625rem;color:#8080A0;'
                    'font-family:\'IBM Plex Mono\',monospace;'
                    'padding:0.3rem 0.4rem;">No campaigns</div>',
                    unsafe_allow_html=True,
                )
            for _c in _sb_group:
                _is_sel  = st.session_state.get("viewing_campaign_id") == _c["id"]
                _bg      = "#151520" if _is_sel else "transparent"
                _bd      = f"1px solid {_sb_sc}" if _is_sel else "1px solid transparent"
                _sb_em   = _STATUS_EMOJI.get(_sb_status, "")
                _c_prod  = _c.get("product") or "Campaign"
                _c_goal  = _c.get("goal") or ""
                _c_date  = _fmt_date(_c["created_at"])
                _c_name_parts = [_c_prod]
                if _c_goal:
                    _c_name_parts.append(_c_goal)
                if _c_date:
                    _c_name_parts.append(_c_date)
                _c_name = " · ".join(_c_name_parts)
                st.markdown(
                    f'<div style="background:{_bg};border:{_bd};border-radius:4px;'
                    f'padding:0.35rem 0.4rem;margin-bottom:0.15rem;">'
                    f'<div style="font-size:1.07rem;color:#C8C8D8;font-weight:500;'
                    f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">'
                    f'{_c_name}</div>'
                    f'<div style="font-size:1.0625rem;color:{_sb_sc};'
                    f'font-family:\'IBM Plex Mono\',monospace;margin-top:0.1rem;">'
                    f'{_sb_em} {_sb_status}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                if st.button("Load →", key=f"load_{_c['id']}"):
                    st.session_state.viewing_campaign_id = _c["id"]
                    st.rerun()

    # ── Sign out ──────────────────────────────────────────────────────────────
    st.markdown('<div style="margin-top:1.5rem;border-top:1px solid #1E1E2E;padding-top:0.75rem;"></div>', unsafe_allow_html=True)
    if st.button("Sign out", key="signout_btn"):
        for _sk in list(st.session_state.keys()):
            del st.session_state[_sk]
        st.rerun()


# ── Header ────────────────────────────────────────────────────────────────────
st.markdown("""
<div class="agent-header">
  <h1>Campaign Builder Agent</h1>
  <p>Brief → Messaging → Rollout &nbsp;·&nbsp; Crawl version</p>
</div>
""", unsafe_allow_html=True)


# ── Viewing mode (read-only saved campaign) ───────────────────────────────────
if st.session_state.get("viewing_campaign_id"):
    _render_saved_campaign_detail(st.session_state.viewing_campaign_id)
    st.stop()

# ── Load sample brief ─────────────────────────────────────────────────────────
try:
    with open("sample_brief.txt", "r") as f:
        sample = f.read()
except FileNotFoundError:
    sample = ""


# ── Input ─────────────────────────────────────────────────────────────────────
col1, col2 = st.columns([3, 1])
with col1:
    brief = st.text_area(
        "Campaign Brief",
        value="",
        height=100,
        placeholder="What are you launching? One sentence is enough.",
        label_visibility="collapsed",
        key="brief_input",
    )
    _char_count = len(brief)
    if _char_count > 0:
        _over_limit = _char_count > 1000
        _counter_color = "#8B4040" if _over_limit else "#606070"
        _counter_msg = (
            f"{_char_count}/1000 characters &nbsp;·&nbsp; "
            "Brief is long. Agent will use the first 1000 characters."
            if _over_limit else f"{_char_count}/1000 characters"
        )
        st.markdown(
            f'<div style="font-size:1.0625rem;color:{_counter_color};margin-top:0.2rem;">'
            f'{_counter_msg}</div>',
            unsafe_allow_html=True
        )

with col2:
    st.markdown("""
<div style="height: 0.5rem"></div>
<div class="section-label">Output sections</div>
<div style="font-size: 1.0625rem; color: #A0A0B0; line-height: 2;">
  <div>Positioning statement</div>
  <div>Messaging pillars × 3</div>
  <div>CTA by persona</div>
  <div>Asset plan</div>
  <div>Phased rollout</div>
  <div>Success metrics</div>
  <div style="color: #8B6B2D;">⬡ Human review checkpoints</div>
</div>
""", unsafe_allow_html=True)

# ── Session state ─────────────────────────────────────────────────────────────
for _k, _v in [
    ("clarifying",            False),
    ("intake_question",       ""),
    ("intake_reasoning",      ""),
    ("original_brief",        ""),
    ("explicit_brief",        ""),   # all user-provided inputs (original + clarification answers)
    ("enriched_brief",        ""),
    ("pipeline_results",      None),
    ("pptx_bytes",            None),
    ("asana_url",             None),
    ("asana_error",           ""),
    ("intake_rounds",         0),   # questions shown so far; max 2 before forced proceed
    ("brief_invalid_reason",  ""),
    ("campaign_save_status",  "Draft"),
    ("intake_qa_log",         []),  # persists follow-up Q&A after pipeline runs
    ("confirm_timeline",      False),
    ("timeline_outlier_msg",  ""),
    ("product_unmatched",       False),
    ("product_unmatched_stated", ""),
    ("persona_unmatched",       False),
    ("persona_unmatched_stated", ""),
    ("asana_sidebar_token",      ""),
    ("asana_sidebar_workspace",  ""),
]:
    if _k not in st.session_state:
        st.session_state[_k] = _v

run = st.button("Run Agent →")
st.markdown(
    '<div style="font-size:1.0625rem;color:#A0A0B0;margin-top:0.35rem;letter-spacing:0.04em;">'
    'The agent will ask if it needs more context.</div>',
    unsafe_allow_html=True
)


# ── Intake ────────────────────────────────────────────────────────────────────
if run and brief.strip():
    st.session_state.clarifying              = False
    st.session_state.confirm_timeline        = False
    st.session_state.timeline_outlier_msg    = ""
    st.session_state.product_unmatched        = False
    st.session_state.product_unmatched_stated = ""
    st.session_state.persona_unmatched        = False
    st.session_state.persona_unmatched_stated = ""
    st.session_state.enriched_brief          = ""
    st.session_state.pipeline_results        = None
    st.session_state.pptx_bytes              = None
    st.session_state.asana_url               = None
    st.session_state.asana_error             = ""
    st.session_state.original_brief          = brief
    st.session_state.explicit_brief          = brief   # starts as the raw user input
    st.session_state.intake_rounds           = 0
    st.session_state.brief_invalid_reason    = ""
    st.session_state.current_campaign_db_id  = None
    st.session_state.viewing_campaign_id     = None
    st.session_state.campaign_save_status    = "Draft"

    _gate = is_valid_docebo_brief(brief)
    if not _gate["valid"]:
        st.session_state.brief_invalid_reason = _gate.get("reason", "")
    else:
        _tl_outlier_m = re.search(r'\b(\d+)\s+weeks?\b', brief, re.IGNORECASE)
        if _tl_outlier_m and int(_tl_outlier_m.group(1)) > 12:
            _tl_wks_raw = int(_tl_outlier_m.group(1))
            st.session_state.confirm_timeline     = True
            st.session_state.timeline_outlier_msg = (
                f"You entered {_tl_wks_raw} weeks. Most campaigns in the Marketing Brain"
                f"run 2 to 4 weeks. Did you mean 2 to 3 weeks, or is this a longer nurture program?"
            )
        else:
            _persona_gate = check_persona_match(brief)
            if not _persona_gate["matched"]:
                st.session_state.persona_unmatched        = True
                st.session_state.persona_unmatched_stated = _persona_gate["stated"]
            else:
                with st.spinner("Checking brief…"):
                    intake = run_intake(brief)

                if intake.get("proceed"):
                    st.session_state.enriched_brief = intake["enriched_brief"]
                else:
                    st.session_state.intake_rounds    = 1
                    st.session_state.clarifying       = True
                    st.session_state.intake_question  = intake["question"]
                    st.session_state.intake_reasoning = intake.get("reasoning", "")

elif run:
    st.warning("Paste a campaign brief to continue.")

# ── Pre-validation redirect ───────────────────────────────────────────────────
if st.session_state.brief_invalid_reason:
    st.markdown(f"""
<div class="section-card" style="border: none; border-left: 3px solid #8B4040; margin-top: 1rem;">
  <div class="section-label" style="color: #8B4040;">NOT A DOCEBO CAMPAIGN BRIEF</div>
  <div style="font-size: 1.2rem; color: #FFFFFF; line-height: 1.65; margin-bottom: 0.5rem;">
    This doesn't look like a Docebo marketing brief. The Campaign Builder Agent is designed
    for Docebo product campaigns. Try something like
    <em>"Launch AgentHub to existing customers"</em> or
    <em>"Drive awareness for Skills Intelligence among L&amp;D leaders."</em>
  </div>
</div>
""", unsafe_allow_html=True)


# ── Timeline outlier confirmation UI ─────────────────────────────────────────
if st.session_state.confirm_timeline:
    _tl_q = st.session_state.timeline_outlier_msg
    st.markdown(f"""
<div class="section-card" style="border: none; border-left: 3px solid #6B2D8B; margin-top: 1rem;">
  <div class="section-label" style="color: #6B2D8B;">CONFIRM TIMELINE</div>
  <div style="font-size: 1.25rem; font-weight: 500; color: #FFFFFF; line-height: 1.65;">{_tl_q}</div>
</div>
""", unsafe_allow_html=True)

    _tl_answer = st.text_input(
        "", placeholder="e.g. '2 weeks' or 'yes, 23 weeks is correct'",
        label_visibility="collapsed", key="timeline_confirm_input"
    )
    _tl_cont = st.button("Continue →", key="timeline_confirm_btn")
    if _tl_cont and _tl_answer.strip():
        _tl_ans = _tl_answer.strip()
        _brief_for_intake = st.session_state.original_brief
        # If the user gave a corrected number of weeks, substitute it in the brief
        _tl_corr_m = re.search(r'\b(\d+(?:[–\-]\d+)?)\s+weeks?\b', _tl_ans, re.IGNORECASE)
        if _tl_corr_m and not re.search(r'\byes\b|\bcorrect\b|\bintentional\b|\bnurture\b|\blong\b', _tl_ans, re.IGNORECASE):
            _brief_for_intake = re.sub(
                r'\b\d+\s+weeks?\b', _tl_corr_m.group(0), _brief_for_intake, count=1, flags=re.IGNORECASE
            )
        else:
            _brief_for_intake = f"{_brief_for_intake}\n\nAdditional context: {_tl_ans}"
        st.session_state.explicit_brief = _brief_for_intake
        st.session_state.intake_qa_log.append({
            "question": _tl_q,
            "answer":   _tl_ans,
        })
        st.session_state.confirm_timeline = False
        with st.spinner("Checking brief…"):
            _tl_intake = run_intake(_brief_for_intake)
        if _tl_intake.get("proceed"):
            st.session_state.enriched_brief = _tl_intake["enriched_brief"]
        else:
            st.session_state.intake_rounds    = 1
            st.session_state.clarifying       = True
            st.session_state.intake_question  = _tl_intake["question"]
            st.session_state.intake_reasoning = _tl_intake.get("reasoning", "")
        st.rerun()


# ── Persona not matched in graph UI ──────────────────────────────────────────
if st.session_state.persona_unmatched:
    _pn_stated = st.session_state.persona_unmatched_stated or "the persona in your brief"
    st.markdown(f"""
<div class="section-card" style="border: none; border-left: 3px solid #8B6B2D; margin-top: 1rem;">
  <div class="section-label" style="color: #8B6B2D;">PERSONA NOT FOUND IN MARKETING BRAIN</div>
  <div style="font-size: 1.25rem; font-weight: 500; color: #FFFFFF; line-height: 1.65;">
    I could not match <strong>{_pn_stated}</strong> to a persona in the Marketing Brain.
    The available personas are VP of Learning &amp; Development, Chief People Officer,
    and L&amp;D Program Manager. Which would you like to target, or would you like the
    system to select the best fit based on the product?
  </div>
</div>
""", unsafe_allow_html=True)
    _PN_OPTIONS = [
        "VP of Learning & Development",
        "Chief People Officer",
        "L&D Program Manager",
        "Let the system decide based on the product",
    ]
    _pn_choice = st.selectbox("", _PN_OPTIONS, label_visibility="collapsed", key="persona_unmatched_select")
    _pn_cont   = st.button("Continue →", key="persona_unmatched_btn")
    if _pn_cont:
        _pn_orig = st.session_state.original_brief
        if _pn_choice == "Let the system decide based on the product":
            _pn_brief = _pn_orig
        else:
            _pn_brief = f"This campaign is targeting {_pn_choice}. {_pn_orig}"
        st.session_state.explicit_brief            = _pn_brief
        st.session_state.persona_unmatched         = False
        st.session_state.persona_unmatched_stated  = ""
        st.session_state.intake_qa_log.append({
            "question": (f'"{_pn_stated}" wasn\'t found in the Marketing Brain. '
                         f"Which persona should this campaign target?"),
            "answer":   _pn_choice,
        })
        with st.spinner("Checking brief…"):
            _pn_intake = run_intake(_pn_brief)
        if _pn_intake.get("proceed"):
            st.session_state.enriched_brief = _pn_intake["enriched_brief"]
        else:
            st.session_state.clarifying       = True
            st.session_state.intake_question  = _pn_intake["question"]
            st.session_state.intake_reasoning = _pn_intake.get("reasoning", "")
        st.rerun()


# ── Product not matched in graph UI ──────────────────────────────────────────
if st.session_state.product_unmatched:
    _pm_stated = st.session_state.product_unmatched_stated or "the product in your brief"
    st.markdown(f"""
<div class="section-card" style="border: none; border-left: 3px solid #8B6B2D; margin-top: 1rem;">
  <div class="section-label" style="color: #8B6B2D;">PRODUCT NOT FOUND IN GRAPH</div>
  <div style="font-size: 1.25rem; font-weight: 500; color: #FFFFFF; line-height: 1.65;">
    <strong>{_pm_stated}</strong> didn't match any product node in the Marketing Brain.
    Which Docebo product is this campaign for?
  </div>
</div>
""", unsafe_allow_html=True)
    _PM_PRODUCTS = [
        "AgentHub", "Advanced Analytics", "Content Creator", "Docebo Engage",
        "Docebo Learn", "Docebo Shape", "Enterprise Knowledge", "Harmony AI",
        "Roleplay", "Skills Intelligence",
    ]
    _pm_choice = st.selectbox("", _PM_PRODUCTS, label_visibility="collapsed", key="product_unmatched_select")
    _pm_cont   = st.button("Continue →", key="product_unmatched_btn")
    if _pm_cont:
        _pm_orig = st.session_state.original_brief
        _pm_corrected = f"This campaign is for {_pm_choice}. {_pm_orig}"
        st.session_state.explicit_brief           = _pm_corrected
        st.session_state.product_unmatched        = False
        st.session_state.product_unmatched_stated = ""
        st.session_state.intake_qa_log.append({
            "question": f'"{_pm_stated}" wasn\'t recognised by the Marketing Brain. Which product did you mean?',
            "answer":   _pm_choice,
        })
        with st.spinner("Checking brief…"):
            _pm_intake = run_intake(_pm_corrected)
        if _pm_intake.get("proceed"):
            st.session_state.enriched_brief = _pm_intake["enriched_brief"]
        else:
            st.session_state.clarifying       = True
            st.session_state.intake_question  = _pm_intake["question"]
            st.session_state.intake_reasoning = _pm_intake.get("reasoning", "")
        st.rerun()


# ── Clarifying question UI ────────────────────────────────────────────────────
if st.session_state.clarifying:
    _q  = st.session_state.intake_question
    _rs = st.session_state.intake_reasoning
    st.markdown(f"""
<div class="section-card" style="border: none; border-left: 3px solid #6B2D8B; margin-top: 1rem;">
  <div class="section-label" style="color: #6B2D8B;">ONE QUESTION BEFORE WE PROCEED</div>
  <div style="font-size: 1.13rem; color: #A0A0B0; line-height: 1.65; margin-bottom: 0.75rem;">{_rs}</div>
  <div style="font-size: 1.25rem; font-weight: 500; color: #FFFFFF; line-height: 1.65;">{_q}</div>
</div>
""", unsafe_allow_html=True)

    _clarification = st.text_input(
        "", placeholder="Your answer…",
        label_visibility="collapsed", key="clarification_input"
    )
    _cont = st.button("Continue →", key="continue_btn")
    if _cont and _clarification.strip():
        _combined = f"{st.session_state.original_brief}\n\nAdditional context: {_clarification}"
        # Accumulate all explicit user inputs so scoring can detect what was stated vs inferred
        st.session_state.explicit_brief = _combined
        # Persist the Q&A pair so it remains visible after the pipeline runs
        st.session_state.intake_qa_log.append({
            "question": _q,
            "answer":   _clarification.strip(),
        })

        if st.session_state.intake_rounds >= 2:
            # Question budget exhausted — force proceed with best inference
            with st.spinner("Building campaign…"):
                _final = run_intake(_combined, force_proceed=True)
            st.session_state.enriched_brief = _final["enriched_brief"]
            st.session_state.clarifying     = False
        else:
            with st.spinner("Updating context…"):
                _intake2 = run_intake(_combined)
            if _intake2.get("proceed"):
                st.session_state.enriched_brief = _intake2["enriched_brief"]
                st.session_state.clarifying     = False
            else:
                # Show second (and final) question
                st.session_state.intake_rounds    = 2
                st.session_state.intake_question  = _intake2["question"]
                st.session_state.intake_reasoning = _intake2.get("reasoning", "")
                st.session_state.original_brief   = _combined  # include Q1 answer in next pass
        st.rerun()


# ── Follow-up Q&A log (persists after pipeline runs) ─────────────────────────
if st.session_state.get("intake_qa_log") and st.session_state.pipeline_results:
    for _qa in st.session_state.intake_qa_log:
        st.markdown(f"""
<div style="background:#0D0D14;border:1px solid #1E1E2E;border-left:3px solid #6B2D8B;
     border-radius:4px;padding:0.75rem 1rem;margin-bottom:0.75rem;">
  <div style="font-size:1.0625rem;color:#6B2D8B;letter-spacing:0.1em;text-transform:uppercase;
       font-weight:600;margin-bottom:0.4rem;">What you told the system</div>
  <div style="font-size:1.0625rem;color:#A0A0B0;margin-bottom:0.2rem;">{_qa['question']}</div>
  <div style="font-size:1.125rem;color:#C8C8D8;">→&nbsp; {_qa['answer']}</div>
</div>
""", unsafe_allow_html=True)


# ── Pipeline ──────────────────────────────────────────────────────────────────
if st.session_state.enriched_brief and not st.session_state.clarifying:

    if st.session_state.pipeline_results is None:
        _steps = ["01 PARSING BRIEF", "02 BUILDING MESSAGING", "03 GENERATING ROLLOUT", "04 DISTILLING FOR DECK"]
        _prog  = st.empty()

        def _render_steps(active: int):
            pills = ""
            for i, s in enumerate(_steps):
                if i < active:
                    cls = "step-pill complete"
                elif i == active:
                    cls = "step-pill active"
                else:
                    cls = "step-pill"
                pills += f'<div class="{cls}">{s}</div>'
            _prog.markdown(f'<div class="step-row">{pills}</div>', unsafe_allow_html=True)

        class _ProductUnmatched(Exception):
            def __init__(self, stated): self.stated = stated

        try:
            _eb  = st.session_state.enriched_brief
            _ctx = load_knowledge_graph(_eb)

            _render_steps(0)
            _structure = extract_brief_structure(_eb, _ctx)
            # Patch ICP using the raw user input (not enriched brief) so
            # employee ranges, industry, and tech signals are always preserved.
            _raw_for_icp = st.session_state.get("explicit_brief", "") or _eb
            if _structure.get("icp"):
                _structure["icp"] = _patch_icp_from_brief(_structure["icp"], _raw_for_icp)

            if not _ctx.get("matched_products"):
                _stated_prod = _structure.get("product") or "the product in your brief"
                raise _ProductUnmatched(_stated_prod)

            _render_steps(1)
            _messaging = generate_messaging(_structure, _ctx)

            _render_steps(2)
            _rollout = generate_rollout(_structure, _messaging, _ctx)

            _render_steps(3)
            _readiness = _compute_readiness(
                _structure, _messaging, _rollout, _ctx,
                explicit_brief=st.session_state.get("explicit_brief", ""),
            )
            _deck_content = distill_for_deck({
                "structure": _structure,
                "messaging": _messaging,
                "rollout":   _rollout,
                "knowledge_context": _ctx,
            }, readiness=_readiness)

            _prog.markdown(
                '<div class="badge done">✓ COMPLETE: REVIEW BEFORE USE</div>',
                unsafe_allow_html=True
            )

            _hyp_no_proof = not any(
                not p.get("proof_point", "").startswith("[PROOF POINT NEEDED")
                for p in _messaging.get("pillars", [])
            )
            if _hyp_no_proof:
                st.markdown(
                    '<div style="background:#1E1200;border:1px solid #5A3A10;border-left:3px solid #D4860A;'
                    'border-radius:4px;padding:0.45rem 0.75rem;margin-top:1.25rem;margin-bottom:0.1rem;'
                    'font-size:1.0625rem;color:#C4860A;line-height:1.5;">'
                    'Hypothesis mode: no verified customer proof for this product.'
                    'Structural decisions are graph-verified. Messaging is provisional.'
                    '</div>',
                    unsafe_allow_html=True,
                )
            st.session_state.pipeline_results = {
                "structure":    _deep_sanitize(_structure),
                "messaging":    _deep_sanitize(_messaging),
                "rollout":      _deep_sanitize(_rollout),
                "ctx":          _ctx,
                "deck_content": _deep_sanitize(_deck_content),
                "readiness":    _deep_sanitize(_readiness),
            }

            # Generate PPTX and cache bytes
            from generate_deck import generate_pptx as _gen_pptx
            st.session_state.pptx_bytes = _gen_pptx(_deck_content)

        except _ProductUnmatched as _pum:
            st.session_state.product_unmatched        = True
            st.session_state.product_unmatched_stated = _pum.stated
            st.session_state.enriched_brief           = ""
            st.rerun()

        except Exception as e:
            st.error(f"Agent error: {str(e)}")
            st.markdown("Check that your ANTHROPIC_API_KEY is set and the brief has enough detail.")

    else:
        st.markdown(
            '<div class="badge done">✓ COMPLETE: REVIEW BEFORE USE</div>',
            unsafe_allow_html=True
        )
        _hyp_m = (st.session_state.pipeline_results or {}).get("messaging", {})
        _hyp_no_proof_cached = not any(
            not p.get("proof_point", "").startswith("[PROOF POINT NEEDED")
            for p in _hyp_m.get("pillars", [])
        )
        if _hyp_no_proof_cached:
            st.markdown(
                '<div style="background:#1E1200;border:1px solid #5A3A10;border-left:3px solid #D4860A;'
                'border-radius:4px;padding:0.45rem 0.75rem;margin-top:0.4rem;margin-bottom:0.1rem;'
                'font-size:1.0625rem;color:#C4860A;line-height:1.5;">'
                'Hypothesis mode: no verified customer proof for this product.'
                'Structural decisions are graph-verified. Messaging is provisional.'
                '</div>',
                unsafe_allow_html=True,
            )

    # ── Display results ───────────────────────────────────────────────────────
    if st.session_state.pipeline_results:
        structure = st.session_state.pipeline_results["structure"]
        messaging = st.session_state.pipeline_results["messaging"]
        rollout   = st.session_state.pipeline_results["rollout"]
        ctx       = st.session_state.pipeline_results["ctx"]
        readiness = st.session_state.pipeline_results.get("readiness") or _compute_readiness(
            structure, messaging, rollout, ctx,
            explicit_brief=st.session_state.get("explicit_brief", ""),
        )
        _eb       = st.session_state.enriched_brief

        st.markdown("---")

        # ── Action bar click state — buttons rendered at bottom after governance ─
        _ab_save_clicked  = False
        _ab_asana_clicked = False

        # ── Marketing Brain "what shaped this" expander ───────────────────────
        _eb_lower   = st.session_state.enriched_brief.lower()

        # -- product row
        _mp          = (ctx.get("matched_products") or [{}])[0]
        _mp_name     = _mp.get("name", "").replace("Docebo ", "")
        _mp_id       = _mp.get("id", "")
        _cm          = _mp.get("campaign_motion", "").lower()
        if "early access" in _cm or "pre-launch" in _cm:
            _mp_tag = "Early access pipeline"
        elif "expansion" in _cm and "not available to net-new" in _cm:
            _mp_tag = "Expansion motion"
        elif "expansion" in _cm and "net-new" in _cm:
            _mp_tag = "Expansion or net-new"
        elif "expansion" in _cm:
            _mp_tag = "Expansion motion"
        elif "net-new" in _cm:
            _mp_tag = "Acquisition motion"
        else:
            _mp_tag = ""

        # -- audience row: derive from campaign_motion, same source as _mp_tag
        _icp_brief   = structure.get("icp", "")
        if isinstance(_icp_brief, dict):
            _icp_brief = ", ".join(str(v) for v in _icp_brief.values() if v)
        if "early access" in _cm or "pre-launch" in _cm:
            _motion_label = "Early access: existing customers"
        elif "expansion" in _cm and "not available to net-new" in _cm:
            _motion_label = "Expansion (existing customers)"
        elif "expansion" in _cm and "net-new" in _cm:
            _motion_label = "Expansion or net-new"
        elif "expansion" in _cm:
            _motion_label = "Expansion (existing customers)"
        elif "net-new" in _cm:
            _motion_label = "Net-new acquisition"
        elif any(kw in _eb_lower for kw in ["existing customer", "current customer", "our customer", "upsell"]):
            _motion_label = "Expansion (existing customers)"
        else:
            _motion_label = "Net-new acquisition"

        # -- timeline / channel row
        _ch_names    = [ch["name"] for ch in ctx.get("matched_channels", [])][:3]
        _tl_wks      = structure.get("timeline_weeks", "?")
        _ch_rationale = f"{_tl_wks} weeks: {' + '.join(_ch_names) if _ch_names else 'multi-channel'} prioritized"

        # -- proof point classification
        # Green = proof point has a VALIDATES_PRODUCT edge to the matched product in the graph.
        # Amber = everything else (platform stats, or case studies without a direct product edge).
        _matched_prod_id = ((ctx.get("_matched_node_ids") or {}).get("products") or [None])[0]
        try:
            from knowledge.neo4j_connection import is_available as _n4j_pp_ok
            if _n4j_pp_ok():
                from knowledge.neo4j_connection import get_driver as _n4j_pp_drv
                _n4j_pp_driver = _n4j_pp_drv()
                with _n4j_pp_driver.session() as _s:
                    _green_pp_ids = {
                        row["id"]
                        for row in _s.run(
                            "MATCH (pp:ProofPoint)-[:VALIDATES_PRODUCT]->(prod:Product {id: $pid}) "
                            "RETURN pp.id AS id",
                            pid=_matched_prod_id or "__none__",
                        ).data()
                    }

                def _is_green(pp):
                    return pp.get("id", "") in _green_pp_ids
            else:
                raise RuntimeError("neo4j off")
        except Exception:
            try:
                from knowledge.graph_builder import build_marketing_graph as _build_g
                _G = _build_g()
                def _is_green(pp):
                    pp_id = pp.get("id", "")
                    return (
                        bool(pp_id) and bool(_matched_prod_id)
                        and _G.has_edge(pp_id, _matched_prod_id)
                        and _G[pp_id][_matched_prod_id].get("relationship") == "VALIDATES_PRODUCT"
                    )
            except Exception:
                def _is_green(pp):
                    return pp.get("source") == "Docebo case study"

        _pp_all_green = [pp for pp in ctx.get("proof_points", []) if _is_green(pp)]
        _pp_amber     = [pp for pp in ctx.get("proof_points", []) if not _is_green(pp)]

        # Within green: named customer (pillar-eligible) vs. product stat (evidence-only)
        _NAMED_PP_NAMES = [
            "Bethany Care Society", "MidFirst Bank", "Disguise",
            "SNCF", "Société Générale", "Segula Technologies",
        ]
        _pp_green_named = [pp for pp in _pp_all_green
                           if any(n in pp.get("claim", "") for n in _NAMED_PP_NAMES)]
        _pp_green_stat  = [pp for pp in _pp_all_green
                           if not any(n in pp.get("claim", "") for n in _NAMED_PP_NAMES)]

        # -- Brief Inputs: use the resolved product name from the graph node
        _brief_product_raw = _mp.get("name", _mp_name) or "Not specified"

        _g_raw = structure.get("campaign_goal", {})
        if isinstance(_g_raw, dict):
            _pg = _g_raw.get("primary", {})
            _g_t  = _pg.get("target", "")
            _g_m  = _pg.get("metric", "")
            _g_wk = structure.get("timeline_weeks", "")
            _brief_goal = (
                f"{_g_t} {_g_m}" + (f", {_g_wk} weeks" if _g_wk else "")
                if (_g_t and _g_m) else "Not specified"
            )
        else:
            _brief_goal = str(_g_raw) if _g_raw else "Not specified"

        # Brief Inputs — Audience: derive only from what the marketer wrote or confirmed.
        # Never read from structure.get("icp") which uses the enriched brief and can
        # contain graph-inferred persona titles the marketer never stated.
        _raw_eb_bi = st.session_state.get("explicit_brief", "") or ""
        _ROLE_RE_BI = re.compile(
            r'\b(VP(?:\s+(?:of\s+)?[A-Za-z&\s]{1,30})?|Director(?:\s+of\s+[A-Za-z\s]{1,20})?|'
            r'CLO|CHRO|CTO|CEO|L&D|Head\s+of\s+[A-Za-z&\s]{1,20}|'
            r'Chief\s+[A-Za-z\s]{1,20}Officer)',
            re.IGNORECASE,
        )
        _FIRM_RE_BI = re.compile(
            r'(?:\bmid-?market\b|\bSMB\b|\bsmall\s+(?:business|company|team)\b|'
            r'\blarge\s+(?:company|enterprise|organization)\b|\bstartup\b|'
            r'\b\d+\s*[\-–]?\s*\d*\s*(?:employee|person|seat|user)s?\b|'
            r'\b(?:healthcare|financial\s+service|retail|manufacturing|banking|pharma|'
            r'insurance|education)(?:s|\s+sector|\s+industry)?\b)',
            re.IGNORECASE,
        )
        _EXIST_RE_BI = re.compile(
            r'\bexisting\s+(?:\w+\s+)?(?:customers?|accounts?|users?|clients?)\b', re.IGNORECASE
        )
        _NN_RE_BI = re.compile(r'\bnet-?new\b', re.IGNORECASE)
        _ba_parts = []
        _ba_role_m = _ROLE_RE_BI.search(_raw_eb_bi)
        if _ba_role_m:
            _ba_role = re.sub(
                r'\s+(?:at|for|in|the|to|from)\b.*$', '', _ba_role_m.group(0), flags=re.IGNORECASE
            ).strip()
            if _ba_role:
                _ba_parts.append(_ba_role)
        _ba_firm_m = _FIRM_RE_BI.search(_raw_eb_bi)
        if _ba_firm_m:
            _ba_parts.append(_ba_firm_m.group(0).strip())
        if re.search(r'\b(?:early\s+access|pre-?launch)\b', _raw_eb_bi, re.IGNORECASE):
            _ba_parts.append("early access")
        elif _EXIST_RE_BI.search(_raw_eb_bi):
            _ba_parts.append("existing customers")
        elif _NN_RE_BI.search(_raw_eb_bi):
            _ba_parts.append("net-new")
        _brief_audience = ", ".join(_ba_parts) if _ba_parts else "Not specified"

        # Pre-compute an explicit-brief motion label to use in Graph Decisions when the
        # marketer stated or confirmed a motion type. Reuses regexes defined above.
        if re.search(r'\b(?:early\s+access|pre-?launch)\b', _raw_eb_bi, re.IGNORECASE):
            _brief_motion_label: str | None = "Early access: existing customers"
        elif _EXIST_RE_BI.search(_raw_eb_bi):
            _brief_motion_label = "Expansion: existing customers"
        elif _NN_RE_BI.search(_raw_eb_bi):
            _brief_motion_label = "Net-new acquisition"
        else:
            _brief_motion_label = None

        # Clean motion statement — pick highest-confidence motion from graph field
        if not _cm:
            _graph_motion = ""
        elif "early access" in _cm or "pre-launch" in _cm:
            _graph_motion = "Early access: existing customers"
        elif "expansion" in _cm and "not available to net-new" in _cm:
            _graph_motion = "Expansion: existing Docebo customers only"
        elif "expansion" in _cm and "net-new" in _cm:
            _graph_motion = "Expansion-first, net-new eligible"
        elif "expansion" in _cm:
            _graph_motion = "Expansion: existing Docebo customers"
        elif "net-new" in _cm or "acquisition" in _cm:
            _graph_motion = "Net-new acquisition"
        else:
            _graph_motion = _motion_label

        _prim_persona_early = (ctx.get("matched_personas") or [{}])[0].get("title", "")
        _ch_names_str       = ", ".join(_ch_names) if _ch_names else ""

        # Pre-compute Evidence Summary (three lines, shown once in the expander)
        if _pp_green_named:
            _ev_named = ", ".join(
                pp.get("claim", "")[:70] + ("…" if len(pp.get("claim", "")) > 70 else "")
                for pp in _pp_green_named
            )
        else:
            _ev_named = f"None retrieved for {_mp_name or 'this product'}"

        if _pp_green_stat:
            _ev_stat_parts = []
            for _evpp in _pp_green_stat:
                _evcl  = _evpp.get("claim", "")[:60] + ("…" if len(_evpp.get("claim","")) > 60 else "")
                _evsrc = _evpp.get("source", "")
                _ev_stat_parts.append(f"{_evcl} [{_evsrc}]" if _evsrc else _evcl)
            _ev_stat = ", ".join(_ev_stat_parts)
        else:
            _ev_stat = "None"

        _ev_amber_n = len(_pp_amber)
        _ev_amber = (
            f'{_ev_amber_n} industry benchmark{"s" if _ev_amber_n != 1 else ""} available, not product-specific'
            if _ev_amber_n else "None"
        )

        # ── Readiness summary line ────────────────────────────────────────────
        _rs_struct  = readiness["structure_score"]
        _rs_evid    = readiness["evidence_score"]
        _rs_total   = readiness["readiness"]
        _rs_status  = readiness["status"]
        _rs_color   = _STATUS_C.get(_rs_status, "#606070")
        _rs_emoji   = _STATUS_EMOJI.get(_rs_status, "")
        st.markdown(
            f'<div style="font-size:1.07rem;color:#A0A0B0;'
            f'font-family:\'IBM Plex Mono\',monospace;padding:0.3rem 0 0.75rem;">'
            f'Readiness: <span style="color:#C8C8D8;font-weight:600;">{_rs_total}/100</span>'
            f' &nbsp;·&nbsp; <span style="color:{_rs_color};">{_rs_emoji} {_rs_status}</span>'
            f' &nbsp;·&nbsp; <span style="color:#8080A0;font-size:1.0625rem;">'
            f'Structure {_rs_struct} · Evidence {_rs_evid}</span></div>',
            unsafe_allow_html=True,
        )

        # ── Marketing Brain Decisions — variable setup ────────────────────────
        _ps_raw = []; _ch_pmap = {}; _pp_full = []; _sem_ids = set(); _lc_cypher = ""
        try:
            from knowledge.neo4j_query import LAST_TRAVERSAL_SUMMARY as _lts, LAST_CYPHER as _lc
            _ps_raw   = _lts.get("persona_scores", [])
            _ch_pmap  = _lts.get("channel_persona_map", {})
            _pp_full  = _lts.get("pp_full_scored", [])
            _sem_ids  = set(_lts.get("semantic_fallback_ids", []))
            _lc_cypher = _lc
        except Exception:
            pass  # fallbacks already initialised above

        # Persona title lookup (covers non-selected personas from degree table)
        _pid_to_title = {r["pid"]: r.get("title") or r["pid"] for r in _ps_raw}
        for _p in ctx.get("matched_personas", []):
            _pid_to_title.setdefault(_p.get("id", ""), _p.get("title", ""))

        _prd_name_d  = (ctx.get("matched_products") or [{}])[0].get("name", "this product").replace("Docebo ", "")
        _prim_p      = (ctx.get("matched_personas") or [{}])[0]
        _prim_pid    = _prim_p.get("id", "")
        _prim_title  = _prim_p.get("title", "")
        _supp_ps     = ctx.get("matched_personas", [])[1:]

        _prim_score_row = next((_r for _r in _ps_raw if _r["pid"] == _prim_pid), {})
        _prim_degree    = _prim_score_row.get("degree", "")

        # Build chain label
        _prim_chain = f"{_prd_name_d} → TARGETS_PERSONA → {_prim_title}"
        if _prim_degree != "":
            _prim_chain += f"  ·  degree score: {_prim_degree}"

        _supp_strs = []
        for _sp in _supp_ps:
            _sp_id = _sp.get("id", "")
            _sp_t  = _pid_to_title.get(_sp_id, _sp.get("title", "?"))
            _sp_deg = next((_r["degree"] for _r in _ps_raw if _r["pid"] == _sp_id), None)
            _supp_strs.append(f"{_sp_t}" + (f" ({_sp_deg})" if _sp_deg is not None else ""))

        # ─ pre-compute shared data for both views ────────────────────────────
        from builders.campaign_builder import _CHANNEL_ASSET_MAP as _cam
        _pp_list       = ctx.get("proof_points", [])
        _pp_green_list = [_p for _p in _pp_list if _is_green(_p)]
        _pp_amb_list   = [_p for _p in _pp_list if not _is_green(_p)]
        _pp_named_ct   = sum(1 for _p in _pp_green_list if any(_n in _p.get("claim","") for _n in _NAMED_PP_NAMES))
        _pp_stat_ct    = len(_pp_green_list) - _pp_named_ct
        _pp_sem_ct     = sum(1 for _p in _pp_list if _p.get("id","") in _sem_ids)
        _ch_list       = ctx.get("matched_channels", [])

        _CH_WEIGHTS = {
            "linkedin_sponsored":       0.91,
            "customer_success_outreach": 0.85,
            "webinar":                  0.78,
            "qualified_outbound":       0.74,
            "hubspot_email":            0.72,
            "in_product":               0.69,
        }

        # ── Marketing Brain: 3 Structural Decisions (collapsed) ──────────────
        with st.expander("Marketing Brain: 3 Structural Decisions", expanded=False):
            _mkt_b1_supp = ""
            if _supp_ps:
                _supp_names_mkt = [_p.get("title", "?") for _p in _supp_ps[:2]]
                _mkt_b1_supp = (
                    f", with {_supp_names_mkt[0]} as secondary audience"
                    if len(_supp_names_mkt) == 1
                    else f", with {' and '.join(_supp_names_mkt)} as secondary audiences"
                )
            _mkt_bullet_1 = (
                f"Targeting {_prim_title}{_mkt_b1_supp}, selected based on"
                f" direct product connections to {_prd_name_d} in the Marketing Brain."
            )
            _mkt_named_n = len(_pp_green_named)
            _mkt_stat_n  = len(_pp_green_stat)
            if _mkt_named_n > 0:
                _mkt_ev = (
                    f"{_mkt_named_n} verified customer case {'study' if _mkt_named_n == 1 else 'studies'}"
                    f" for {_prd_name_d}"
                    + (f" and {_mkt_stat_n} supporting stat{'s' if _mkt_stat_n != 1 else ''}" if _mkt_stat_n > 0 else "")
                )
                _mkt_bullet_2 = f"Found {_mkt_ev}."
            else:
                _mkt_ev2 = f"{_mkt_stat_n} platform stat{'s' if _mkt_stat_n != 1 else ''}" if _mkt_stat_n > 0 else "no verified evidence"
                _mkt_bullet_2 = (
                    f"No customer case studies verified for {_prd_name_d} in the Marketing Brain"
                    + (f": {_mkt_ev2} available as supporting evidence." if _mkt_stat_n > 0 else ".")
                )
            _mkt_ch_weighted = [
                _c.get("name", _c.get("id", "?"))
                for _c in _ch_list
            ]
            if len(_mkt_ch_weighted) > 3:
                _mkt_ch_str = ", ".join(_mkt_ch_weighted[:3]) + f" and {len(_mkt_ch_weighted) - 3} more"
            elif len(_mkt_ch_weighted) > 1:
                _mkt_ch_str = ", ".join(_mkt_ch_weighted[:-1]) + f" and {_mkt_ch_weighted[-1]}"
            elif _mkt_ch_weighted:
                _mkt_ch_str = _mkt_ch_weighted[0]
            else:
                _mkt_ch_str = "none selected"
            _mkt_ch_s = "s" if len(_ch_list) != 1 else ""
            _mkt_bullet_3 = (
                f"{len(_ch_list)} channel{_mkt_ch_s} selected: {_mkt_ch_str}"
                f", all verified to reach {_prim_title} in the Marketing Brain."
            )
            st.markdown(f"""
<div style="background:#0D0D14;border:1px solid #1E1E2E;border-left:3px solid #6B2D8B;
     border-radius:4px;padding:1rem 1.25rem;margin-top:0.5rem;">
  <div style="display:flex;flex-direction:column;gap:0.75rem;">
    <div style="font-size:1.125rem;color:#D8D8E8;line-height:1.6;">
      <span style="color:#9B5DBB;margin-right:0.5rem;">●</span>{_mkt_bullet_1}
    </div>
    <div style="font-size:1.125rem;color:#D8D8E8;line-height:1.6;">
      <span style="color:#9B5DBB;margin-right:0.5rem;">●</span>{_mkt_bullet_2}
    </div>
    <div style="font-size:1.125rem;color:#D8D8E8;line-height:1.6;">
      <span style="color:#9B5DBB;margin-right:0.5rem;">●</span>{_mkt_bullet_3}
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

        # ── Under the hood (collapsed) ─────────────────────────────────────────
        with st.expander("Under the hood", expanded=False):

            # ── Pipeline timings ──────────────────────────────────────────────
            try:
                from builders.campaign_builder import PIPELINE_TIMINGS as _pt
                if _pt:
                    _uth_total = sum(_pt.values())
                    _uth_labels = {
                        "01_parse_brief": "01 Parse brief (Haiku)",
                        "02_messaging":   "02 Messaging (Sonnet)",
                        "03_rollout":     "03 Rollout (Haiku)",
                        "04_deck":        "04 Deck (Haiku)",
                    }
                    _uth_timing_rows = "".join(
                        f'<div style="display:flex;justify-content:space-between;'
                        f'padding:0.2rem 0;font-size:1.07rem;border-bottom:1px solid #1E1E2E;">'
                        f'<span style="color:#A0A0B0;">{_uth_labels.get(k, k)}</span>'
                        f'<span style="color:#A0A0B0;">{v}s</span></div>'
                        for k, v in _pt.items()
                    )
                    st.markdown(f"""
<div style="background:#0D0D14;border:1px solid #1E1E2E;border-radius:4px;
     padding:0.6rem 0.8rem;margin:0.5rem 0 1rem;">
  <div style="font-family:'IBM Plex Mono',monospace;font-size:1.0625rem;color:#6B2D8B;
       letter-spacing:0.1em;text-transform:uppercase;margin-bottom:0.4rem;">
    Pipeline timings
  </div>
  {_uth_timing_rows}
  <div style="display:flex;justify-content:space-between;padding:0.3rem 0 0;font-size:1.07rem;">
    <span style="color:#6B2D8B;font-family:'IBM Plex Mono',monospace;">Total</span>
    <span style="color:#A0A0B0;font-weight:600;">{_uth_total:.1f}s</span>
  </div>
</div>
""", unsafe_allow_html=True)
            except Exception:
                pass

            # ── What shaped this output ───────────────────────────────────────
            st.markdown(f"""
<div style="padding:0.25rem 0;">
  <div style="font-family:'IBM Plex Mono',monospace;font-size:1.0625rem;letter-spacing:0.1em;color:#6B2D8B;text-transform:uppercase;margin-bottom:0.75rem;">MARKETING BRAIN: WHAT SHAPED THIS CAMPAIGN</div>

  <div style="font-family:'IBM Plex Mono',monospace;font-size:1.0625rem;letter-spacing:0.1em;color:#A0A0B0;text-transform:uppercase;margin-bottom:0.4rem;">Brief Inputs</div>
  <div style="display:flex;flex-direction:column;gap:0.25rem;margin-bottom:0.9rem;">
    <div style="display:flex;gap:0.75rem;align-items:baseline;">
      <span style="font-size:1.1rem;color:#A0A0B0;min-width:80px;flex-shrink:0;">Product</span>
      <span style="font-size:1.15rem;color:#C8C8D8;">{_brief_product_raw}</span>
    </div>
    <div style="display:flex;gap:0.75rem;align-items:baseline;">
      <span style="font-size:1.1rem;color:#A0A0B0;min-width:80px;flex-shrink:0;">Goal</span>
      <span style="font-size:1.15rem;color:#C8C8D8;">{_brief_goal}</span>
    </div>
    <div style="display:flex;gap:0.75rem;align-items:baseline;">
      <span style="font-size:1.1rem;color:#A0A0B0;min-width:80px;flex-shrink:0;">Audience</span>
      <span style="font-size:1.15rem;color:#C8C8D8;">{_brief_audience}</span>
    </div>
  </div>

  <div style="font-family:'IBM Plex Mono',monospace;font-size:1.0625rem;letter-spacing:0.1em;color:#A0A0B0;text-transform:uppercase;margin-bottom:0.4rem;border-top:1px solid #1A1A28;padding-top:0.6rem;">Graph Decisions</div>
  <div style="display:flex;flex-direction:column;gap:0.25rem;margin-bottom:0.9rem;">
    <div style="display:flex;gap:0.75rem;align-items:baseline;">
      <span style="font-size:1.1rem;color:#A0A0B0;min-width:145px;flex-shrink:0;">Product mapped to</span>
      <span style="font-size:1.15rem;color:#9B5DBB;">{_mp.get('name', '')}</span>
    </div>
    <div style="display:flex;gap:0.75rem;align-items:baseline;">
      <span style="font-size:1.1rem;color:#A0A0B0;min-width:145px;flex-shrink:0;">Primary persona</span>
      <span style="font-size:1.15rem;color:#C8C8D8;">{_prim_persona_early}</span>
    </div>
    <div style="display:flex;gap:0.75rem;align-items:baseline;">
      <span style="font-size:1.1rem;color:#A0A0B0;min-width:145px;flex-shrink:0;">Campaign motion</span>
      <span style="font-size:1.15rem;color:#C8C8D8;">{_graph_motion}</span>
    </div>
    <div style="display:flex;gap:0.75rem;align-items:baseline;">
      <span style="font-size:1.1rem;color:#A0A0B0;min-width:145px;flex-shrink:0;">Channels prioritized</span>
      <span style="font-size:1.15rem;color:#C8C8D8;">{_ch_names_str}</span>
    </div>
  </div>

  <div style="font-family:'IBM Plex Mono',monospace;font-size:1.0625rem;letter-spacing:0.1em;color:#A0A0B0;text-transform:uppercase;margin-bottom:0.4rem;border-top:1px solid #1A1A28;padding-top:0.6rem;">Evidence Summary</div>
  <div style="display:flex;flex-direction:column;gap:0.3rem;">
    <div style="display:flex;gap:0.75rem;align-items:baseline;">
      <span style="font-size:1.1rem;color:#A0A0B0;min-width:165px;flex-shrink:0;">Named case studies</span>
      <span style="font-size:1.15rem;color:#C8C8D8;">{_ev_named}</span>
    </div>
    <div style="display:flex;gap:0.75rem;align-items:baseline;">
      <span style="font-size:1.1rem;color:#A0A0B0;min-width:165px;flex-shrink:0;">Product-verified stats</span>
      <span style="font-size:1.15rem;color:#C8C8D8;">{_ev_stat}</span>
    </div>
    <div style="display:flex;gap:0.75rem;align-items:baseline;">
      <span style="font-size:1.1rem;color:#A0A0B0;min-width:165px;flex-shrink:0;">Amber stats</span>
      <span style="font-size:1.15rem;color:#C8C8D8;">{_ev_amber}</span>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

            # ── Technical view: Decision 1 / 2 / 3 with full scoring detail ───

            # ─ Decision 1: Persona ────────────────────────────────────────────
            _d1_supp_html = (
                f'<div style="font-size:1.07rem;color:#8080A0;margin-top:0.25rem;">'
                f'Supporting: {" · ".join(_supp_strs)}</div>' if _supp_strs else ""
            )
            st.markdown(f"""
<div style="background:#0D0D14;border:1px solid #1E1E2E;border-left:3px solid #6B2D8B;
     border-radius:4px;padding:0.75rem 1rem;margin-top:0.75rem;margin-bottom:0.15rem;">
  <div style="display:flex;align-items:baseline;gap:0.6rem;margin-bottom:0.3rem;">
    <span style="font-family:'IBM Plex Mono',monospace;font-size:1.0625rem;color:#6B2D8B;
          letter-spacing:0.08em;text-transform:uppercase;flex-shrink:0;">Decision 1</span>
    <span style="font-size:1.13rem;color:#FFFFFF;font-weight:500;">
      Primary persona: <span style="color:#9B5DBB;">{_prim_title}</span>
    </span>
  </div>
  <div style="font-size:1.08rem;font-family:'IBM Plex Mono',monospace;
       color:#7B4DBB;margin-bottom:0.2rem;">{_prim_chain}</div>
  {_d1_supp_html}
</div>
""", unsafe_allow_html=True)

            with st.expander("Show reasoning: persona scoring", expanded=False):
                if _ps_raw:
                    _p_rows_html = ""
                    for _pr in _ps_raw:
                        _is_prim = _pr["pid"] == _prim_pid
                        _t  = _pr.get("targets_count", 0)
                        _r  = _pr.get("reaches_count", 0)
                        _rw = _pr.get("resonates_count", 0)
                        _brk = (
                            f"({_t} edges × 3pts)"
                            f" + ({_r} channels × 1pt)"
                            f" + ({_rw} proof points × 1pt)"
                            f" = {_pr['degree']}"
                        )
                        _p_rows_html += (
                            f'<tr><td style="padding:0.35rem 0.75rem;border-bottom:1px solid #1A1A28;'
                            f'font-size:1.1rem;color:{"#C8C8D8" if _is_prim else "#8080A0"};">'
                            f'{"★ " if _is_prim else ""}{_pr.get("title") or _pr["pid"]}</td>'
                            f'<td style="padding:0.35rem 0.75rem;border-bottom:1px solid #1A1A28;'
                            f'font-size:1.07rem;font-family:\'IBM Plex Mono\',monospace;'
                            f'color:{"#9B5DBB" if _is_prim else "#7A7A8A"};text-align:right;">'
                            f'{_pr["degree"]}</td>'
                            f'<td style="padding:0.35rem 0.75rem;border-bottom:1px solid #1A1A28;'
                            f'font-size:1.125rem;font-family:\'IBM Plex Mono\',monospace;color:#A0A0B0;">'
                            f'{_brk}</td></tr>'
                        )
                    st.markdown(f"""
<table style="width:100%;border-collapse:collapse;background:#080810;">
  <thead><tr>
    <th style="padding:0.3rem 0.75rem;text-align:left;font-size:1.0625rem;font-family:'IBM Plex Mono',monospace;
        color:#A0A0B0;text-transform:uppercase;border-bottom:1px solid #2A1A3A;">Persona</th>
    <th style="padding:0.3rem 0.75rem;text-align:right;font-size:1.0625rem;font-family:'IBM Plex Mono',monospace;
        color:#A0A0B0;text-transform:uppercase;border-bottom:1px solid #2A1A3A;">Score</th>
    <th style="padding:0.3rem 0.75rem;text-align:left;font-size:1.0625rem;font-family:'IBM Plex Mono',monospace;
        color:#A0A0B0;text-transform:uppercase;border-bottom:1px solid #2A1A3A;">Edge breakdown</th>
  </tr></thead>
  <tbody>{_p_rows_html}</tbody>
</table>
""", unsafe_allow_html=True)
                else:
                    st.markdown('<div style="font-size:1.07rem;color:#8080A0;">Degree data not available.</div>', unsafe_allow_html=True)

            # ─ Decision 2: Proof Points ───────────────────────────────────────
            _case_word  = "study" if _pp_named_ct == 1 else "studies"
            _stat_s     = "" if _pp_stat_ct == 1 else "s"
            _d2_summary = (
                f"{len(_pp_list)} proof points retrieved  ·  "
                f"{_pp_named_ct} named case {_case_word} with VALIDATES_PRODUCT  ·  "
                f"{_pp_stat_ct} product stat{_stat_s}  ·  "
                f"{len(_pp_amb_list)} amber"
                + (f"  ·  {_pp_sem_ct} semantic" if _pp_sem_ct else "")
            )

            _pp_detail_rows = []
            for _pp in _pp_list:
                _pp_id    = _pp.get("id", "")
                _pp_claim = _pp.get("claim", "")
                _pp_trunc = (_pp_claim[:80] + "…") if len(_pp_claim) > 80 else _pp_claim
                _is_sem   = _pp_id in _sem_ids
                _is_g     = _is_green(_pp)
                _is_named = any(_n in _pp_claim for _n in _NAMED_PP_NAMES)
                if _is_sem:
                    _edge_tag, _status_tag, _col = "semantic similarity", "Amber: no graph edge", "#8B7B3D"
                elif _is_g and _is_named:
                    _edge_tag, _status_tag, _col = f"VALIDATES_PRODUCT → {_prd_name_d}", "Pillar-eligible ✓", "#3D7B5B"
                elif _is_g:
                    _edge_tag, _status_tag, _col = f"VALIDATES_PRODUCT → {_prd_name_d}", "Stat: supporting evidence only", "#4B7B5B"
                else:
                    _edge_tag, _status_tag, _col = "SUPPORTS_PAIN / RESONATES_WITH", "Amber: no VALIDATES_PRODUCT edge", "#8B7B3D"
                _pp_detail_rows.append((_pp_trunc, _edge_tag, _status_tag, _col))

            _pp_lines_html = "".join(
                f'<div style="padding:0.45rem 0;border-bottom:1px solid #111118;">'
                f'<div style="font-size:1.1rem;color:#B0B0C0;line-height:1.5;margin-bottom:0.15rem;">'
                f'"{_tr}"</div>'
                f'<div style="display:flex;gap:1rem;flex-wrap:wrap;">'
                f'<span style="font-size:1.125rem;font-family:\'IBM Plex Mono\',monospace;'
                f'color:#7B4DBB;">{_et}</span>'
                f'<span style="font-size:1.125rem;font-family:\'IBM Plex Mono\',monospace;'
                f'color:{_c};">{_st}</span>'
                f'</div></div>'
                for _tr, _et, _st, _c in _pp_detail_rows
            )

            st.markdown(f"""
<div style="background:#0D0D14;border:1px solid #1E1E2E;border-left:3px solid #6B2D8B;
     border-radius:4px;padding:0.75rem 1rem;margin-top:0.75rem;margin-bottom:0.15rem;">
  <div style="display:flex;align-items:baseline;gap:0.6rem;margin-bottom:0.5rem;">
    <span style="font-family:'IBM Plex Mono',monospace;font-size:1.0625rem;color:#6B2D8B;
          letter-spacing:0.08em;text-transform:uppercase;flex-shrink:0;">Decision 2</span>
    <span style="font-size:1.13rem;color:#FFFFFF;font-weight:500;">Proof points retrieved</span>
  </div>
  <div style="font-size:1.08rem;font-family:'IBM Plex Mono',monospace;
       color:#7B4DBB;margin-bottom:0.5rem;">{_d2_summary}</div>
  {_pp_lines_html}
</div>
""", unsafe_allow_html=True)

            with st.expander("Show reasoning: proof point Cypher and full scored list", expanded=False):
                if _lc_cypher:
                    st.code(_lc_cypher, language="cypher")
                if _pp_full:
                    _pf_rows = ""
                    for _i, _pf in enumerate(_pp_full):
                        _pf_id  = _pf.get("id", "")
                        _sel    = _pf_id in [_p.get("id","") for _p in _pp_list]
                        _pf_cl  = _pf.get("claim","")
                        _pf_tr  = (_pf_cl[:72] + "…") if len(_pf_cl) > 72 else _pf_cl
                        _pf_rows += (
                            f'<tr><td style="padding:0.3rem 0.75rem;border-bottom:1px solid #111118;'
                            f'font-size:1.08rem;color:{"#C0C0D0" if _sel else "#8080A0"};">{_pf_tr}</td>'
                            f'<td style="padding:0.3rem 0.75rem;border-bottom:1px solid #111118;'
                            f'font-size:1.07rem;font-family:\'IBM Plex Mono\',monospace;'
                            f'color:{"#9B5DBB" if _sel else "#7A7A8A"};text-align:right;">{_pf.get("score","")}</td>'
                            f'<td style="padding:0.3rem 0.75rem;border-bottom:1px solid #111118;'
                            f'font-size:1.0625rem;font-family:\'IBM Plex Mono\',monospace;'
                            f'color:{"#3D7B5B" if _sel else "#7A7A8A"};">{"✓ selected" if _sel else "not selected"}</td>'
                            f'</tr>'
                        )
                    st.markdown(f"""
<div style="font-family:'IBM Plex Mono',monospace;font-size:1.0625rem;color:#A0A0B0;
     text-transform:uppercase;letter-spacing:0.08em;margin-bottom:0.4rem;margin-top:0.5rem;">
  Full scored list (top 12 before truncation to 6)
</div>
<table style="width:100%;border-collapse:collapse;background:#080810;">
  <thead><tr>
    <th style="padding:0.3rem 0.75rem;text-align:left;font-size:1.0625rem;font-family:'IBM Plex Mono',monospace;
        color:#A0A0B0;text-transform:uppercase;border-bottom:1px solid #2A1A3A;">Claim</th>
    <th style="padding:0.3rem 0.75rem;text-align:right;font-size:1.0625rem;font-family:'IBM Plex Mono',monospace;
        color:#A0A0B0;text-transform:uppercase;border-bottom:1px solid #2A1A3A;">Score</th>
    <th style="padding:0.3rem 0.75rem;text-align:left;font-size:1.0625rem;font-family:'IBM Plex Mono',monospace;
        color:#A0A0B0;text-transform:uppercase;border-bottom:1px solid #2A1A3A;">Status</th>
  </tr></thead>
  <tbody>{_pf_rows}</tbody>
</table>
""", unsafe_allow_html=True)

            # ─ Decision 3: Channels ───────────────────────────────────────────
            _ch_lines = []
            for _ch in _ch_list:
                _ch_id   = _ch.get("id", "")
                _ch_nm   = _ch.get("name", _ch_id)
                _reaches = _ch_pmap.get(_ch_id, [])
                _reach_titles = [_pid_to_title.get(_pid, _pid) for _pid in _reaches]
                _asset_defn   = _cam.get(_ch_id, {})
                _asset_nm     = _asset_defn.get("asset_type", "")
                _asset_owner  = _asset_defn.get("owner", "")
                _reach_str    = " + ".join(_reach_titles) if _reach_titles else ""
                _ch_wt        = _CH_WEIGHTS.get(_ch_id, "")
                _ch_lines.append((_ch_nm, _reach_str, _asset_nm, _asset_owner, _ch_wt))

            _ch_detail_html = "".join(
                f'<div style="padding:0.4rem 0;border-bottom:1px solid #111118;'
                f'display:flex;flex-wrap:wrap;align-items:baseline;gap:0.4rem;">'
                f'<span style="font-size:1.1rem;color:#C0C0D0;min-width:185px;">{_cn}</span>'
                f'<span style="font-family:\'IBM Plex Mono\',monospace;font-size:1.125rem;'
                f'color:#5A9B6B;min-width:34px;">{_cw}</span>'
                f'<span style="font-size:1.125rem;font-family:\'IBM Plex Mono\',monospace;'
                f'color:#7B4DBB;">REACHES_PERSONA → {_rp}</span>'
                f'<span style="font-size:1.125rem;color:#A0A0B0;">→</span>'
                f'<span style="font-size:1.125rem;font-family:\'IBM Plex Mono\',monospace;'
                f'color:#3D7B5B;">{_an}</span>'
                f'<span style="font-size:1.125rem;color:#A0A0B0;">({_ao})</span>'
                f'</div>'
                for _cn, _rp, _an, _ao, _cw in _ch_lines
            )

            st.markdown(f"""
<div style="background:#0D0D14;border:1px solid #1E1E2E;border-left:3px solid #6B2D8B;
     border-radius:4px;padding:0.75rem 1rem;margin-top:0.75rem;margin-bottom:0.25rem;">
  <div style="display:flex;align-items:baseline;gap:0.6rem;margin-bottom:0.5rem;">
    <span style="font-family:'IBM Plex Mono',monospace;font-size:1.0625rem;color:#6B2D8B;
          letter-spacing:0.08em;text-transform:uppercase;flex-shrink:0;">Decision 3</span>
    <span style="font-size:1.13rem;color:#FFFFFF;font-weight:500;">
      {len(_ch_list)} channel{"s" if len(_ch_list) != 1 else ""} selected via REACHES_PERSONA traversal
    </span>
  </div>
  {_ch_detail_html}
</div>
<div style="font-family:'IBM Plex Mono',monospace;font-size:1.0625rem;color:#A0A0B0;
     margin-top:0.3rem;margin-bottom:0.25rem;line-height:1.5;font-style:italic;padding:0 0.1rem;">
  Currently hardcoded by motion type. Walk version derives weights from HubSpot engagement rates and Salesforce pipeline conversion by channel.
</div>
""", unsafe_allow_html=True)

            with st.expander("Show reasoning: channel graph edges", expanded=False):
                _all_ch_html = ""
                for _cid, _cdefn in _cam.items():
                    _is_act = _cid in {_c.get("id","") for _c in _ch_list}
                    _reach_ps = _ch_pmap.get(_cid, [])
                    _reach_ts = [_pid_to_title.get(_pid, _pid) for _pid in _reach_ps]
                    _status_c = "#3D7B5B" if _is_act else "#303040"
                    _name_c   = "#C0C0D0" if _is_act else "#7A7A8A"
                    _all_ch_html += (
                        f'<tr><td style="padding:0.35rem 0.75rem;border-bottom:1px solid #111118;'
                        f'font-size:1.1rem;color:{_name_c};">{_cdefn.get("asset_type","?")}</td>'
                        f'<td style="padding:0.35rem 0.75rem;border-bottom:1px solid #111118;'
                        f'font-size:1.07rem;font-family:\'IBM Plex Mono\',monospace;color:#A0A0B0;">'
                        f'{", ".join(_reach_ts) if _reach_ts else ""}</td>'
                        f'<td style="padding:0.35rem 0.75rem;border-bottom:1px solid #111118;'
                        f'font-size:1.125rem;font-family:\'IBM Plex Mono\',monospace;color:{_status_c};">'
                        f'{"✓ activated" if _is_act else "not activated"}</td>'
                        f'</tr>'
                    )
                st.markdown(f"""
<div style="font-family:'IBM Plex Mono',monospace;font-size:1.0625rem;color:#A0A0B0;
     text-transform:uppercase;letter-spacing:0.08em;margin-bottom:0.4rem;">
  All channels in brain: activated vs not
</div>
<table style="width:100%;border-collapse:collapse;background:#080810;">
  <thead><tr>
    <th style="padding:0.3rem 0.75rem;text-align:left;font-size:1.0625rem;font-family:'IBM Plex Mono',monospace;
        color:#A0A0B0;text-transform:uppercase;border-bottom:1px solid #2A1A3A;">Channel / asset</th>
    <th style="padding:0.3rem 0.75rem;text-align:left;font-size:1.0625rem;font-family:'IBM Plex Mono',monospace;
        color:#A0A0B0;text-transform:uppercase;border-bottom:1px solid #2A1A3A;">Reaches persona</th>
    <th style="padding:0.3rem 0.75rem;text-align:left;font-size:1.0625rem;font-family:'IBM Plex Mono',monospace;
        color:#A0A0B0;text-transform:uppercase;border-bottom:1px solid #2A1A3A;">Status</th>
  </tr></thead>
  <tbody>{_all_ch_html}</tbody>
</table>
""", unsafe_allow_html=True)

            # ── Knowledge Graph table ─────────────────────────────────────────────
            try:
                _is_neo4j_viz = False
                try:
                    from knowledge.neo4j_connection import is_available as _n4j_ok
                    if _n4j_ok():
                        _is_neo4j_viz = True
                except Exception:
                    pass

                _matched_ids = ctx.get("_matched_node_ids")
                _viz_source  = "Neo4j: live graph query" if _is_neo4j_viz else "NetworkX in-memory"

                # Build activated-node rows
                try:
                    from knowledge.neo4j_query import LAST_TRAVERSAL_SUMMARY as _ts_tbl
                    _sem_ids = set(_ts_tbl.get("semantic_fallback_ids", []))
                except Exception:
                    _sem_ids = set()

                # Determine which matched pains were reached via product SOLVES_PAIN
                # vs persona EXPERIENCES_PAIN, so the table and traversal paths are labelled correctly.
                _product_solves_pain_ids: set = set()
                try:
                    from knowledge.neo4j_connection import is_available as _n4j_path_ok
                    if _n4j_path_ok() and _mp_id:
                        from knowledge.neo4j_connection import get_driver as _n4j_path_drv
                        with _n4j_path_drv().session() as _ps:
                            _product_solves_pain_ids = {
                                r["id"] for r in _ps.run(
                                    "MATCH (prod:Product {id: $pid})-[:SOLVES_PAIN]->(pain:Pain) "
                                    "RETURN pain.id AS id",
                                    pid=_mp_id,
                                ).data()
                            }
                except Exception:
                    pass
                # NetworkX fallback — populate when Neo4j is unavailable
                if not _product_solves_pain_ids and _mp_id:
                    try:
                        from knowledge.graph_builder import build_marketing_graph as _nx_g_build
                        _nx_g = _nx_g_build()
                        if _nx_g.has_node(_mp_id):
                            _product_solves_pain_ids = {
                                _nb for _nb, _ed in _nx_g[_mp_id].items()
                                if _ed.get("relationship") == "SOLVES_PAIN"
                            }
                    except Exception:
                        pass

                _tbl_rows = []
                for _prod in ctx.get("matched_products", []):
                    _tbl_rows.append((_prod.get("name", _prod.get("id", "?")), "Product", "Brief → Product node traversal"))
                for _prs in ctx.get("matched_personas", []):
                    _tbl_rows.append((_prs.get("title", _prs.get("id", "?")), "Persona", "TARGETS_PERSONA"))
                for _pain in ctx.get("matched_pains", []):
                    _pain_id  = _pain.get("id", "")
                    _pain_rel = "SOLVES_PAIN" if _pain_id in _product_solves_pain_ids else "EXPERIENCES_PAIN (persona path)"
                    _tbl_rows.append((_pain.get("label", _pain_id or "?"), "Pain", _pain_rel))
                for _ch in ctx.get("matched_channels", []):
                    _tbl_rows.append((_ch.get("name", _ch.get("id", "?")), "Channel", "REACHES_PERSONA"))
                for _pp in ctx.get("proof_points", []):
                    _pp_id    = _pp.get("id", "")
                    _pp_claim = _pp.get("claim", _pp_id)
                    _pp_label = (_pp_claim[:72] + "…") if len(_pp_claim) > 72 else _pp_claim
                    if _pp_id in _sem_ids:
                        _pp_edge = "Semantic similarity"
                    elif _is_green(_pp):
                        _pp_edge = "VALIDATES_PRODUCT"
                    else:
                        _pp_edge = "SUPPORTS_PAIN / RESONATES_WITH"
                    _tbl_rows.append((_pp_label, "ProofPoint", _pp_edge))

                # Type colour map
                _type_colors = {
                    "Product":    "#9B5DBB",
                    "Persona":    "#3D8BAB",
                    "Pain":       "#AB5D5D",
                    "Channel":    "#3DAB7B",
                    "ProofPoint": "#9B8B3D",
                }
                _edge_colors = {
                    "Brief → Product node traversal": "#606070",
                    "TARGETS_PERSONA":              "#3D8BAB",
                    "SOLVES_PAIN":                  "#AB5D5D",
                    "REACHES_PERSONA":              "#3DAB7B",
                    "VALIDATES_PRODUCT":            "#9B5DBB",
                    "SUPPORTS_PAIN / RESONATES_WITH": "#9B8B3D",
                    "Semantic similarity":          "#7B7B8B",
                }

                def _tr(node, ntype, edge):
                    tc = _type_colors.get(ntype, "#606070")
                    ec = _edge_colors.get(edge, "#606070")
                    return (
                        f'<tr>'
                        f'<td style="padding:0.45rem 0.75rem;border-bottom:1px solid #1A1A28;'
                        f'font-size:1.13rem;color:#C0C0D0;max-width:320px;">{node}</td>'
                        f'<td style="padding:0.45rem 0.75rem;border-bottom:1px solid #1A1A28;'
                        f'font-size:1.07rem;font-family:\'IBM Plex Mono\',monospace;'
                        f'color:{tc};white-space:nowrap;">{ntype}</td>'
                        f'<td style="padding:0.45rem 0.75rem;border-bottom:1px solid #1A1A28;'
                        f'font-size:1.07rem;font-family:\'IBM Plex Mono\',monospace;'
                        f'color:{ec};white-space:nowrap;">{edge}</td>'
                        f'</tr>'
                    )

                _thead = (
                    '<thead><tr>'
                    '<th style="padding:0.4rem 0.75rem;text-align:left;font-size:1.0625rem;'
                    'font-family:\'IBM Plex Mono\',monospace;letter-spacing:0.1em;'
                    'color:#6B2D8B;text-transform:uppercase;border-bottom:1px solid #2A1A3A;">NODE</th>'
                    '<th style="padding:0.4rem 0.75rem;text-align:left;font-size:1.0625rem;'
                    'font-family:\'IBM Plex Mono\',monospace;letter-spacing:0.1em;'
                    'color:#6B2D8B;text-transform:uppercase;border-bottom:1px solid #2A1A3A;">TYPE</th>'
                    '<th style="padding:0.4rem 0.75rem;text-align:left;font-size:1.0625rem;'
                    'font-family:\'IBM Plex Mono\',monospace;letter-spacing:0.1em;'
                    'color:#6B2D8B;text-transform:uppercase;border-bottom:1px solid #2A1A3A;">EDGE THAT ACTIVATED IT</th>'
                    '</tr></thead>'
                )
                _tbody = "".join(_tr(n, t, e) for n, t, e in _tbl_rows)
                _active_table_html = (
                    f'<table style="width:100%;border-collapse:collapse;background:#0D0D14;">'
                    f'{_thead}<tbody>{_tbody}</tbody></table>'
                )

                with st.expander("↳ Knowledge graph: traversal paths", expanded=False):
                    # Two structural chains — product→pain→persona and product→persona→channel
                    _prd_nm_g = (ctx.get("matched_products") or [{}])[0].get("name", "?").replace("Docebo ", "")
                    _prs_nm_g = (ctx.get("matched_personas") or [{}])[0].get("title", "?")
                    _ch_nm_g  = (ctx.get("matched_channels") or [{}])[0].get("name", "?")

                    # Primary pain: use the product's own pain_ids ordering as priority.
                    # The product JSON lists pain_ids in significance order; match that order
                    # against the pains that are both in matched_pains AND have a SOLVES_PAIN
                    # edge from the product. Only fall back to matched_pains[0] if no such
                    # pain exists (e.g. product has no SOLVES_PAIN entries at all).
                    _matched_pains_g    = ctx.get("matched_pains") or [{}]
                    _prod_pain_order    = (ctx.get("matched_products") or [{}])[0].get("pain_ids") or []
                    _matched_pain_by_id = {p.get("id", ""): p for p in _matched_pains_g}
                    _primary_pain_g     = None
                    # Walk the product's preferred pain order; take first that was actually matched
                    # and has a direct SOLVES_PAIN edge.
                    for _ppid in _prod_pain_order:
                        if _ppid in _product_solves_pain_ids and _ppid in _matched_pain_by_id:
                            _primary_pain_g = _matched_pain_by_id[_ppid]
                            break
                    # Fallback 1: any SOLVES_PAIN pain in matched_pains order
                    if _primary_pain_g is None:
                        _primary_pain_g = next(
                            (p for p in _matched_pains_g if p.get("id", "") in _product_solves_pain_ids),
                            None,
                        )
                    # Fallback 2: just the first matched pain
                    if _primary_pain_g is None:
                        _primary_pain_g = _matched_pains_g[0]
                    _pain_nm_g = _primary_pain_g.get("label", "?")
                    _pain_id_g = _primary_pain_g.get("id", "")

                    def _node(t):
                        return f'<span style="color:#C8C8D8;font-weight:500;">{t}</span>'
                    def _edge(t):
                        return f'<span style="color:#7B4DBB;font-family:\'IBM Plex Mono\',monospace;font-size:1.0625rem;">→ {t} →</span>'
                    def _edgeback(t):
                        return f'<span style="color:#7B4DBB;font-family:\'IBM Plex Mono\',monospace;font-size:1.0625rem;">← {t} ←</span>'

                    _pain_via_product = _pain_id_g in _product_solves_pain_ids
                    if _pain_via_product:
                        _path1 = (f'{_node(_prd_nm_g)} {_edge("SOLVES_PAIN")} {_node(_pain_nm_g)} '
                                  f'{_edgeback("EXPERIENCES_PAIN")} {_node(_prs_nm_g)}')
                    else:
                        _path1 = (f'{_node(_prs_nm_g)} {_edge("EXPERIENCES_PAIN")} {_node(_pain_nm_g)} '
                                  f'<span style="font-size:1.0625rem;color:#A0A0B0;font-style:italic;">'
                                  f'(persona path: no SOLVES_PAIN edge from {_prd_nm_g})</span>')
                    _path2 = (f'{_node(_prd_nm_g)} {_edge("TARGETS_PERSONA")} {_node(_prs_nm_g)} '
                              f'{_edgeback("REACHES_PERSONA")} {_node(_ch_nm_g)}')

                    _path_rows = f'<div style="margin-bottom:0.4rem;">{_path1}</div>'
                    _path_rows += f'<div style="margin-bottom:0.4rem;">{_path2}</div>'

                    st.markdown(f"""
    <div style="background:#080810;border:1px solid #1A1A28;border-radius:4px;
         padding:0.75rem 1rem;font-size:1.07rem;line-height:2rem;">
      {_path_rows}
    </div>
    """, unsafe_allow_html=True)

            except Exception as _gve:
                pass  # visualization is additive — never break the main output

            # ── Assumptions Made ─────────────────────────────────────────────────
            # Each entry: (statement, tier, rationale)
            _tier_c = {"High": "#3D7B5B", "Medium": "#8B7B3D", "Low": "#7B4040"}
            _assumptions_data = []

            # 1. Product mapping → High
            if ctx.get("matched_products"):
                _assumptions_data.append((
                    f"{_prd_name_d} selected, matched via product node traversal from the brief.",
                    "High",
                    "Derived from direct keyword match to product node, not inferred from context.",
                ))

            # 2. Persona selection → High
            if ctx.get("matched_personas"):
                _deg_note = f" (degree score: {_prim_degree})" if _prim_degree != "" else ""
                _deg_ref  = f"with degree score {_prim_degree}" if _prim_degree != "" else "via TARGETS_PERSONA traversal"
                # Use the value re-derived from the explicit brief (not the enriched brief
                # that query_graph receives, which may contain auto-added persona language).
                if readiness.get("persona_from_explicit"):
                    _persona_rationale = (
                        f"Derived from direct text match in brief, confirmed by TARGETS_PERSONA edge in graph {_deg_ref}."
                    )
                else:
                    _persona_rationale = (
                        f"Derived from direct TARGETS_PERSONA edge in the graph {_deg_ref}, not inferred from context."
                    )
                _assumptions_data.append((
                    f"{_prim_title} selected as primary buyer, highest TARGETS_PERSONA degree score"
                    f" for {_prd_name_d} in the Marketing Brain{_deg_note}.",
                    "High",
                    _persona_rationale,
                ))

            # 3. Channel selection → High
            if _ch_list:
                _ch_s = "s" if len(_ch_list) != 1 else ""
                _assumptions_data.append((
                    f"{len(_ch_list)} channel{_ch_s} selected via REACHES_PERSONA traversal to {_prim_title}.",
                    "High",
                    "Derived from REACHES_PERSONA edges in the graph, not inferred from context.",
                ))

            # 4. Campaign motion — High if brief confirms it explicitly, Medium if inferred from product only
            if _mp.get("campaign_motion"):
                _cm_type = (
                    "existing Docebo customers"
                    if ("early access" in _cm or "pre-launch" in _cm
                        or ("expansion" in _cm
                            and ("net-new" not in _cm or "not available to net-new" in _cm)))
                    else "new prospects"
                    if ("net-new" in _cm and "expansion" not in _cm)
                    else "existing customers and new prospects"
                )
                _orig_brief_lower = st.session_state.get("original_brief", "").lower()
                _qa_lower = " ".join(
                    qa.get("answer", "") for qa in st.session_state.get("intake_qa_log", [])
                ).lower()
                _expansion_signals = [
                    "existing customer", "existing client", "existing account",
                    "existing docebo", "docebo customer", "docebo client",
                    "current customer", "current account", "our customer",
                    "expansion", "upsell", "up-sell",
                ]
                _netnew_signals = [
                    "net-new", "net new", "new account", "new prospect",
                    "new customer", "acquisition", "prospects",
                ]
                _is_expansion_motion = "expansion" in _cm or "upsell" in _cm or "early" in _cm
                _is_netnew_motion    = "net-new" in _cm or "acquisition" in _cm
                _motion_in_orig = (
                    (_is_expansion_motion and any(s in _orig_brief_lower for s in _expansion_signals))
                    or (_is_netnew_motion and any(s in _orig_brief_lower for s in _netnew_signals))
                )
                _motion_in_qa = not _motion_in_orig and (
                    (_is_expansion_motion and any(s in _qa_lower for s in _expansion_signals))
                    or (_is_netnew_motion and any(s in _qa_lower for s in _netnew_signals))
                )
                _brief_confirms_motion = _motion_in_orig or _motion_in_qa
                _motion_confidence = "High" if _brief_confirms_motion else "Medium"
                _motion_rationale = (
                    "Confirmed by explicit audience language in the brief."
                    if _motion_in_orig
                    else "Confirmed by user."
                    if _motion_in_qa
                    else "Inferred from product-to-motion mapping in the graph. No explicit brief signal confirmed this."
                )
                # Use the user-confirmed motion label when available so Graph Decisions
                # matches the Motion field exactly. Fall back to graph motion only when
                # motion was purely inferred from the product node.
                _gd_motion_label = (
                    _brief_motion_label
                    if _brief_confirms_motion and _brief_motion_label
                    else _graph_motion
                )
                _gd_motion_text = (
                    f"{_gd_motion_label}, confirmed for this campaign."
                    if _brief_confirms_motion and _brief_motion_label
                    else f"{_gd_motion_label}: {_prd_name_d} mapped to this motion in the Marketing Brain."
                )
                _assumptions_data.append((
                    _gd_motion_text,
                    _motion_confidence,
                    _motion_rationale,
                ))

            # 5. Firmographic details → Low
            if ctx.get("matched_personas"):
                _assumptions_data.append((
                    f"Company size and team size derived from {_prim_title} persona node properties.",
                    "Low",
                    "Derived from persona node properties, not from brief input. Marketer should verify this matches their actual target.",
                ))

            # 6. Timeline — High if explicitly stated, Medium if inferred from fuzzy language
            _tl_wks_raw         = structure.get("timeline_weeks", "")
            _tl_from_explicit   = readiness.get("timeline_from_explicit", False)
            if _tl_wks_raw:
                _tl_tier        = "High" if _tl_from_explicit else "Medium"
                _tl_text        = (
                    f"{_tl_wks_raw}-week timeline stated in brief."
                    if _tl_from_explicit
                    else f"{_tl_wks_raw}-week timeline inferred from brief."
                )
                _tl_rationale   = (
                    "Explicitly stated in brief. Treat as confirmed input."
                    if _tl_from_explicit
                    else "Derived from brief context, not a direct number. Not validated against channel-specific conversion timelines."
                )
                _assumptions_data.append((_tl_text, _tl_tier, _tl_rationale))

            # Split assumptions: High shown in marketer view; Medium/Low deferred to tech expander.
            _asmp_high   = [(at, ti, ar) for at, ti, ar in _assumptions_data if ti == "High"]
            _asmp_low_med= [(at, ti, ar) for at, ti, ar in _assumptions_data if ti != "High"]

            def _asmp_row(at, ti, ar, show_tier=True):
                _tc = _tier_c.get(ti, "#606070")
                _tier_badge = (
                    f'<span style="font-family:\'IBM Plex Mono\',monospace;font-size:1.0625rem;'
                    f'color:{_tc};margin-left:0.5rem;">[{ti}]</span>'
                    if show_tier else ""
                )
                return (
                    f'<div style="padding:0.3rem 0;border-bottom:1px solid #111118;">'
                    f'<div style="font-size:1.1rem;color:#C8C8D8;line-height:1.5;">'
                    f'<span style="color:#6B2D8B;font-family:\'IBM Plex Mono\',monospace;margin-right:0.5rem;">›</span>'
                    f'{at}{_tier_badge}</div>'
                    f'<div style="font-size:1.125rem;color:#A0A0B0;margin-left:1.35rem;'
                    f'margin-top:0.1rem;line-height:1.4;font-style:italic;">{ar}</div>'
                    f'</div>'
                )

            if _asmp_high:
                _asmp_lines = "".join(_asmp_row(at, ti, ar, show_tier=False) for at, ti, ar in _asmp_high)
                st.markdown(f"""
    <div class="section-card" style="margin-bottom:0.75rem;">
      <div class="section-label">Graph Decisions</div>
      {_asmp_lines}
    </div>
    """, unsafe_allow_html=True)

            # Medium/Low inferences shown in a collapsed technical expander only
            if _asmp_low_med:
                with st.expander("↳ System inferences: review before activating", expanded=False):
                    _inf_rows = "".join(_asmp_row(at, ti, ar, show_tier=True) for at, ti, ar in _asmp_low_med)
                    st.markdown(
                        f'<div style="font-family:\'IBM Plex Mono\',monospace;font-size:1.0625rem;'
                        f'color:#A0A0B0;text-transform:uppercase;letter-spacing:0.08em;'
                        f'margin-bottom:0.5rem;">Values the system inferred, not from the brief</div>'
                        f'{_inf_rows}',
                        unsafe_allow_html=True,
                    )

            # ── Inference Load ────────────────────────────────────────────────────
            _inf_load      = readiness.get("inference_load", "")
            _inf_provided  = readiness.get("provided_elements", [])
            _inf_inferred  = readiness.get("inferred_elements", [])
            if _inf_load:
                _inf_load_c = {"Low": "#3D7B5B", "Medium": "#8B7B3D", "High": "#8B3D3D"}.get(_inf_load, "#606070")
                _prov_str   = ", ".join(_inf_provided) if _inf_provided else "none"
                _infer_str  = ", ".join(_inf_inferred) if _inf_inferred else "none"
                st.markdown(f"""
    <div class="section-card" style="margin-bottom:0.75rem;">
      <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:0.4rem;">
        <div class="section-label" style="margin-bottom:0;">Inference Load</div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:1.0625rem;font-weight:600;color:{_inf_load_c};">{_inf_load}</div>
      </div>
      <div style="font-size:1.1rem;color:#3D7B5B;line-height:1.7;">
        <span style="color:#A0A0B0;font-size:1.0625rem;text-transform:uppercase;letter-spacing:0.08em;">Provided</span>
        &nbsp;{_prov_str}
      </div>
      <div style="font-size:1.1rem;color:#8B3D3D;line-height:1.7;">
        <span style="color:#A0A0B0;font-size:1.0625rem;text-transform:uppercase;letter-spacing:0.08em;">Inferred</span>
        &nbsp;{_infer_str}
      </div>
    </div>
    """, unsafe_allow_html=True)
                st.markdown("""
    <div style="font-size:1.07rem;color:#A0A0B0;font-family:'IBM Plex Mono',monospace;
      padding:0.4rem 0.75rem;margin-bottom:0.75rem;border-left:2px solid #2A2A3A;">
      Containment: Active. Inferred fields isolated from structural decisions.
    </div>
    """, unsafe_allow_html=True)


        # ── Data gap warning ──────────────────────────────────────────────────
        _data_gaps = messaging.get("data_gaps", [])
        if _data_gaps:
            _gap_items = "".join(
                f'<div style="font-size:1.07rem;color:#FFFFFF;padding:0.35rem 0;'
                f'border-bottom:1px solid #2E2200;display:flex;gap:0.6rem;line-height:1.5;">'
                f'<span style="color:#FFFFFF;flex-shrink:0;font-family:\'IBM Plex Mono\',monospace;">!</span>'
                f'<span>{g}</span></div>'
                for g in _data_gaps
            )
            st.markdown(f"""
<div style="background:#131000;border:1px solid #8B6B2D;border-left:3px solid #8B6B2D;
     border-radius:6px;padding:0.9rem 1.25rem;margin-bottom:1.5rem;">
  <div style="font-family:'IBM Plex Mono',monospace;font-size:1.0625rem;color:#FFFFFF;
       letter-spacing:0.12em;text-transform:uppercase;margin-bottom:0.5rem;">
    Marketing team: resolve before publishing
  </div>
  {_gap_items}
</div>
""", unsafe_allow_html=True)


        # ── Campaign Readiness Score ──────────────────────────────────────────
        _rs_struct  = readiness["structure_score"]
        _rs_evid    = readiness["evidence_score"]
        _rs_total   = readiness["readiness"]
        _rs_status  = readiness["status"]
        _rs_color   = _STATUS_C.get(_rs_status, "#606070")
        _rs_emoji   = _STATUS_EMOJI.get(_rs_status, "")

        def _req_row(r: dict) -> str:
            if r["satisfied"]:
                return (
                    f'<div style="display:flex;justify-content:space-between;'
                    f'font-size:1.0625rem;color:#3D7B5B;line-height:1.65;">'
                    f'<span>✓ {r["label"]}</span>'
                    f'<span style="font-family:\'IBM Plex Mono\',monospace;font-size:1.0625rem;">'
                    f'{r["pts"]}pts</span></div>'
                )
            else:
                return (
                    f'<div style="display:flex;justify-content:space-between;'
                    f'font-size:1.0625rem;color:#8B3D3D;line-height:1.65;">'
                    f'<span>✗ {r["label"]}</span>'
                    f'<span style="font-family:\'IBM Plex Mono\',monospace;font-size:1.0625rem;">'
                    f'0/{r["weight"]}pts</span></div>'
                )

        _struct_rows = "".join(_req_row(r) for r in readiness["structure_reqs"])
        _evid_rows   = "".join(_req_row(r) for r in readiness["evidence_reqs"])

        _cs_grounded_items = (
            [r["label"] for r in readiness["structure_reqs"] if r["satisfied"]]
            + [r["label"] for r in readiness["evidence_reqs"] if r["satisfied"]]
        )
        _cd_missing_items = (
            [r["label"] for r in readiness["structure_reqs"] if not r["satisfied"]]
            + [r["label"] for r in readiness["evidence_reqs"] if not r["satisfied"]]
        )

        st.markdown(f"""
<div class="section-card" style="margin-bottom:0.75rem;">
  <div class="section-label">Campaign Readiness</div>

  <div style="margin:0.5rem 0 0.35rem;">
    <div style="font-family:'IBM Plex Mono',monospace;font-size:1.0625rem;color:#8080A0;
         text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.2rem;">Structure</div>
    {_struct_rows}
    <div style="font-size:1.0625rem;color:#A0A0B0;font-family:'IBM Plex Mono',monospace;
         text-align:right;margin-top:0.15rem;">{_rs_struct}/100</div>
  </div>

  <div style="margin:0.5rem 0 0.35rem;border-top:1px solid #1A1A28;padding-top:0.4rem;">
    <div style="font-family:'IBM Plex Mono',monospace;font-size:1.0625rem;color:#8080A0;
         text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.2rem;">Evidence</div>
    {_evid_rows}
    <div style="font-size:1.0625rem;color:#A0A0B0;font-family:'IBM Plex Mono',monospace;
         text-align:right;margin-top:0.15rem;">{_rs_evid}/100</div>
  </div>

  <div style="border-top:2px solid #1A1A28;padding-top:0.5rem;margin-top:0.1rem;
       display:flex;align-items:baseline;justify-content:space-between;">
    <div>
      <span style="font-family:'IBM Plex Mono',monospace;font-size:1.0625rem;color:#8080A0;
            text-transform:uppercase;letter-spacing:0.1em;">Readiness</span>
      <span style="font-size:1.0625rem;color:#8080A0;margin-left:0.5rem;">
        {_rs_struct} × {_rs_evid} / 100</span>
    </div>
    <div style="display:flex;align-items:baseline;gap:0.5rem;">
      <span style="font-size:1.65rem;font-weight:700;color:{_rs_color};
            font-family:'IBM Plex Mono',monospace;line-height:1;">{_rs_total}/100</span>
      <span style="font-size:1.07rem;color:{_rs_color};">{_rs_emoji} {_rs_status}</span>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

        # ── ICP: only show what was explicitly stated in the brief ───────────────
        # Read from explicit_brief (raw user input + clarification answers) — NOT the
        # enriched brief, which the intake agent expands with inferred ICP characteristics.
        _raw_eb = st.session_state.get("explicit_brief", "")
        _icp_parts = []
        _ROLE_EXTRACT_RE = re.compile(
            r'\b(VP(?:\s+(?:of\s+)?[A-Za-z&\s]{1,30})?|Director(?:\s+of\s+[A-Za-z\s]{1,20})?|'
            r'CLO|CHRO|CTO|CEO|L&D|Head\s+of\s+[A-Za-z&\s]{1,20}|'
            r'Chief\s+[A-Za-z\s]{1,20}Officer)',
            re.IGNORECASE,
        )
        _EXISTING_CUST_RE = re.compile(
            r'\bexisting\s+(?:\w+\s+)?(?:customers?|accounts?|users?|clients?)\b', re.IGNORECASE
        )
        _NETNEW_CUST_RE   = re.compile(r'\bnet-?new\b', re.IGNORECASE)
        _ICP_FIRM_DISP_RE = re.compile(
            r'(?:\bmid-?market\b|\bSMB\b|\bsmall\s+(?:business|company|team)\b|'
            r'\blarge\s+(?:company|enterprise|organization)\b|\bstartup\b|'
            r'\b\d+\s*[\-–]?\s*\d*\s*(?:employee|person|seat|user)s?\b|'
            r'\b(?:healthcare|financial\s+service|retail|manufacturing|banking|pharma|'
            r'insurance|education)(?:s|\s+sector|\s+industry)?\b)',
            re.IGNORECASE,
        )
        _role_m = _ROLE_EXTRACT_RE.search(_raw_eb)
        if _role_m:
            # Strip trailing prepositions that make the match run into the next clause
            _role_raw = re.sub(
                r'\s+(?:at|for|in|the|to|from)\b.*$', '', _role_m.group(0), flags=re.IGNORECASE
            ).strip()
            if _role_raw:
                _icp_parts.append(_role_raw)
        _firm_m = _ICP_FIRM_DISP_RE.search(_raw_eb)
        if _firm_m:
            _icp_parts.append(_firm_m.group(0).strip())
        icp_display = ", ".join(_icp_parts) if _icp_parts else ""
        _icp_empty  = not bool(_icp_parts)

        # ── Motion: from explicit brief, with graph-inference fallback ──────────
        _motion_graph_inferred = False
        if re.search(r'\b(?:early\s+access|pre-?launch)\b', _raw_eb, re.IGNORECASE):
            _disp_motion = "Early access: existing customers"
        elif _EXISTING_CUST_RE.search(_raw_eb):
            _disp_motion = "Expansion: existing customers"
        elif _NETNEW_CUST_RE.search(_raw_eb):
            _disp_motion = "Net-new acquisition"
        else:
            # Brief doesn't specify — infer from matched product's campaign_motion in the graph
            if "early access" in _cm or "pre-launch" in _cm:
                _disp_motion = "Early access: existing customers"
                _motion_graph_inferred = True
            elif "expansion" in _cm and ("net-new" not in _cm or "not available to net-new" in _cm):
                _disp_motion = "Expansion: existing customers"
                _motion_graph_inferred = True
            elif "expansion" in _cm and "net-new" in _cm:
                _disp_motion = "Expansion or net-new"
                _motion_graph_inferred = True
            elif "net-new" in _cm or "acquisition" in _cm:
                _disp_motion = "Net-new acquisition"
                _motion_graph_inferred = True
            else:
                _disp_motion = None  # truly no product motion data

        # ── Flatten campaign_goal ─────────────────────────────────────────────
        goal_raw = structure.get("campaign_goal", "")
        if isinstance(goal_raw, dict):
            primary = goal_raw.get("primary", {})
            if primary:
                target = primary.get("target", "")
                action = primary.get("metric", "")
                days   = primary.get("timeframe_days", "")
                goal_display = f"{target} {action} in {days} days" if days else f"{target} {action}"
            else:
                goal_display = " · ".join(str(v) for v in goal_raw.values() if v)
        else:
            goal_display = str(goal_raw)

        # ── Timeline: only show if explicitly stated in brief ─────────────────
        from builders.campaign_builder import _EXPLICIT_TIMELINE_RE as _TL_RE
        _tl_explicit = bool(_TL_RE.search(_raw_eb))
        timeline_raw = structure.get("timeline_weeks", "")
        if isinstance(timeline_raw, dict):
            timeline_display = str(timeline_raw.get("total", ""))
        else:
            timeline_display = str(timeline_raw)
        # Guard: show "Not specified" if no explicit timeline in brief
        _tl_valid = _tl_explicit and bool(timeline_display) and timeline_display not in ("0", "None", "")

        # ── Positioning ───────────────────────────────────────────────────────
        _pos_icp_val    = icp_display if not _icp_empty else \
            '<span style="color:#A0A0B0;font-style:italic;font-size:1.0625rem;">Not specified. Add to brief</span>'
        _pos_motion_val = (
            (
                f'<span style="color:#FFFFFF;">{_disp_motion}</span>'
                + (f'<span style="color:#A0A0B0;font-size:1.0625rem;margin-left:0.5rem;">(graph-inferred)</span>'
                   if _motion_graph_inferred else "")
            ) if _disp_motion else
            '<span style="color:#A0A0B0;font-style:italic;font-size:1.0625rem;">Not specified. Add to brief</span>'
        )
        _pos_tl_val = (
            f'<span style="color:#FFFFFF;">{timeline_display} weeks</span>' if _tl_valid else
            '<span style="color:#A0A0B0;font-style:italic;font-size:1.0625rem;">Not specified. Add to brief</span>'
        )
        _camp_name    = messaging.get("campaign_name", "") or ""
        _camp_concept = messaging.get("campaign_concept", "") or ""
        _camp_name_html = (
            f'<span style="color:#FFFFFF;font-size:1.25rem;font-weight:600;">{_camp_name}</span>'
            if _camp_name else
            '<span style="color:#A0A0B0;font-style:italic;font-size:1.0625rem;"></span>'
        )
        _camp_concept_html = (
            f'<span style="color:#A0A0B0;font-size:1.125rem;">{_camp_concept}</span>'
            if _camp_concept else
            '<span style="color:#A0A0B0;font-style:italic;font-size:1.0625rem;"></span>'
        )
        st.markdown(f"""
<div class="section-card">
  <div class="section-label">Positioning</div>
  <div class="positioning">{messaging.get('positioning_statement', '')}</div>
  <div style="margin-top:1rem;margin-bottom:0.75rem;padding-top:0.75rem;border-top:1px solid #1E1E2E;">
    <div style="margin-bottom:0.6rem;">
      <span style="color:#A0A0B0;font-size:1.0625rem;text-transform:uppercase;letter-spacing:0.1em;font-weight:600;">Campaign Name</span><br>
      {_camp_name_html}
    </div>
    <div>
      <span style="color:#A0A0B0;font-size:1.0625rem;text-transform:uppercase;letter-spacing:0.1em;font-weight:600;">Campaign Concept</span><br>
      {_camp_concept_html}
    </div>
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.75rem;margin-top:0.5rem;font-size:1.125rem;">
    <div><span style="color:#A0A0B0;font-size:1.0625rem;font-weight:600;text-transform:uppercase;letter-spacing:0.08em;">ICP</span><br>{_pos_icp_val}</div>
    <div><span style="color:#A0A0B0;font-size:1.0625rem;font-weight:600;text-transform:uppercase;letter-spacing:0.08em;">Motion</span><br>{_pos_motion_val}</div>
    <div><span style="color:#A0A0B0;font-size:1.0625rem;font-weight:600;text-transform:uppercase;letter-spacing:0.08em;">Goal</span><br><span style="color:#FFFFFF;">{goal_display}</span></div>
    <div><span style="color:#A0A0B0;font-size:1.0625rem;font-weight:600;text-transform:uppercase;letter-spacing:0.08em;">Timeline</span><br>{_pos_tl_val}</div>
  </div>
</div>
""", unsafe_allow_html=True)

        # ── Messaging pillars ─────────────────────────────────────────────────
        # If no proof point exists for a pillar, suppress the one_liner body copy —
        # assertive claims without evidence should not appear. Show only the placeholder.
        # If evidence_score < 25 (zero verified evidence), force all pillars to placeholder
        # regardless of what the LLM wrote — containment enforcement at display layer.
        _force_placeholder = readiness.get("evidence_score", 0) < 25
        pillars_html = ""
        for p in messaging.get("pillars", []):
            _proof = p.get("proof_point", "")
            _is_placeholder = _proof.startswith("[PROOF POINT NEEDED") or _force_placeholder
            if _is_placeholder:
                _proof_html = (
                    f'<div style="font-size:1.0625rem;color:#FFFFFF;font-family:\'IBM Plex Mono\',monospace;'
                    f'font-style:italic;line-height:1.5;margin-top:0.35rem;">'
                    f'[Proof point needed: add case study or stat before activating]</div>'
                )
                _oneliner_html = ""  # suppress body copy when no proof exists
            else:
                _proof_html   = f'<div class="pillar-proof">{_proof}</div>'
                _oneliner_html = f'<div class="pillar-oneliner">{p.get("one_liner","")}</div>'
            pillars_html += f"""
<div class="pillar">
  <div class="pillar-title">{p.get('title','')}</div>
  {_oneliner_html}
  {_proof_html}
</div>"""

        st.markdown(f"""
<div class="section-card">
  <div class="section-label">Messaging Pillars</div>
  {pillars_html}
</div>
""", unsafe_allow_html=True)

        # ── CTA by persona + Asset plan ───────────────────────────────────────
        col_l, col_r = st.columns(2)

        with col_l:
            ctas = messaging.get("cta_by_persona", {})
            cta_html = ""
            for persona, cta in ctas.items():
                cta_html += f"""
<div class="persona-cta">
  <div class="persona-name">{persona}</div>
  <div class="persona-text">{cta}</div>
</div>"""
            st.markdown(f"""
<div class="section-card">
  <div class="section-label">CTA by Persona</div>
  {cta_html}
</div>
""", unsafe_allow_html=True)

        with col_r:
            assets = messaging.get("asset_plan", [])
            asset_rows = '<div class="asset-row header"><div>Asset</div><div>Format</div><div>Owner</div><div>Purpose</div></div>'
            for a in assets:
                asset_rows += f"""
<div class="asset-row">
  <div class="asset-type">{a.get('asset_type','')}</div>
  <div class="asset-format">{a.get('format','')}</div>
  <div class="asset-owner">{a.get('owner','')}</div>
  <div class="asset-purpose">{a.get('purpose','')}</div>
</div>"""
            st.markdown(f"""
<div class="section-card">
  <div class="section-label">Asset Plan</div>
  {asset_rows}
</div>
""", unsafe_allow_html=True)

        # ── Rollout phases ────────────────────────────────────────────────────
        phases_html = ""
        for ph in rollout.get("phases", []):
            task_rows = ""
            for t in ph.get("tasks", []):
                task_rows += f"""
<div class="task-row">
  <div class="task-name">{t.get('task','')}</div>
  <div style="color:#4A9B6F;">{t.get('owner','')}</div>
  <div style="color:#A0A0B0;">Day {t.get('due_day','')}</div>
</div>"""
            phases_html += f"""
<div class="phase-block">
  <div class="phase-header">
    <div class="phase-name">{ph.get('phase','')}</div>
    <div class="phase-weeks">{ph.get('weeks','')}</div>
  </div>
  <div class="phase-milestone">↳ {ph.get('milestone','')}</div>
  <div class="task-row" style="color:#A0A0B0; font-size:1.0625rem; margin-bottom:0.25rem;">
    <div>TASK</div><div>OWNER</div><div>DUE</div>
  </div>
  {task_rows}
</div>"""

        st.markdown(f"""
<div class="section-card">
  <div class="section-label">Phased Rollout</div>
  {phases_html}
</div>
""", unsafe_allow_html=True)

        # ── Success Metrics (full width) ──────────────────────────────────────
        # Metric 1 (primary goal) always shown as-is.
        # Metrics 2-4 with missing benchmarks collapse into one note — do not repeat
        # three nearly-identical placeholder lines.
        _all_metrics = rollout.get("success_metrics", [])
        _primary_metric = _all_metrics[0] if _all_metrics else None
        _needs_benchmark = any(
            ("Benchmark unavailable" in m or "BENCHMARK NEEDED" in m)
            for m in _all_metrics[1:]
        )
        _rendered_metrics = []
        if _primary_metric:
            _rendered_metrics.append(
                f'<div class="metric-item">{_primary_metric}</div>'
            )
        # Metrics 2-4 that have real values come before the benchmark note
        for _sm in _all_metrics[1:]:
            if "Benchmark unavailable" not in _sm and "BENCHMARK NEEDED" not in _sm:
                _rendered_metrics.append(f'<div class="metric-item">{_sm}</div>')
        # Benchmark placeholder appears once, after all metrics
        if _needs_benchmark:
            _rendered_metrics.append(
                '<div class="metric-item" style="color:#6B4B2D;font-style:italic;">'
                'Benchmarks not available: add manually before activating campaign.'
                '</div>'
            )
        metrics_html = "".join(_rendered_metrics)
        st.markdown(f"""
<div class="section-card">
  <div class="section-label">Success Metrics</div>
  {metrics_html}
</div>
""", unsafe_allow_html=True)

        # Sidebar stays minimal after pipeline runs — brain detail is in the main expander

        # ── BEFORE THIS GOES TO ASANA — governance section ───────────────────
        _TEAM_OPTIONS = [
            "Demand Gen", "PMM", "Paid Social", "Field Marketing",
            "Customer Marketing", "Marketing Ops", "RevOps",
            "Customer Success", "SDR/BDR",
        ]
        _TEAM_SIGNALS = [
            ("Demand Generation", "Demand Gen"), ("Demand Gen", "Demand Gen"),
            ("Product Marketing", "PMM"),        ("PMM", "PMM"),
            ("RevOps", "RevOps"),                ("Marketing Ops", "Marketing Ops"),
            ("Paid Social", "Paid Social"),      ("Field Marketing", "Field Marketing"),
            ("Customer Marketing", "Customer Marketing"),
            ("Customer Success", "Customer Success"),
            ("SDR/BDR", "SDR/BDR"), ("SDR", "SDR/BDR"), ("BDR", "SDR/BDR"),
        ]

        def _cp_teams(text):
            lower, found, seen = text.lower(), [], set()
            for pat, canonical in _TEAM_SIGNALS:
                if pat.lower() in lower and canonical not in seen:
                    found.append(canonical); seen.add(canonical)
            return found[:2]

        def _cp_day(text):
            m = re.search(r'day\s+(\d+)', text, re.IGNORECASE)
            return m.group(1) if m else "?"

        _checkpoints = rollout.get("human_review_checkpoints", [])
        _cp_cards_html = ""
        for _ci, _cp in enumerate(_checkpoints, 1):
            _teams = _cp_teams(_cp)
            _day   = _cp_day(_cp)
            _badges = "".join(
                f'<span class="gov-team-badge">☐ {t}</span>' for t in _teams
            )
            _cp_cards_html += f"""
<div class="gov-cp-card">
  <div class="gov-cp-meta">Checkpoint {_ci} &nbsp;·&nbsp; Day {_day}</div>
  <div class="gov-cp-text">{_cp}</div>
  <div>{_badges}</div>
</div>"""

        st.markdown(f"""
<div class="gov-header">
  <div class="section-label">Before This Goes to Asana</div>
  <div class="gov-title">Three governance checkpoints are built into this campaign</div>
  <div class="gov-subtitle">These milestones will appear as tasks in the Asana project. Edit owners or due days below before pushing.</div>
</div>
{_cp_cards_html}
""", unsafe_allow_html=True)

        # ── Action bar: status + Save + Push to Asana ─────────────────────────
        _sys_status = readiness["status"]
        _cur_save_s = st.session_state.get("campaign_save_status", _sys_status)
        _disp_s = _cur_save_s if _cur_save_s == "Complete" else _sys_status
        _disp_c = _STATUS_C.get(_disp_s, "#606070")
        _disp_e = _STATUS_EMOJI.get(_disp_s, "")
        st.markdown('<div style="margin-top:1.5rem;"></div>', unsafe_allow_html=True)
        _ab_c = st.columns([2, 1, 2, 3])
        with _ab_c[0]:
            st.markdown(
                f'<div style="padding-top:0.4rem;font-size:1.1rem;color:{_disp_c};font-weight:500;">'
                f'{_disp_e} {_disp_s}</div>',
                unsafe_allow_html=True,
            )
        with _ab_c[1]:
            _ab_save_clicked = st.button("Save", key="action_bar_save_btn", type="primary")
        with _ab_c[2]:
            if st.session_state.asana_url:
                st.markdown(
                    f'<div style="padding-top:0.45rem;">'
                    f'<a href="{st.session_state.asana_url}" target="_blank" '
                    f'style="font-size:1.125rem;color:#3D7B5B;text-decoration:none;">✓ In Asana →</a></div>',
                    unsafe_allow_html=True,
                )
            else:
                _ab_asana_clicked = st.button("Push to Asana", key="asana_btn")
        with _ab_c[3]:
            if st.session_state.current_campaign_db_id:
                _saved_sc = _STATUS_C.get(_disp_s, "#606070")
                st.markdown(
                    f'<div style="padding-top:0.5rem;font-size:1.0625rem;'
                    f'font-family:\'IBM Plex Mono\',monospace;color:{_saved_sc};">'
                    f'Saved · {_disp_s}</div>',
                    unsafe_allow_html=True,
                )
            elif st.session_state.asana_error:
                st.markdown(
                    f'<div style="padding-top:0.5rem;font-size:1.0625rem;color:#A05050;">'
                    f'Push failed: check credentials</div>',
                    unsafe_allow_html=True,
                )
        st.markdown('<div style="margin-bottom:1rem;"></div>', unsafe_allow_html=True)

        # ── Action bar: Save handler ───────────────────────────────────────────
        if _ab_save_clicked:
            # Status is system-derived from readiness score; "Complete" is the only manual override
            _save_status = _disp_s  # already computed above (system status or "Complete" if overridden)
            if st.session_state.current_campaign_db_id:
                _db_update_status(st.session_state.current_campaign_db_id, _save_status)
            else:
                _saved_id = _db_save(
                    username=st.session_state.logged_in_user,
                    brief_text=st.session_state.enriched_brief,
                    product=_prd_name_d,
                    goal=_brief_goal,
                    timeline=str(structure.get("timeline_weeks", "")),
                    primary_persona=_prim_title,
                    confidence_grounded=_cs_grounded_items,
                    confidence_missing=_cd_missing_items,
                    full_campaign={
                        "structure":                structure,
                        "messaging":                messaging,
                        "rollout":                  rollout,
                        "ctx":                      ctx,
                        "readiness_score":          readiness["readiness"],
                        "readiness_structure_score": readiness["structure_score"],
                        "readiness_evidence_score":  readiness["evidence_score"],
                        "readiness_product_ok":      readiness["product_ok"],
                        "readiness_persona_ok":      readiness["persona_ok"],
                    },
                    status=_save_status,
                )
                st.session_state.current_campaign_db_id = _saved_id
            st.rerun()

        # ── Action bar: Asana push handler ────────────────────────────────────
        if _ab_asana_clicked:
            _mod_rollout = copy.deepcopy(rollout)
            for _ph_i, _ph in enumerate(_mod_rollout.get("phases", [])):
                for _ti, _t in enumerate(_ph.get("tasks", [])):
                    _ok = f"to_{_ph_i}_{_ti}"
                    _dk = f"td_{_ph_i}_{_ti}"
                    if _ok in st.session_state:
                        _t["owner"]   = st.session_state[_ok]
                    if _dk in st.session_state:
                        _t["due_day"] = st.session_state[_dk]
            _pos = st.session_state.pipeline_results["messaging"].get(
                "positioning_statement", "Campaign Kickoff"
            )
            _campaign_payload = {
                "rollout":           _mod_rollout,
                "structure":         st.session_state.pipeline_results["structure"],
                "knowledge_context": st.session_state.pipeline_results["ctx"],
                "messaging":         st.session_state.pipeline_results.get("messaging", {}),
            }
            try:
                with st.spinner("Creating Asana project…"):
                    _asana_tok = st.session_state.asana_sidebar_token or None
                    _asana_ws  = st.session_state.asana_sidebar_workspace or None
                    _url = push_to_asana(_campaign_payload, _pos,
                                         token=_asana_tok, workspace_gid=_asana_ws)
                st.session_state.asana_url   = _url
                st.session_state.asana_error = ""
                if st.session_state.current_campaign_db_id:
                    _db_update_asana(st.session_state.current_campaign_db_id, _url, status=_sys_status)
                else:
                    _pushed_id = _db_save(
                        username=st.session_state.logged_in_user,
                        brief_text=st.session_state.enriched_brief,
                        product=_prd_name_d,
                        goal=_brief_goal,
                        timeline=str(structure.get("timeline_weeks", "")),
                        primary_persona=_prim_title,
                        confidence_grounded=_cs_grounded_items,
                        confidence_missing=_cd_missing_items,
                        full_campaign={
                            "structure":                structure,
                            "messaging":                messaging,
                            "rollout":                  rollout,
                            "ctx":                      ctx,
                            "readiness_score":          readiness["readiness"],
                            "readiness_structure_score": readiness["structure_score"],
                            "readiness_evidence_score":  readiness["evidence_score"],
                            "readiness_product_ok":      readiness["product_ok"],
                            "readiness_persona_ok":      readiness["persona_ok"],
                        },
                        asana_url=_url,
                        status=_sys_status,
                    )
                    st.session_state.current_campaign_db_id = _pushed_id
                st.rerun()
            except Exception as _e:
                st.session_state.asana_error = str(_e)
                st.rerun()

        # ── Export: deck download ─────────────────────────────────────────────
        st.markdown("""
<div style="margin-top: 2rem; padding-top: 1.5rem; border-top: 1px solid #1E1E2E;">
  <div class="section-label">Export</div>
</div>
""", unsafe_allow_html=True)

        if st.session_state.pptx_bytes:
            _raw_camp_name = (
                st.session_state.pipeline_results.get("messaging", {}).get("campaign_name", "")
                or "campaign_deck"
            )
            _pptx_filename = re.sub(r"[^\w\s-]", "", _raw_camp_name).strip()
            _pptx_filename = re.sub(r"[\s-]+", "_", _pptx_filename) + ".pptx"
            st.download_button(
                "⬇  Download Kickoff Deck (.pptx)",
                data=st.session_state.pptx_bytes,
                file_name=_pptx_filename,
                mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            )
