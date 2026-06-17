"""
Graph traversal query layer — routes to Neo4j when available, NetworkX otherwise.

query_graph(brief) tries the Neo4j backend first. If Neo4j is unreachable it
falls back to the NetworkX DiGraph transparently — the rest of the pipeline
sees the same dict shape either way.

The NetworkX implementation lives below the router and is unchanged.
"""
import logging
import re

from knowledge.graph_builder import build_marketing_graph

log = logging.getLogger(__name__)


def query_graph(brief: str) -> dict:
    """Route brief to Neo4j or NetworkX depending on availability."""
    try:
        from knowledge.neo4j_connection import is_available
        if is_available():
            from knowledge.neo4j_query import query_graph_neo4j
            return query_graph_neo4j(brief)
    except Exception as exc:
        log.warning("Neo4j query failed (%s) — falling back to NetworkX", exc)
    return _query_graph_networkx(brief)

# ── Detection signal maps (kept in sync with campaign_builder.py) ─────────────

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


def _detect_products(brief_lower: str, G) -> list:
    detected = []
    for nid, attr in G.nodes(data=True):
        if attr.get("type") != "Product":
            continue
        data = attr.get("data", {})
        name = data.get("name", "").lower()
        if name in brief_lower or nid.replace("_", " ") in brief_lower:
            detected.append(nid)
            continue
        if any(alias.lower() in brief_lower for alias in data.get("also_known_as", [])):
            detected.append(nid)
            continue
        if any(kw in brief_lower for kw in _PRODUCT_SIGNALS.get(nid, [])):
            detected.append(nid)
    return detected


def _detect_personas(brief_lower: str, G) -> list:
    detected = []
    for nid, attr in G.nodes(data=True):
        if attr.get("type") != "Persona":
            continue
        data = attr.get("data", {})
        if data.get("title", "").lower() in brief_lower:
            detected.append(nid)
            continue
        if any(aka.lower() in brief_lower for aka in data.get("also_known_as", [])):
            detected.append(nid)
            continue
        if any(kw in brief_lower for kw in _PERSONA_SIGNALS.get(nid, [])):
            detected.append(nid)
    return detected


