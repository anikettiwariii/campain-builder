"""
Neo4j data loader — populates the graph from marketing_brain.json.

Run directly:  python -m knowledge.neo4j_loader
Or call:       load_brain_into_neo4j()

Embeddings are generated with transformers (all-MiniLM-L6-v2) using mean
pooling and stored as float-list properties on each node for semantic fallback.
Uses transformers directly to avoid the sentence-transformers → datasets →
huggingface_hub version conflict.
"""
import json
import logging
import os

log = logging.getLogger(__name__)

_ROOT       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BRAIN_PATH = os.path.join(_ROOT, "knowledge", "marketing_brain.json")

# ── Embedding model (lazy-loaded once) ───────────────────────────────────────

_tokenizer   = None
_embed_model = None
_MODEL_NAME  = "sentence-transformers/all-MiniLM-L6-v2"


def _get_embed_model():
    global _tokenizer, _embed_model
    if _embed_model is None:
        from transformers import AutoModel, AutoTokenizer
        _tokenizer   = AutoTokenizer.from_pretrained(_MODEL_NAME)
        _embed_model = AutoModel.from_pretrained(_MODEL_NAME)
        _embed_model.eval()
    return _tokenizer, _embed_model


def _embed(text: str) -> list:
    if not text or not text.strip():
        return []
    import torch
    tokenizer, model = _get_embed_model()
    encoded = tokenizer(text, padding=True, truncation=True,
                        max_length=128, return_tensors="pt")
    with torch.no_grad():
        out = model(**encoded)
    # Mean-pool over token dimension, then L2-normalise
    mask = encoded["attention_mask"].unsqueeze(-1).float()
    pooled = (out.last_hidden_state * mask).sum(1) / mask.sum(1)
    normed = torch.nn.functional.normalize(pooled, p=2, dim=1)
    return normed[0].tolist()


def _product_text(p: dict) -> str:
    parts = [
        p.get("name", ""),
        p.get("tagline", ""),
        p.get("positioning", ""),
        p.get("key_differentiator", ""),
    ]
    return " ".join(x for x in parts if x)


def _pain_text(p: dict) -> str:
    return f"{p.get('label', '')}. {p.get('description', '')}"


def _persona_text(p: dict) -> str:
    parts = [p.get("title", ""), p.get("pain_language", "")]
    return " ".join(x for x in parts if x)


def _channel_text(c: dict) -> str:
    return f"{c.get('name', '')} {c.get('use_case', '')}"


def _pp_text(pp: dict) -> str:
    return pp.get("claim", "")


# ── Constraints (idempotent) ──────────────────────────────────────────────────

_CONSTRAINT_QUERIES = [
    "CREATE CONSTRAINT product_id   IF NOT EXISTS FOR (n:Product)    REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT persona_id   IF NOT EXISTS FOR (n:Persona)    REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT pain_id      IF NOT EXISTS FOR (n:Pain)       REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT channel_id   IF NOT EXISTS FOR (n:Channel)    REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT proofpoint_id IF NOT EXISTS FOR (n:ProofPoint) REQUIRE n.id IS UNIQUE",
]


def _run_constraints(session):
    for q in _CONSTRAINT_QUERIES:
        session.run(q)


# ── Node helpers ──────────────────────────────────────────────────────────────

def _merge_node(session, label: str, props: dict, embedding_text: str, raw: dict = None):
    """MERGE node, set all props, attach embedding and full raw JSON."""
    emb = _embed(embedding_text)
    # Store the complete source dict as _raw so query layer never misses a field
    all_props = dict(props)
    if raw is not None:
        all_props["_raw"] = json.dumps(raw)
    session.run(
        f"""
        MERGE (n:{label} {{id: $id}})
        SET n += $props
        SET n.embedding = $emb
        """,
        id=props["id"],
        props=all_props,
        emb=emb,
    )


