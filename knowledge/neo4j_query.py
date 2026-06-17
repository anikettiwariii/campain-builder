"""
Neo4j Cypher retrieval layer.

query_graph_neo4j(brief) → same dict format as query_graph() in graph_query.py
so it is a drop-in replacement.

Traversal strategy
──────────────────
1. Detect seed product / persona IDs from the brief (Python signal maps — same
   as the NetworkX version; keeps detection logic consistent between backends).
2. Traverse via Cypher:
     Product → SOLVES_PAIN → Pain
     Product → TARGETS_PERSONA → Persona
     Channel → REACHES_PERSONA → Persona   (reverse)
     ProofPoint → VALIDATES_PRODUCT → Product
     ProofPoint → SUPPORTS_PAIN → Pain
     ProofPoint → RESONATES_WITH → Persona
3. Score and rank proof points.
4. Semantic fallback: if no ProofPoint has a VALIDATES_PRODUCT edge to the
   matched product, compute cosine similarity against node embeddings in Python
   and flag the nearest match as amber (no edge = no named customer claim).

The last Cypher query that ran is cached as neo4j_query.LAST_CYPHER so the
app can display it for the demo.
"""
import json
import logging

log = logging.getLogger(__name__)

# Exposed for the UI "show Cypher" panel
LAST_CYPHER: str = ""
LAST_TRAVERSAL_SUMMARY: dict = {}

# ── Detection signal maps (mirror of graph_query.py) ─────────────────────────

_PRODUCT_SIGNALS = {
    "agenthub":            ["agent", "agents", "agentic", "agenthub", "agent hub",
                            "ai agent", "workflow automation", "autonomous", "no-code agent"],
    "skills_intelligence": ["skills intelligence", "skill intelligence", "skills",
                            "skill gap", "enterprise skills", "workforce skills",
                            "skills data", "skills profiles"],
    "headless_learning":   ["headless", "embed", "embedded", "context switch",
                            "existing tools", "inject"],
    "roleplay":            ["roleplay", "role play", "practice", "simulation", "coach",
                            "scenario", "sales training", "objection"],
    "content_creator":     ["content", "course creation", "authoring", "create courses",
                            "build courses", "content backlog"],
    "harmony_ai":          ["harmony", "search", "discovery", "find content",
                            "neural search", "ai search"],
    "advanced_analytics":  ["analytics", "advanced analytics", "learning analytics",
                            "bi", "business intelligence", "reporting", "dashboard",
                            "insights", "data reports", "learning data"],
    "enterprise_knowledge": ["enterprise knowledge", "knowledge search", "knowledge base",
                             "docebo knowledge", "enterprise kb", "knowledge management",
                             "trusted answers", "knowledge hub"],
}

_PERSONA_SIGNALS = {
    "vp_ld":              ["l&d", "learning", "training", "development"],
    "cpo":                ["people", "hr", "human resources", "workforce", "talent"],
    "ld_program_manager": ["program manager", "instructional", "specialist", "coordinator"],
}

_PAIN_KW = {
    "admin_overload":        ["admin", "manual", "tedious", "overhead", "too many steps",
                              "spreadsheet", "time", "hours", "burden"],
    "no_roi_proof":          ["roi", "proof", "business outcome", "reporting", "analytics", "cfo",
                              "board", "budget", "prove", "justify", "cost center"],
    "personalization_gap":   ["personaliz", "personalize", "one-size", "scale", "personalized",
                              "individual", "paths"],
    "skills_visibility":     ["skills", "skill gap", "capability", "workforce readiness",
                              "gaps", "reskill"],
    "ai_readiness_gap":      ["ai ready", "ai literar", "ai training", "ai tool",
                              "artificial intelligence", "ai readiness", "ai literacy"],
    "content_creation_time": ["content creation", "course creation", "authoring"],
}

