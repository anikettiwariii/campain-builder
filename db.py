"""SQLite persistence for campaigns. campaigns.db lives in the project directory."""
import sqlite3
import json
import uuid
import os
from datetime import datetime, timezone

_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "campaigns.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
    id                  TEXT PRIMARY KEY,
    username            TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    brief_text          TEXT,
    product             TEXT,
    goal                TEXT,
    timeline            TEXT,
    primary_persona     TEXT,
    status              TEXT DEFAULT 'Draft',
    confidence_grounded TEXT,
    confidence_missing  TEXT,
    full_campaign_json  TEXT,
    asana_url           TEXT
)
"""


def _conn():
    c = sqlite3.connect(_DB)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _conn() as c:
        c.execute(_SCHEMA)


def save_campaign(
    username: str,
    brief_text: str,
    product: str,
    goal: str,
    timeline: str,
    primary_persona: str,
    confidence_grounded: list,
    confidence_missing: list,
    full_campaign: dict,
    asana_url: str = None,
    status: str = "Draft",
) -> str:
    init_db()
    cid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        c.execute(
            """INSERT INTO campaigns
               (id, username, created_at, brief_text, product, goal, timeline,
                primary_persona, status, confidence_grounded, confidence_missing,
                full_campaign_json, asana_url)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                cid, username, now, brief_text, product, goal, timeline,
                primary_persona, status,
                json.dumps(confidence_grounded),
                json.dumps(confidence_missing),
                json.dumps(full_campaign),
                asana_url,
            ),
        )
    return cid


def update_status(campaign_id: str, status: str):
    init_db()
    with _conn() as c:
        c.execute("UPDATE campaigns SET status=? WHERE id=?", (status, campaign_id))


def update_asana(campaign_id: str, asana_url: str, status: str = "Active"):
    init_db()
    with _conn() as c:
        c.execute(
            "UPDATE campaigns SET asana_url=?, status=? WHERE id=?",
            (asana_url, status, campaign_id),
        )


def get_all(username: str) -> list:
    init_db()
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM campaigns WHERE username=? ORDER BY created_at DESC",
            (username,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_one(campaign_id: str) -> dict | None:
    init_db()
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM campaigns WHERE id=?", (campaign_id,)
        ).fetchone()
    return dict(row) if row else None


def _derive_status(jstr: str, stored_status: str) -> str:
    """Derive display status from stored JSON readiness data, falling back to stored status."""
    if stored_status == "Complete":
        return "Complete"
    try:
        data = json.loads(jstr or "{}")
        struct = data.get("readiness_structure_score")
        evid   = data.get("readiness_evidence_score")
        if struct is not None and evid is not None:
            # New 4-bucket model: each dimension evaluated independently
            if struct >= 70 and evid >= 70:
                return "Execution Ready"
            elif struct >= 70 and evid < 70:
                return "Hypothesis"
            elif struct < 70 and evid >= 70:
                return "Incomplete"
            else:
                return "Blocked"
        # Fall back to combined score for campaigns saved before structure/evidence split
        score = data.get("readiness_score")
        if score is not None:
            if score < 30:
                return "Blocked"
            elif score < 60:
                return "Hypothesis"
            else:
                return "Execution Ready"
    except Exception:
        pass
    # Map legacy status strings to new vocabulary
    return {
        "Active":          "Execution Ready",
        "In Review":       "Hypothesis",
        "Draft":           "Hypothesis",
        "Blocked":         "Blocked",
        "Hypothesis":      "Hypothesis",
        "Incomplete":      "Incomplete",
        "Execution Ready": "Execution Ready",
    }.get(stored_status, "Hypothesis")


def backfill_readiness_scores(username: str | None = None) -> int:
    """Recompute and persist structure/evidence readiness scores for campaigns that lack them.

    Uses stored ctx and brief_text as input so no API call is needed.
    Returns the number of campaigns updated.
    """
    from builders.campaign_builder import compute_readiness_score  # local import avoids circular dep

    init_db()
    with _conn() as c:
        if username:
            rows = c.execute(
                "SELECT id, full_campaign_json, brief_text, status FROM campaigns WHERE username=?",
                (username,),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT id, full_campaign_json, brief_text, status FROM campaigns"
            ).fetchall()

    updated = 0
    for row in rows:
        try:
            data = json.loads(row["full_campaign_json"] or "{}")
            if data.get("readiness_structure_score") is not None:
                continue  # already has new-model scores, skip

            ctx       = data.get("ctx", {})
            structure = data.get("structure", {})
            messaging = data.get("messaging", {})
            rollout   = data.get("rollout",   {})
            if not ctx:
                continue

            # Use stored brief_text as best available proxy for explicit_brief
            proxy_brief = row["brief_text"] or ""

            r = compute_readiness_score(
                structure=structure,
                messaging=messaging,
                rollout=rollout,
                ctx=ctx,
                explicit_brief=proxy_brief,
            )

            data["readiness_score"]            = r["readiness"]
            data["readiness_structure_score"]  = r["structure_score"]
            data["readiness_evidence_score"]   = r["evidence_score"]
            data["readiness_product_ok"]       = r["product_ok"]
            data["readiness_persona_ok"]       = r["persona_ok"]

            stored_status = row["status"]
            new_status = r["status"] if stored_status != "Complete" else "Complete"

            with _conn() as c2:
                c2.execute(
                    "UPDATE campaigns SET full_campaign_json=?, status=? WHERE id=?",
                    (json.dumps(data), new_status, row["id"]),
                )
            updated += 1
        except Exception as exc:
            import traceback
            traceback.print_exc()

    return updated


def pipeline_summary(username: str) -> tuple:
    """Returns (total, execution_ready, blocked) using derived status from readiness JSON."""
    init_db()
    with _conn() as c:
        rows = c.execute(
            "SELECT status, full_campaign_json FROM campaigns WHERE username=?", (username,)
        ).fetchall()
    total = len(rows)
    execution_ready = sum(1 for r in rows if _derive_status(r[1], r[0]) == "Execution Ready")
    blocked         = sum(1 for r in rows if _derive_status(r[1], r[0]) == "Blocked")
    return total, execution_ready, blocked