def _query_graph_networkx(brief: str) -> dict:
    """
    NetworkX fallback — traverse the in-memory DiGraph.

    Returns the same structure as the Neo4j path so the pipeline is unchanged.
    """
    G            = build_marketing_graph()
    brief_lower  = brief.lower()

    # ── Step 1: Seed detection ────────────────────────────────────────────────
    product_ids = _detect_products(brief_lower, G)
    persona_ids = _detect_personas(brief_lower, G)
    _layer1_persona_ids = list(persona_ids)   # snapshot before product expansion

    # ── Step 2: Expand personas via Product → TARGETS_PERSONA ────────────────
    for prod_id in product_ids:
        for neighbor, edata in G[prod_id].items():
            if edata.get("relationship") == "TARGETS_PERSONA" and neighbor not in persona_ids:
                persona_ids.append(neighbor)

    # Brief-only pain detection to augment persona signals
    brief_pain_ids = [
        pid for pid, kws in _PAIN_KW.items()
        if any(kw in brief_lower for kw in kws)
    ]

    # Fallback: if still no personas, use first two in graph
    if not persona_ids:
        persona_ids = [nid for nid, a in G.nodes(data=True) if a.get("type") == "Persona"][:2]

    persona_set = set(persona_ids)

    # ── Step 3: Expand pains via graph traversal ──────────────────────────────
    pain_ids = set()

    # Product → SOLVES_PAIN → Pain
    for prod_id in product_ids:
        for neighbor, edata in G[prod_id].items():
            if edata.get("relationship") == "SOLVES_PAIN":
                pain_ids.add(neighbor)

    # Persona → EXPERIENCES_PAIN → Pain (intersect with product pains when possible)
    persona_pain_ids = set()
    for persona_id in persona_ids:
        for neighbor, edata in G[persona_id].items():
            if edata.get("relationship") == "EXPERIENCES_PAIN":
                persona_pain_ids.add(neighbor)

    # Prefer product-specific pains; fall back to persona pains if product gave nothing
    pain_ids = pain_ids if pain_ids else persona_pain_ids
    # Also include brief-detected pains even if not in the graph paths
    pain_ids.update(pid for pid in brief_pain_ids if G.has_node(pid))

    # ── Step 4: Expand channels via reverse Channel → REACHES_PERSONA ─────────
    channel_ids = set()
    for persona_id in persona_ids:
        for pred_id, edata in G.pred[persona_id].items():
            if edata.get("relationship") == "REACHES_PERSONA":
                channel_ids.add(pred_id)

    # ── Step 4b: Compute persona degree scores (mirrors Neo4j Cypher formula) ─
    _nx_persona_scores = []
    for _pid in persona_ids:
        _targets  = sum(
            1 for _prod_id in product_ids
            if G.has_edge(_prod_id, _pid)
            and G[_prod_id][_pid].get("relationship") == "TARGETS_PERSONA"
        )
        _reaches  = sum(
            1 for _pred in G.predecessors(_pid)
            if G[_pred][_pid].get("relationship") == "REACHES_PERSONA"
        )
        _resonates = sum(
            1 for _pred in G.predecessors(_pid)
            if G[_pred][_pid].get("relationship") == "RESONATES_WITH"
        )
        _nx_persona_scores.append({
            "pid":            _pid,
            "title":          G.nodes[_pid].get("data", {}).get("title", _pid),
            "degree":         _targets * 3 + _reaches + _resonates,
            "targets_count":  _targets,
            "reaches_count":  _reaches,
            "resonates_count": _resonates,
        })
    _nx_persona_scores.sort(key=lambda r: (-r["degree"], r["pid"]))
    if len(persona_ids) > 1:
        persona_ids = [r["pid"] for r in _nx_persona_scores]

    _nx_channel_persona_map: dict = {}
    for _pid in persona_ids:
        for _pred in G.predecessors(_pid):
            if G[_pred][_pid].get("relationship") == "REACHES_PERSONA":
                _nx_channel_persona_map.setdefault(_pred, [])
                if _pid not in _nx_channel_persona_map[_pred]:
                    _nx_channel_persona_map[_pred].append(_pid)

    try:
        import knowledge.neo4j_query as _nq
        _nq.LAST_TRAVERSAL_SUMMARY = {
            "persona_scores":       _nx_persona_scores,
            "channel_persona_map":  _nx_channel_persona_map,
            "pp_full_scored":       [],
            "semantic_fallback_ids": [],
            "cypher_used":          "",
        }
    except Exception:
        pass

    # ── Step 5: Score and filter proof points ─────────────────────────────────
    product_set = set(product_ids)

    def _score(pp_id: str) -> int:
        score  = 0
        edges  = G[pp_id] if pp_id in G else {}
        ppdata = G.nodes[pp_id].get("data", {})
        for neighbor, edata in edges.items():
            rel = edata.get("relationship", "")
            if rel == "VALIDATES_PRODUCT" and neighbor in product_set:
                score += 3
            elif rel == "SUPPORTS_PAIN" and neighbor in pain_ids:
                score += 2
            elif rel == "RESONATES_WITH" and neighbor in persona_set:
                score += 1
        if score >= 2 and any(name in ppdata.get("claim", "") for name in _NAMED_CUSTOMERS):
            score += 1
        return score

    def _include(pp_id: str) -> bool:
        edges  = G[pp_id] if pp_id in G else {}
        ppdata = G.nodes[pp_id].get("data", {})
        claim  = ppdata.get("claim", "")
        is_named = any(name in claim for name in _NAMED_CUSTOMERS)
        if is_named:
            return any(
                (edata.get("relationship") == "VALIDATES_PRODUCT" and neighbor in product_set)
                or (edata.get("relationship") == "SUPPORTS_PAIN" and neighbor in pain_ids)
                for neighbor, edata in edges.items()
            )
        # Non-named: must have at least one product/pain edge AND at least one persona edge.
        # Persona resonance alone is not sufficient — the proof point could belong to a
        # different product that targets the same persona.
        _has_prod_or_pain = any(
            (edata.get("relationship") == "VALIDATES_PRODUCT" and neighbor in product_set)
            or (edata.get("relationship") == "SUPPORTS_PAIN" and neighbor in pain_ids)
            for neighbor, edata in edges.items()
        )
        _has_persona = any(
            edata.get("relationship") == "RESONATES_WITH" and neighbor in persona_set
            for neighbor, edata in edges.items()
        )
        return _has_prod_or_pain and _has_persona

    all_pp_ids      = [nid for nid, a in G.nodes(data=True) if a.get("type") == "ProofPoint"]
    filtered_pp_ids = [pid for pid in all_pp_ids if _include(pid)]
    ranked_pp_ids   = sorted(filtered_pp_ids, key=_score, reverse=True)[:6]

    # ── Step 6: Assemble output dict ──────────────────────────────────────────
    def _data(nid): return G.nodes[nid].get("data", {})

    return {
        "matched_products":  [_data(pid) for pid in product_ids],
        "matched_personas":  [_data(pid) for pid in persona_ids],
        "matched_pains":     [_data(pid) for pid in pain_ids],
        "matched_channels":  [_data(cid) for cid in channel_ids],
        "proof_points":      [_data(pid) for pid in ranked_pp_ids],
        "brand_voice":       G.graph.get("brand_voice", {}),
        "company_context":   G.graph.get("company_context", {}),
        "meta":              G.graph.get("meta", {}),
        # Graph metadata surfaced for the UI visualization
        "_matched_node_ids": {
            "products":     list(product_ids),
            "personas":     list(persona_ids),
            "pains":        list(pain_ids),
            "channels":     list(channel_ids),
            "proof_points": list(ranked_pp_ids),
        },
        "persona_from_brief": bool(_layer1_persona_ids),
    }