_NAMED_CUSTOMERS = [
    "Bethany Care Society", "MidFirst Bank", "Disguise",
    "SNCF", "Société Générale", "Segula Technologies",
]


# ── Brief detection helpers ───────────────────────────────────────────────────

def _detect_product_ids(brief_lower: str, session) -> list:
    """Detect product IDs from brief text using signal maps + Neo4j name lookup."""
    detected = []

    # Signal-map detection (same logic as NetworkX version)
    for prod_id, signals in _PRODUCT_SIGNALS.items():
        if any(sig in brief_lower for sig in signals):
            if prod_id not in detected:
                detected.append(prod_id)

    # Name / alias lookup via Cypher
    rows = session.run(
        "MATCH (p:Product) RETURN p.id AS id, p.name AS name, p.also_known_as AS aka"
    ).data()
    for row in rows:
        nid = row["id"]
        if nid in detected:
            continue
        if row["name"] and row["name"].lower() in brief_lower:
            detected.append(nid)
            continue
        aka_list = json.loads(row.get("aka") or "[]")
        if any(alias.lower() in brief_lower for alias in aka_list):
            detected.append(nid)

    return detected


def _detect_persona_ids(brief_lower: str, session) -> list:
    detected = []
    for persona_id, signals in _PERSONA_SIGNALS.items():
        if any(sig in brief_lower for sig in signals):
            if persona_id not in detected:
                detected.append(persona_id)

    rows = session.run(
        "MATCH (p:Persona) RETURN p.id AS id, p.title AS title, p.also_known_as AS aka"
    ).data()
    for row in rows:
        nid = row["id"]
        if nid in detected:
            continue
        if row["title"] and row["title"].lower() in brief_lower:
            detected.append(nid)
            continue
        aka_list = json.loads(row.get("aka") or "[]")
        if any(alias.lower() in brief_lower for alias in aka_list):
            detected.append(nid)

    return detected


# ── Cosine similarity (Python-side, no GDS plugin needed) ────────────────────

def _cosine(a: list, b: list) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot   = sum(x * y for x, y in zip(a, b))
    mag_a = sum(x * x for x in a) ** 0.5
    mag_b = sum(x * x for x in b) ** 0.5
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _embed_brief(brief: str) -> list:
    """Embed the brief text using the same model/pooling as the loader."""
    try:
        from knowledge.neo4j_loader import _embed
        return _embed(brief)
    except Exception:
        return []


# ── Node data reconstruction ──────────────────────────────────────────────────
# All functions use _raw (the full source JSON stored at load time) so no
# field drift is possible between the JSON and what the pipeline receives.

def _from_raw(row: dict, fallback: dict = None) -> dict:
    """Deserialise _raw property; merge fallback keys for anything missing."""
    raw_str = row.get("_raw")
    if raw_str:
        try:
            base = json.loads(raw_str)
            # Always ensure 'id' is present from the row in case _raw omits it
            base.setdefault("id", row.get("id", ""))
            return base
        except Exception:
            pass
    return fallback or {"id": row.get("id", "")}


def _neo4j_to_product(row: dict) -> dict:
    return _from_raw(row, {"id": row.get("id", ""), "name": row.get("name", "")})


def _neo4j_to_persona(row: dict) -> dict:
    return _from_raw(row, {"id": row.get("id", ""), "title": row.get("title", "")})


def _neo4j_to_pain(row: dict) -> dict:
    return _from_raw(row, {"id": row.get("id", ""), "label": row.get("label", ""),
                           "description": row.get("description", "")})


def _neo4j_to_channel(row: dict) -> dict:
    return _from_raw(row, {"id": row.get("id", ""), "name": row.get("name", "")})


def _neo4j_to_pp(row: dict) -> dict:
    d = _from_raw(row, {"id": row.get("id", ""), "claim": row.get("claim", ""),
                        "source": row.get("source", "")})
    # Ensure list fields used by scoring are always present
    for k in ("use_for_products", "use_for_pains", "use_for_personas"):
        if k not in d:
            d[k] = []
    return d


