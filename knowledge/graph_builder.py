"""
Marketing Knowledge Graph — builds a Graph from marketing_brain.json.

Nodes  : Product, Persona, Pain, Channel, ProofPoint
Edges  : SOLVES_PAIN, TARGETS_PERSONA, REACHES_PERSONA, EXPERIENCES_PAIN,
         VALIDATES_PRODUCT, SUPPORTS_PAIN, RESONATES_WITH

The full raw JSON object is stored as node["data"] so the query layer
can return it in the same format as the original load_knowledge_graph().
"""
import json
import os

import networkx as nx

_ROOT       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BRAIN_PATH = os.path.join(_ROOT, "knowledge", "marketing_brain.json")

_GRAPH: nx.DiGraph = None  # module-level singleton


def build_marketing_graph(force_rebuild: bool = False) -> nx.DiGraph:
    """Return the cached marketing knowledge graph, building it on first call."""
    global _GRAPH
    if _GRAPH is not None and not force_rebuild:
        return _GRAPH

    with open(_BRAIN_PATH) as f:
        brain = json.load(f)

    G = nx.DiGraph()

    # Stash top-level brain metadata as graph attributes
    G.graph["brand_voice"]     = brain.get("brand_voice", {})
    G.graph["company_context"] = brain.get("company_context", {})
    G.graph["meta"]            = brain.get("meta", {})

    # ── Persona nodes ─────────────────────────────────────────────────────────
    for persona in brain.get("personas", []):
        G.add_node(
            persona["id"],
            type  = "Persona",
            label = persona["title"],
            data  = persona,
        )

    # ── Pain nodes ────────────────────────────────────────────────────────────
    for pain in brain.get("pains", []):
        G.add_node(
            pain["id"],
            type  = "Pain",
            label = pain["label"],
            data  = pain,
        )

    # ── Product nodes ─────────────────────────────────────────────────────────
    for product in brain.get("products", []):
        G.add_node(
            product["id"],
            type  = "Product",
            label = product["name"],
            data  = product,
        )

    # ── Channel nodes ─────────────────────────────────────────────────────────
    for channel in brain.get("channels", []):
        G.add_node(
            channel["id"],
            type  = "Channel",
            label = channel["name"],
            data  = channel,
        )

    # ── ProofPoint nodes ──────────────────────────────────────────────────────
    for pp in brain.get("proof_points", []):
        G.add_node(
            pp["id"],
            type  = "ProofPoint",
            label = pp["claim"][:45] + "…" if len(pp["claim"]) > 45 else pp["claim"],
            data  = pp,
        )

    # ── Edges ─────────────────────────────────────────────────────────────────

    # Persona → EXPERIENCES_PAIN → Pain
    for persona in brain.get("personas", []):
        for pain_id in persona.get("pain_ids", []):
            if G.has_node(pain_id):
                G.add_edge(persona["id"], pain_id, relationship="EXPERIENCES_PAIN")

    # Product → SOLVES_PAIN → Pain
    for product in brain.get("products", []):
        for pain_id in product.get("pain_ids", []):
            if G.has_node(pain_id):
                G.add_edge(product["id"], pain_id, relationship="SOLVES_PAIN")

    # Product → TARGETS_PERSONA → Persona
    for product in brain.get("products", []):
        for persona_id in product.get("persona_ids", []):
            if G.has_node(persona_id):
                G.add_edge(product["id"], persona_id, relationship="TARGETS_PERSONA")

    # Channel → REACHES_PERSONA → Persona
    for channel in brain.get("channels", []):
        for persona_id in channel.get("best_for_personas", []):
            if G.has_node(persona_id):
                G.add_edge(channel["id"], persona_id, relationship="REACHES_PERSONA")

    # ProofPoint → VALIDATES_PRODUCT → Product
    for pp in brain.get("proof_points", []):
        for prod_id in pp.get("use_for_products", []):
            if G.has_node(prod_id):
                G.add_edge(pp["id"], prod_id, relationship="VALIDATES_PRODUCT")

    # ProofPoint → SUPPORTS_PAIN → Pain
    for pp in brain.get("proof_points", []):
        for pain_id in pp.get("use_for_pains", []):
            if G.has_node(pain_id):
                G.add_edge(pp["id"], pain_id, relationship="SUPPORTS_PAIN")

    # ProofPoint → RESONATES_WITH → Persona
    for pp in brain.get("proof_points", []):
        for persona_id in pp.get("use_for_personas", []):
            if G.has_node(persona_id):
                G.add_edge(pp["id"], persona_id, relationship="RESONATES_WITH")

    _GRAPH = G
    return G


def get_raw_brain() -> dict:
    """Return the raw marketing_brain.json dict."""
    with open(_BRAIN_PATH) as f:
        return json.load(f)
