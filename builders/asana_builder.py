import json
import os
import re
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

_CP_DAY_RE = re.compile(r'\bDay\s+(\d+)\b', re.IGNORECASE)

ASANA_TOKEN         = os.environ.get("ASANA_TOKEN", "")
ASANA_WORKSPACE_GID = os.environ.get("ASANA_WORKSPACE_GID", "")

_BASE      = "https://app.asana.com/api/1.0"
_DELAY     = 0.05   # seconds between sequential API calls
_WORKERS   = 3      # parallel task-create threads


def fetch_workspace_gid(token: str) -> str | None:
    """Return the first workspace GID accessible by this PAT, or None on failure."""
    try:
        req = urllib.request.Request(
            f"{_BASE}/workspaces",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            ws = data.get("data", [])
            return ws[0]["gid"] if ws else None
    except Exception:
        return None


# Ordered list of (search_string, canonical_team_name).
# Checked from top to bottom; first match in the checkpoint text wins.
_CHECKPOINT_TEAMS = [
    ("Demand Generation", "Demand Generation"),
    ("Demand Gen",        "Demand Generation"),
    ("Product Marketing", "Product Marketing"),
    ("RevOps",            "RevOps"),
    ("Marketing Ops",     "Marketing Ops"),
    ("Paid Social",       "Paid Social"),
    ("Field Marketing",   "Field Marketing"),
    ("Customer Marketing","Customer Marketing"),
    ("Customer Success",  "Customer Success"),
    ("SDR/BDR",           "SDR/BDR"),
    ("SDR",               "SDR/BDR"),
    ("BDR",               "SDR/BDR"),
    ("PMM",               "Product Marketing"),
]


def _api(method: str, path: str, body: dict = None) -> dict:
    url  = f"{_BASE}{path}"
    data = json.dumps({"data": body}).encode("utf-8") if body is not None else None
    req  = urllib.request.Request(
        url, data=data,
        headers={
            "Authorization": f"Bearer {ASANA_TOKEN}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))["data"]
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Asana {method} {path} → HTTP {e.code}: {detail}") from None


def _project_name(campaign: dict) -> str:
    """Use the generated campaign name; fall back to product+goal+weeks if absent."""
    # Prefer the campaign name generated during the messaging step
    camp_name = (campaign.get("messaging") or {}).get("campaign_name", "")
    if camp_name and camp_name.strip():
        return camp_name.strip()[:100]

    # Fallback: build from structure fields
    ctx       = campaign.get("knowledge_context", {})
    structure = campaign.get("structure", {})
    products  = ctx.get("matched_products", [])
    product   = products[0].get("name", "Docebo").replace("Docebo ", "").strip() if products else "Docebo"
    primary   = structure.get("campaign_goal", {}).get("primary", {}) if isinstance(structure.get("campaign_goal"), dict) else {}
    target    = str(primary.get("target", "")).strip()
    metric    = " ".join(str(primary.get("metric", "")).split()[:2]).title()
    weeks     = str(structure.get("timeline_weeks", "")).strip()
    parts     = [product]
    if target and metric:
        parts.append(f"{target} {metric}")
    if weeks:
        parts.append(f"{weeks} Weeks")
    name = ", ".join(parts)
    return name[:60] if len(name) > 60 else name


def _checkpoint_owner(text: str) -> str:
    """Return the first recognised team name found in checkpoint text."""
    lower = text.lower()
    # Find which known team appears earliest in the string
    best_pos  = len(text) + 1
    best_name = "Marketing Ops"
    for pattern, canonical in _CHECKPOINT_TEAMS:
        pos = lower.find(pattern.lower())
        if pos != -1 and pos < best_pos:
            best_pos  = pos
            best_name = canonical
    return best_name


def push_to_asana(campaign: dict, positioning_statement: str = "",
                  token: str = None, workspace_gid: str = None) -> str:
    """
    Build an Asana project from a campaign output dict and return its URL.

    Expected keys in campaign:
      campaign["rollout"]["phases"]                    — list of phase dicts
      campaign["rollout"]["success_metrics"]           — list of strings
      campaign["rollout"]["human_review_checkpoints"]  — list of strings
      campaign["structure"]                            — brief structure (for naming)
      campaign["knowledge_context"]                    — matched nodes (for naming)
    """
    global ASANA_TOKEN, ASANA_WORKSPACE_GID
    _orig_token = ASANA_TOKEN
    _orig_ws    = ASANA_WORKSPACE_GID
    if token:
        ASANA_TOKEN = token
    if workspace_gid:
        ASANA_WORKSPACE_GID = workspace_gid
    try:
        return _push_to_asana_inner(campaign, positioning_statement)
    finally:
        ASANA_TOKEN         = _orig_token
        ASANA_WORKSPACE_GID = _orig_ws


def _push_to_asana_inner(campaign: dict, positioning_statement: str) -> str:
    project_name = _project_name(campaign)

    # ── 1. Create project ─────────────────────────────────────────────────────
    project     = _api("POST", "/projects", {
        "name":         project_name,
        "workspace":    ASANA_WORKSPACE_GID,
        "default_view": "list",
        "color":        "dark-purple",
    })
    project_gid = project["gid"]
    project_url = f"https://app.asana.com/0/{project_gid}/list"
    time.sleep(_DELAY)

    today     = date.today()
    rollout   = campaign.get("rollout", {})
    phases    = rollout.get("phases", [])
    messaging = campaign.get("messaging", {})

    # ── Detect blocked pillars (missing proof points) ─────────────────────────
    blocked_pillars = [
        p for p in messaging.get("pillars", [])
        if str(p.get("proof_point", "")).startswith("[PROOF POINT NEEDED")
    ]

    # ── 2. Create all sections first in one sequential pass ───────────────────
    blocker_sec = None
    if blocked_pillars:
        blocker_sec = _api("POST", f"/projects/{project_gid}/sections",
                           {"name": "Blockers — Resolve Before Activating"})
        time.sleep(_DELAY)

    phase_sections: dict[str, str] = {}   # phase_name → section_gid
    for phase in phases:
        phase_name = phase.get("phase", "Phase")
        sec        = _api("POST", f"/projects/{project_gid}/sections", {"name": phase_name})
        phase_sections[phase_name] = sec["gid"]
        time.sleep(_DELAY)

    metrics_sec = _api("POST", f"/projects/{project_gid}/sections",
                       {"name": "Success Metrics"})
    time.sleep(_DELAY)
    checkpoints_sec = _api("POST", f"/projects/{project_gid}/sections",
                           {"name": "Human Review Checkpoints"})
    time.sleep(_DELAY)

    # ── 3. Build the full task list ───────────────────────────────────────────
    task_bodies: list[dict] = []

    # Blocker task — injected first so it sits at the top of the project
    if blocked_pillars and blocker_sec:
        pillar_lines = "; ".join(
            f"Pillar {i + 1} ({p.get('title', 'untitled')})"
            for i, p in enumerate(messaging.get("pillars", []))
            if str(p.get("proof_point", "")).startswith("[PROOF POINT NEEDED")
        )
        blocker_name = f"[BLOCKER] Add case study or proof point for: {pillar_lines}"
        task_bodies.append({
            "name":        blocker_name,
            "notes":       (
                "Campaign cannot be activated until each blocked pillar has a "
                "named-customer case study or verified stat in Marketing Brain.\n\n"
                f"Blocked: {pillar_lines}\n\n"
                "Action: source case study from CS / field, add to marketing_brain.json, "
                "then re-run campaign to regenerate proof point assignment."
            ),
            "due_on":      today.isoformat(),
            "projects":    [project_gid],
            "memberships": [{"project": project_gid, "section": blocker_sec["gid"]}],
        })

    for phase in phases:
        phase_name = phase.get("phase", "Phase")
        sec_gid    = phase_sections[phase_name]
        for t in phase.get("tasks", []):
            due_day        = int(t.get("due_day", 0))
            due_dt         = today + timedelta(days=due_day)
            due_date       = due_dt.isoformat()
            due_label      = f"{due_dt.strftime('%b')} {due_dt.day}"   # "Jun 22" — no day name
            owner          = t.get("owner", "").strip()
            task_text      = t.get("task", "Task").strip()
            # Strip owner from start of task text if the generator already embedded it
            if owner and task_text.lower().startswith(owner.lower()):
                task_text = task_text[len(owner):].lstrip(" \t-—,")
            task_name = f"{owner}: {task_text} · {due_label}" if owner else f"{task_text} · {due_label}"
            task_bodies.append({
                "name":        task_name,
                "notes":       f"Due: Day {due_day} from campaign launch",
                "due_on":      due_date,
                "projects":    [project_gid],
                "memberships": [{"project": project_gid, "section": sec_gid}],
            })

    for metric in rollout.get("success_metrics", []):
        task_bodies.append({
            "name":        metric,
            "notes":       "Owner: Marketing Ops",
            "projects":    [project_gid],
            "memberships": [{"project": project_gid, "section": metrics_sec["gid"]}],
        })

    for checkpoint in rollout.get("human_review_checkpoints", []):
        owner = _checkpoint_owner(checkpoint)
        m = _CP_DAY_RE.search(checkpoint)
        if m:
            cp_day    = int(m.group(1))
            cp_dt     = today + timedelta(days=cp_day)
            cp_date   = cp_dt.isoformat()
            cp_label  = f"{cp_dt.strftime('%b')} {cp_dt.day}"
            cp_name   = _CP_DAY_RE.sub(cp_label, checkpoint, count=1)
        else:
            cp_day, cp_date, cp_name = 0, today.isoformat(), checkpoint
        task_bodies.append({
            "name":        cp_name,
            "notes":       f"Owner: {owner}",
            "due_on":      cp_date,
            "projects":    [project_gid],
            "memberships": [{"project": project_gid, "section": checkpoints_sec["gid"]}],
        })

    # ── 4. Create all tasks in parallel — do not wait between creates ─────────
    def _create(body: dict) -> dict:
        time.sleep(_DELAY)
        return _api("POST", "/tasks", body)

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        futures = [pool.submit(_create, body) for body in task_bodies]
        for fut in as_completed(futures):
            fut.result()  # re-raises any per-task exception

    return project_url