def _merge_edge(session, from_id: str, rel: str, to_id: str):
    session.run(
        f"""
        MATCH (a {{id: $from_id}})
        MATCH (b {{id: $to_id}})
        MERGE (a)-[:{rel}]->(b)
        """,
        from_id=from_id,
        to_id=to_id,
    )


# ── Main loader ───────────────────────────────────────────────────────────────

def load_brain_into_neo4j(force_reload: bool = False) -> bool:
    """
    Populate Neo4j from marketing_brain.json.

    Returns True on success, False if Neo4j is unavailable.
    Set force_reload=True to wipe and re-import (useful after editing the JSON).
    """
    from knowledge.neo4j_connection import get_driver
    driver = get_driver()
    if driver is None:
        return False

    with open(_BRAIN_PATH) as f:
        brain = json.load(f)

    with driver.session() as session:
        _run_constraints(session)

        if force_reload:
            session.run("MATCH (n) DETACH DELETE n")
            log.info("Wiped existing Neo4j graph for reload")

        # Check if already loaded (skip embedding re-generation if not forced)
        existing = session.run("MATCH (n:Product) RETURN count(n) AS c").single()["c"]
        if existing > 0 and not force_reload:
            log.info("Neo4j already populated (%d products) — skipping load", existing)
            return True

        log.info("Loading marketing brain into Neo4j…")

        # ── Nodes ─────────────────────────────────────────────────────────────

        for persona in brain.get("personas", []):
            _merge_node(session, "Persona", {
                "id":    persona["id"],
                "title": persona.get("title", ""),
            }, _persona_text(persona), raw=persona)

        for pain in brain.get("pains", []):
            _merge_node(session, "Pain", {
                "id":    pain["id"],
                "label": pain.get("label", ""),
            }, _pain_text(pain), raw=pain)

        for product in brain.get("products", []):
            _merge_node(session, "Product", {
                "id":   product["id"],
                "name": product.get("name", ""),
            }, _product_text(product), raw=product)

        for channel in brain.get("channels", []):
            _merge_node(session, "Channel", {
                "id":   channel["id"],
                "name": channel.get("name", ""),
            }, _channel_text(channel), raw=channel)

        for pp in brain.get("proof_points", []):
            _merge_node(session, "ProofPoint", {
                "id":    pp["id"],
                "claim": pp.get("claim", ""),
            }, _pp_text(pp), raw=pp)

        # ── Edges ──────────────────────────────────────────────────────────────

        for persona in brain.get("personas", []):
            for pain_id in persona.get("pain_ids", []):
                _merge_edge(session, persona["id"], "EXPERIENCES_PAIN", pain_id)

        for product in brain.get("products", []):
            for pain_id in product.get("pain_ids", []):
                _merge_edge(session, product["id"], "SOLVES_PAIN", pain_id)
            for persona_id in product.get("persona_ids", []):
                _merge_edge(session, product["id"], "TARGETS_PERSONA", persona_id)

        for channel in brain.get("channels", []):
            for persona_id in channel.get("best_for_personas", []):
                _merge_edge(session, channel["id"], "REACHES_PERSONA", persona_id)

        for pp in brain.get("proof_points", []):
            for prod_id in pp.get("use_for_products", []):
                _merge_edge(session, pp["id"], "VALIDATES_PRODUCT", prod_id)
            for pain_id in pp.get("use_for_pains", []):
                _merge_edge(session, pp["id"], "SUPPORTS_PAIN", pain_id)
            for persona_id in pp.get("use_for_personas", []):
                _merge_edge(session, pp["id"], "RESONATES_WITH", persona_id)

        counts = session.run("""
            MATCH (n) RETURN labels(n)[0] AS label, count(n) AS c
            ORDER BY label
        """).data()
        edge_count = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]

    log.info(
        "Neo4j load complete — nodes: %s | edges: %d",
        {row["label"]: row["c"] for row in counts},
        edge_count,
    )
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ok = load_brain_into_neo4j(force_reload=True)
    print("Load succeeded:", ok)