# ── Main query ────────────────────────────────────────────────────────────────

def query_graph_neo4j(brief: str) -> dict:
    """
    Traverse the Neo4j graph to produce a campaign context dict.

    Returns the same structure as graph_query.query_graph() so the pipeline
    is fully backward-compatible.
    """
    global LAST_CYPHER, LAST_TRAVERSAL_SUMMARY

    from knowledge.neo4j_connection import get_driver
    from knowledge.neo4j_loader import load_brain_into_neo4j
    driver = get_driver()
    if driver is None:
        raise RuntimeError("Neo4j unavailable")

    # Ensure data is loaded (no-op if already populated)
    load_brain_into_neo4j()

    brief_lower = brief.lower()

    with driver.session() as session:

        # ── Step 1: Seed detection ─────────────────────────────────────────────
        product_ids = _detect_product_ids(brief_lower, session)
        persona_ids = _detect_persona_ids(brief_lower, session)
        _layer1_persona_ids = list(persona_ids)   # snapshot before product expansion

        # ── Step 2: Expand personas via Product → TARGETS_PERSONA ─────────────
        if product_ids:
            extra_personas = session.run(
                """
                MATCH (prod:Product)-[:TARGETS_PERSONA]->(persona:Persona)
                WHERE prod.id IN $product_ids
                RETURN DISTINCT persona.id AS id
                """,
                product_ids=product_ids,
            ).data()
            for row in extra_personas:
                if row["id"] not in persona_ids:
                    persona_ids.append(row["id"])

        if not persona_ids:
            persona_ids = [
                r["id"]
                for r in session.run("MATCH (p:Persona) RETURN p.id AS id LIMIT 2").data()
            ]

        # Brief-only pain detection
        brief_pain_ids = [
            pid for pid, kws in _PAIN_KW.items()
            if any(kw in brief_lower for kw in kws)
        ]

        # ── Step 3: Traverse pains ─────────────────────────────────────────────
        # Cypher 1 — product pains
        CYPHER_PAINS = """
MATCH (prod:Product)-[:SOLVES_PAIN]->(pain:Pain)
WHERE prod.id IN $product_ids
RETURN DISTINCT pain.id AS id, pain.label AS label,
       pain.description AS description, pain.embedding AS embedding
"""
        pain_rows = session.run(CYPHER_PAINS, product_ids=product_ids).data()
        pain_ids  = {r["id"] for r in pain_rows}

        if not pain_ids:
            persona_pain_rows = session.run(
                """
                MATCH (persona:Persona)-[:EXPERIENCES_PAIN]->(pain:Pain)
                WHERE persona.id IN $persona_ids
                RETURN DISTINCT pain.id AS id
                """,
                persona_ids=persona_ids,
            ).data()
            pain_ids = {r["id"] for r in persona_pain_rows}

        # Include brief-detected pains
        pain_ids.update(brief_pain_ids)

        # ── Step 3b: Sort personas by graph degree relative to matched product ──
        # Primary persona = highest count of direct graph edges to product neighbourhood.
        # Scoring: direct TARGETS_PERSONA edge (×3) + channels that reach persona (×1)
        #         + proof points that resonate with persona (×1). Tiebreak by id ASC.
        # Always run (even single-persona) so score data is available for the UI panel.
        _persona_scores_raw: list = []
        _persona_ids_for_sort = persona_ids if persona_ids else []
        _product_ids_for_scoring = product_ids if not isinstance(product_ids, set) else list(product_ids)
        if _persona_ids_for_sort:
            degree_rows = session.run(
                """
                MATCH (persona:Persona)
                WHERE persona.id IN $persona_ids
                OPTIONAL MATCH (prod:Product)-[:TARGETS_PERSONA]->(persona)
                  WHERE prod.id IN $product_ids
                OPTIONAL MATCH (ch:Channel)-[:REACHES_PERSONA]->(persona)
                OPTIONAL MATCH (pp:ProofPoint)-[:RESONATES_WITH]->(persona)
                WITH persona.id AS pid, persona.title AS title,
                     count(DISTINCT prod) AS targets_count,
                     count(DISTINCT ch)   AS reaches_count,
                     count(DISTINCT pp)   AS resonates_count
                RETURN pid, title,
                       (targets_count * 3 + reaches_count + resonates_count) AS degree,
                       targets_count, reaches_count, resonates_count
                ORDER BY degree DESC, pid ASC
                """,
                persona_ids=_persona_ids_for_sort,
                product_ids=_product_ids_for_scoring,
            ).data()
            if degree_rows:
                _persona_scores_raw = degree_rows
                if len(persona_ids) > 1:
                    persona_ids = [r["pid"] for r in degree_rows]

        # ── Step 4: Channels ───────────────────────────────────────────────────
        # ORDER BY coverage DESC so channels that reach more matched personas come first;
        # ch.id ASC tiebreaker makes the order fully deterministic across runs.
        # reached_personas captured for UI reasoning panel (which persona edge activated each channel).
        CYPHER_CHANNELS = """
MATCH (ch:Channel)-[:REACHES_PERSONA]->(persona:Persona)
WHERE persona.id IN $persona_ids
WITH ch, collect(DISTINCT persona.id) AS reached_personas, count(DISTINCT persona) AS coverage
RETURN ch.id AS id, ch._raw AS _raw, reached_personas
ORDER BY coverage DESC, ch.id ASC
"""
        channel_rows = session.run(CYPHER_CHANNELS, persona_ids=persona_ids).data()
        channel_ids  = [r["id"] for r in channel_rows]
        _channel_persona_map = {r["id"]: r.get("reached_personas", []) for r in channel_rows}

        # ── Step 5: Proof point traversal + scoring ────────────────────────────
        # Main proof point query — all three edge types in one pass
        CYPHER_PROOF_POINTS = """
MATCH (pp:ProofPoint)
WHERE (pp)-[:VALIDATES_PRODUCT]->(:Product {id: $product_id_0})
   OR (pp)-[:SUPPORTS_PAIN]->(:Pain)
   OR (pp)-[:RESONATES_WITH]->(:Persona)
WITH pp
OPTIONAL MATCH (pp)-[v:VALIDATES_PRODUCT]->(prod:Product)
  WHERE prod.id IN $product_ids
OPTIONAL MATCH (pp)-[s:SUPPORTS_PAIN]->(pain:Pain)
  WHERE pain.id IN $pain_ids
OPTIONAL MATCH (pp)-[rw:RESONATES_WITH]->(persona:Persona)
  WHERE persona.id IN $persona_ids
WITH pp,
     count(DISTINCT v)  AS val_count,
     count(DISTINCT s)  AS pain_count,
     count(DISTINCT rw) AS persona_count
WITH pp,
     val_count, pain_count, persona_count,
     (val_count * 3 + pain_count * 2 + persona_count * 1) AS raw_score
WHERE raw_score > 0
RETURN pp.id AS id, pp.claim AS claim, pp._raw AS _raw,
       pp.embedding AS embedding,
       val_count, pain_count, persona_count,
       raw_score AS score
ORDER BY score DESC
LIMIT 8
"""
        product_id_0 = product_ids[0] if product_ids else "__none__"
        pp_rows = session.run(
            CYPHER_PROOF_POINTS,
            product_id_0=product_id_0,
            product_ids=list(product_ids) if isinstance(product_ids, set) else product_ids,
            pain_ids=list(pain_ids),
            persona_ids=persona_ids,
        ).data()

        # Named-customer bonus (mirrors NetworkX scoring)
        def _final_score(row: dict) -> float:
            score = row.get("score", 0)
            if score >= 2 and any(name in (row.get("claim") or "") for name in _NAMED_CUSTOMERS):
                score += 1
            return score

        # Inclusion filter using Cypher-computed edge counts (not use_for_* fields,
        # which are not stored as direct Neo4j properties and always read as None).
        #
        # Named customer proof points: included if they have ANY relevant edge to the
        #   campaign context — product, pain, OR persona (val, pain, or persona_count > 0).
        # Non-named stats: must have at least one product/pain edge AND at least one persona
        #   edge. A proof point that only resonates with matched personas but has no
        #   VALIDATES_PRODUCT or SUPPORTS_PAIN connection to the current campaign is
        #   excluded — persona resonance alone means it may belong to a different product.
        def _include(row: dict) -> bool:
            claim         = row.get("claim", "")
            val_count     = row.get("val_count", 0) or 0
            pain_count    = row.get("pain_count", 0) or 0
            persona_count = row.get("persona_count", 0) or 0
            is_named = any(name in claim for name in _NAMED_CUSTOMERS)
            if is_named:
                return val_count > 0 or pain_count > 0 or persona_count > 0
            return (val_count > 0 or pain_count > 0) and persona_count > 0

        filtered     = [r for r in pp_rows if _include(r)]
        _all_scored  = sorted(filtered, key=_final_score, reverse=True)
        # Capture full scored list (up to 12) before truncation — used by UI reasoning panel
        _pp_full_scored = [
            {"id": r.get("id", ""), "claim": r.get("claim", ""),
             "score": _final_score(r),
             "use_for_products": r.get("use_for_products", "[]")}
            for r in _all_scored[:12]
        ]
        ranked   = _all_scored[:6]
        pp_ids   = [r["id"] for r in ranked]

        # ── Step 6: Semantic fallback when no VALIDATES_PRODUCT hit ───────────
        # use val_count from the Cypher result (use_for_products is not selected)
        has_validated = any((r.get("val_count") or 0) > 0 for r in ranked)
        semantic_ids: list = []
        if not has_validated and product_ids:
            log.info("No VALIDATES_PRODUCT hit — running semantic embedding fallback")
            brief_emb = _embed_brief(brief)
            if brief_emb:
                emb_rows = session.run(
                    "MATCH (pp:ProofPoint) WHERE pp.embedding IS NOT NULL "
                    "RETURN pp.id AS id, pp.embedding AS embedding, pp.claim AS claim"
                ).data()
                scored = [
                    (r["id"], _cosine(brief_emb, r["embedding"]))
                    for r in emb_rows
                    if r["id"] not in pp_ids
                ]
                scored.sort(key=lambda x: x[1], reverse=True)

                # Widen candidate pool before filtering — we need room to discard
                # proof points that have no graph edge to the current campaign.
                sem_candidate_ids = [sid for sid, _ in scored[:10]]
                if sem_candidate_ids:
                    # Check each candidate's edge counts against matched campaign nodes.
                    # Reuse the same _include filter as the primary ranked list so a
                    # semantically similar proof point that has no product/pain/persona
                    # connection (e.g. an AgentHub stat in a Content Creator campaign)
                    # is excluded rather than padding the list to six.
                    sem_check_rows = session.run(
                        "MATCH (pp:ProofPoint) WHERE pp.id IN $ids "
                        "OPTIONAL MATCH (pp)-[v:VALIDATES_PRODUCT]->(prod:Product) "
                        "  WHERE prod.id IN $product_ids "
                        "OPTIONAL MATCH (pp)-[s:SUPPORTS_PAIN]->(pain:Pain) "
                        "  WHERE pain.id IN $pain_ids "
                        "OPTIONAL MATCH (pp)-[rw:RESONATES_WITH]->(persona:Persona) "
                        "  WHERE persona.id IN $persona_ids "
                        "WITH pp, "
                        "     count(DISTINCT v)  AS val_count, "
                        "     count(DISTINCT s)  AS pain_count, "
                        "     count(DISTINCT rw) AS persona_count "
                        "RETURN pp.id AS id, pp.claim AS claim, pp._raw AS _raw, "
                        "       val_count, pain_count, persona_count",
                        ids=sem_candidate_ids,
                        product_ids=list(product_ids) if isinstance(product_ids, set) else product_ids,
                        pain_ids=list(pain_ids),
                        persona_ids=persona_ids,
                    ).data()
                    # Preserve cosine ordering, then apply the same inclusion filter
                    _sem_order = {sid: i for i, (sid, _) in enumerate(scored)}
                    sem_check_rows.sort(key=lambda r: _sem_order.get(r.get("id", ""), 999))
                    sem_qualified = [r for r in sem_check_rows if _include(r)]
                    # No padding — only include proof points that actually qualify
                    semantic_ids = [r["id"] for r in sem_qualified[:2]]
                    pp_ids.extend(semantic_ids)
                    _sem_id_set = set(semantic_ids)
                    ranked.extend(r for r in sem_qualified if r.get("id") in _sem_id_set)

        # ── Step 7: Full node data fetch for products / personas / pains ──────
        prod_rows = session.run(
            "MATCH (p:Product) WHERE p.id IN $ids RETURN p.id AS id, p._raw AS _raw",
            ids=product_ids if not isinstance(product_ids, set) else list(product_ids),
        ).data()

        per_rows = session.run(
            "MATCH (p:Persona) WHERE p.id IN $ids RETURN p.id AS id, p._raw AS _raw",
            ids=persona_ids,
        ).data()

        pain_full_rows = session.run(
            "MATCH (p:Pain) WHERE p.id IN $ids RETURN p.id AS id, p._raw AS _raw",
            ids=list(pain_ids),
        ).data()

        # Brand voice / company context live in the JSON (not Neo4j)
        import json as _json
        import os
        _brain_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "knowledge", "marketing_brain.json",
        )
        with open(_brain_path) as f:
            _brain = _json.load(f)

    # ── Assemble output ────────────────────────────────────────────────────────

    # Record Cypher for UI display
    LAST_CYPHER = CYPHER_PROOF_POINTS.strip()
    LAST_TRAVERSAL_SUMMARY = {
        "product_ids":        list(product_ids) if not isinstance(product_ids, set) else list(product_ids),
        "persona_ids":        persona_ids,
        "pain_ids":           list(pain_ids),
        "channel_ids":        channel_ids,
        "proof_point_ids":    pp_ids,
        "semantic_fallback_ids": semantic_ids,
        "cypher_used":        LAST_CYPHER,
        # UI reasoning panel data — derived from existing queries, no new Cypher needed
        "persona_scores":     _persona_scores_raw,
        "channel_persona_map": _channel_persona_map,
        "pp_full_scored":     _pp_full_scored,
    }

    return {
        "matched_products": [_neo4j_to_product(r) for r in prod_rows],
        "matched_personas": [_neo4j_to_persona(r) for r in per_rows],
        "matched_pains":    [_neo4j_to_pain(r)    for r in pain_full_rows],
        "matched_channels": [_neo4j_to_channel(r) for r in channel_rows],
        "proof_points":     [_neo4j_to_pp(r)      for r in ranked[:6]],
        "brand_voice":      _brain.get("brand_voice", {}),
        "company_context":  _brain.get("company_context", {}),
        "meta":             _brain.get("meta", {}),
        "_matched_node_ids": {
            "products":     list(product_ids) if not isinstance(product_ids, set) else list(product_ids),
            "personas":     persona_ids,
            "pains":        list(pain_ids),
            "channels":     channel_ids,
            "proof_points": pp_ids,
        },
        "_neo4j": True,
        "_semantic_fallback_ids": semantic_ids,
        "persona_from_brief": bool(_layer1_persona_ids),
    }
