"""
Neo4j graph visualization.

render_brain_html_neo4j(highlight_ids=None) → str

Queries Neo4j for all nodes and edges, renders with pyvis in the same
dark theme as the NetworkX version. Adds a "Neo4j — live graph query"
label so the data source is visible in the demo.
"""
import json
import logging
import os
import tempfile

log = logging.getLogger(__name__)

# ── Visual config (same as graph_viz.py) ─────────────────────────────────────

_NODE_STYLES = {
    "Product":    {"color": "#7B3D9B", "dim": "#3A1A4A", "size": 30},
    "Persona":    {"color": "#2D6B8B", "dim": "#0F2535", "size": 24},
    "Pain":       {"color": "#8B3D3D", "dim": "#2E1010", "size": 20},
    "Channel":    {"color": "#2D8B5B", "dim": "#0F3020", "size": 18},
    "ProofPoint": {"color": "#7B6B2D", "dim": "#2E260F", "size": 16},
}

_EDGE_COLORS = {
    "SOLVES_PAIN":       "#8B3D3D",
    "TARGETS_PERSONA":   "#2D6B8B",
    "EXPERIENCES_PAIN":  "#5A3D3D",
    "REACHES_PERSONA":   "#2D8B5B",
    "VALIDATES_PRODUCT": "#7B3D9B",
    "SUPPORTS_PAIN":     "#7B6B2D",
    "RESONATES_WITH":    "#4A4A5A",
}

_PYVIS_OPTIONS = {
    "nodes": {
        "font": {"color": "#C0C0D0", "size": 14, "face": "IBM Plex Mono, monospace"},
        "borderWidth": 1,
        "borderWidthSelected": 2,
    },
    "edges": {
        "arrows":  {"to": {"enabled": True, "scaleFactor": 0.5}},
        "smooth":  {"type": "curvedCW", "roundness": 0.15},
        "width":   1,
        "font":    {"color": "#505060", "size": 9, "strokeWidth": 0, "align": "middle"},
    },
    "physics": {
        "barnesHut": {
            "gravitationalConstant": -25000,
            "centralGravity":        0.1,
            "springLength":          280,
            "springConstant":        0.04,
            "damping":               0.09,
            "avoidOverlap":          1.0,
        },
        "stabilization": {"iterations": 300},
    },
    "interaction": {
        "hover":             True,
        "tooltipDelay":      100,
        "navigationButtons": False,
        "zoomView":          True,
        "dragView":          True,
    },
    "layout": {"improvedLayout": True},
}

# Badge injected into the HTML so it's visible in the demo
_NEO4J_BADGE_HTML = (
    '<div id="neo4j-badge" style="position:fixed;top:10px;right:14px;z-index:9999;'
    'background:#1A1A28;border:1px solid #6B2D8B;border-radius:4px;'
    'padding:4px 10px;font-family:IBM Plex Mono,monospace;'
    'font-size:0.62rem;color:#9B5DBB;letter-spacing:0.08em;">'
    'Neo4j: live graph query</div>'
)


def render_brain_html_neo4j(highlight_ids: dict = None, compact: bool = False) -> str:
    """
    Build interactive pyvis graph from Neo4j and return HTML string.

    highlight_ids: optional dict from ctx["_matched_node_ids"] — same format
                   as the NetworkX version.
    """
    from pyvis.network import Network

    from knowledge.neo4j_connection import get_driver
    driver = get_driver()
    if driver is None:
        raise RuntimeError("Neo4j unavailable")

    lit_set = set()
    if highlight_ids:
        for id_list in highlight_ids.values():
            lit_set.update(id_list)
    has_highlight = bool(lit_set)
    _show_only = lit_set if (compact and has_highlight) else None

    if compact and has_highlight:
        _opts = {**_PYVIS_OPTIONS, "physics": {
            "barnesHut": {
                "gravitationalConstant": -8000,
                "centralGravity":        0.4,
                "springLength":          120,
                "springConstant":        0.05,
                "damping":               0.15,
                "avoidOverlap":          1.0,
            },
            "stabilization": {"enabled": True, "iterations": 250, "fit": True},
        }}
    else:
        _opts = _PYVIS_OPTIONS

    with driver.session() as session:
        node_rows = session.run(
            "MATCH (n) RETURN n.id AS id, labels(n)[0] AS label, "
            "n.name AS name, n.title AS title, n.label AS nlabel, "
            "n.claim AS claim, n.source AS source, "
            "n.positioning AS positioning, n.company_size AS company_size, "
            "n.description AS description, n.use_case AS use_case"
        ).data()

        edge_rows = session.run(
            "MATCH (a)-[r]->(b) RETURN a.id AS from_id, type(r) AS rel, b.id AS to_id"
        ).data()

    nt = Network(
        height="500px" if (compact and has_highlight) else "720px",
        width="100%",
        bgcolor="#0A0A0F",
        font_color="#A0A0B0",
        directed=True,
        notebook=False,
    )
    nt.set_options(json.dumps(_opts))

    for row in node_rows:
        nid   = row["id"]
        if _show_only is not None and nid not in _show_only:
            continue
        ntype = row["label"]
        style = _NODE_STYLES.get(ntype, _NODE_STYLES["ProofPoint"])

        # Compact mode: full ProofPoint claim, no truncation
        if compact and ntype == "ProofPoint":
            label = row.get("claim") or nid
        else:
            label = (
                row.get("name")
                or row.get("title")
                or row.get("nlabel")
                or (row.get("claim") or "")[:40] + "…"
                or nid
            )

        # Tooltip
        if ntype == "Product":
            tip = f"<b>{label}</b><br>{(row.get('positioning') or '')[:120]}…"
        elif ntype == "Persona":
            tip = f"<b>{label}</b><br>{row.get('company_size') or ''}"
        elif ntype == "Pain":
            tip = f"<b>{label}</b><br>{(row.get('description') or '')[:100]}…"
        elif ntype == "Channel":
            tip = f"<b>{label}</b><br>{(row.get('use_case') or '')[:100]}…"
        else:
            tip = f"{(row.get('claim') or '')[:140]}…<br><i>{row.get('source') or ''}</i>"

        highlighted = (not has_highlight) or (nid in lit_set)
        color = style["color"] if highlighted else style["dim"]
        size  = style["size"] if highlighted else max(style["size"] - 8, 8)

        nt.add_node(
            nid,
            label=label,
            title=tip,
            color={"background": color, "border": color,
                   "highlight": {"background": style["color"], "border": "#FFFFFF"}},
            size=size,
            font={"color": "#FFFFFF" if highlighted else "#606070",
                  "size": 14 if highlighted else 11},
            shape="dot",
            borderWidth=2 if highlighted and nid in lit_set else 1,
        )

    for row in edge_rows:
        if _show_only is not None and (row["from_id"] not in _show_only or row["to_id"] not in _show_only):
            continue
        rel       = row["rel"]
        base_color = _EDGE_COLORS.get(rel, "#3A3A4A")
        edge_lit   = (not has_highlight) or (row["from_id"] in lit_set and row["to_id"] in lit_set)
        nt.add_edge(
            row["from_id"], row["to_id"],
            color=base_color if edge_lit else "#1A1A22",
            width=1.5 if edge_lit else 0.5,
            title=rel,
            label=rel.replace("_", " ").lower() if edge_lit else "",
        )

    with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w") as f:
        tmp_path = f.name
    try:
        nt.save_graph(tmp_path)
        with open(tmp_path) as f:
            html = f.read()
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    html = html.replace(
        "<body>",
        f'<body style="background:#0A0A0F;margin:0;padding:0;">{_NEO4J_BADGE_HTML}',
    )
    if compact and has_highlight:
        html = html.replace(
            "</body>",
            '<script>'
            '(function freeze(){'
            '  if(typeof network!=="undefined"){'
            '    network.once("stabilizationIterationsDone",function(){'
            '      network.setOptions({physics:false});'
            '      network.fit({animation:false});'
            '    });'
            '  } else { setTimeout(freeze,50); }'
            '})();'
            '</script></body>',
        )
    return html


def render_brain_png_neo4j(highlight_ids: dict = None, compact: bool = False) -> bytes:
    """Deterministic matplotlib PNG (1200×800, black bg) from Neo4j data.

    compact=True: only activated nodes.  compact=False: all nodes.
    Returns raw PNG bytes suitable for st.image().
    """
    import io
    import textwrap
    import networkx as nx
    import matplotlib.pyplot as plt
    plt.switch_backend("Agg")

    driver = get_driver()
    if driver is None:
        raise RuntimeError("Neo4j unavailable")

    lit_set: set = set()
    if highlight_ids:
        for ids in highlight_ids.values():
            lit_set.update(ids)
    has_highlight = bool(lit_set)

    with driver.session() as session:
        node_rows = session.run(
            "MATCH (n) RETURN n.id AS id, labels(n)[0] AS ntype, "
            "n.name AS name, n.title AS title, n.label AS nlabel, n.claim AS claim"
        ).data()
        edge_rows = session.run(
            "MATCH (a)-[r]->(b) RETURN a.id AS src, b.id AS dst, type(r) AS rel"
        ).data()

    if compact and has_highlight:
        node_rows = [r for r in node_rows if r["id"] in lit_set]
        edge_rows = [r for r in edge_rows
                     if r["src"] in lit_set and r["dst"] in lit_set]

    G_tmp = nx.DiGraph()
    for row in node_rows:
        G_tmp.add_node(row["id"],
                       ntype=row.get("ntype") or "ProofPoint",
                       name=row.get("name") or "",
                       title=row.get("title") or "",
                       nlabel=row.get("nlabel") or "",
                       claim=row.get("claim") or "")
    for row in edge_rows:
        if G_tmp.has_node(row["src"]) and G_tmp.has_node(row["dst"]):
            G_tmp.add_edge(row["src"], row["dst"], rel=row["rel"])

    _C = {
        "Product":    "#7B3D9B",
        "Persona":    "#2D6B8B",
        "Pain":       "#8B3D3D",
        "Channel":    "#2D8B5B",
        "ProofPoint": "#7B6B2D",
    }
    _S = {
        "Product":    2800,
        "Persona":    2000,
        "Pain":       1800,
        "Channel":    1800,
        "ProofPoint": 1500,
    }

    node_list = list(G_tmp.nodes())
    colors, sizes, labels = [], [], {}

    for nid in node_list:
        attr  = G_tmp.nodes[nid]
        ntype = attr.get("ntype", "ProofPoint")
        colors.append(_C.get(ntype, "#7B6B2D"))
        sizes.append(_S.get(ntype, 1500))

        if ntype == "ProofPoint":
            raw = attr.get("claim") or nid
            labels[nid] = textwrap.fill(raw, 32)
        elif ntype == "Product":
            labels[nid] = attr.get("name") or nid
        elif ntype == "Persona":
            labels[nid] = attr.get("title") or nid
        elif ntype == "Pain":
            labels[nid] = attr.get("nlabel") or nid
        elif ntype == "Channel":
            labels[nid] = attr.get("name") or nid
        else:
            labels[nid] = nid

    k = 2.0 if (compact and has_highlight) else 1.2
    pos = nx.spring_layout(G_tmp, seed=42, k=k, iterations=120)

    fig, ax = plt.subplots(figsize=(12, 8), dpi=100)
    fig.patch.set_facecolor("#0A0A0F")
    ax.set_facecolor("#0A0A0F")
    ax.axis("off")

    nx.draw_networkx_edges(
        G_tmp, pos, ax=ax,
        edge_color="#3A3A5A", arrows=True, arrowsize=15,
        width=0.9, alpha=0.6, node_size=2500,
    )
    nx.draw_networkx_nodes(
        G_tmp, pos, ax=ax,
        nodelist=node_list, node_color=colors, node_size=sizes, alpha=0.92,
    )
    nx.draw_networkx_labels(
        G_tmp, pos, labels=labels, ax=ax,
        font_size=9, font_color="white", font_family="monospace",
    )
    ax.text(
        0.99, 0.99, "Neo4j: live graph query",
        transform=ax.transAxes, fontsize=7, color="#9B5DBB",
        ha="right", va="top", fontfamily="monospace",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="#1A1A28",
                  edgecolor="#6B2D8B", alpha=0.95),
    )

    fig.tight_layout(pad=0.3)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight",
                facecolor="#0A0A0F", edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def graph_stats_neo4j() -> dict:
    """Node and edge counts from Neo4j — same shape as graph_viz.graph_stats()."""
    from knowledge.neo4j_connection import get_driver
    driver = get_driver()
    if driver is None:
        raise RuntimeError("Neo4j unavailable")

    with driver.session() as session:
        node_rows = session.run(
            "MATCH (n) RETURN labels(n)[0] AS label, count(n) AS c ORDER BY label"
        ).data()
        rel_rows = session.run(
            "MATCH ()-[r]->() RETURN type(r) AS rel, count(r) AS c ORDER BY rel"
        ).data()
        total_nodes = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        total_edges = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]

    with driver.session() as session:
        gap_rows = session.run(
            """
            MATCH (p:Product)
            WHERE NOT EXISTS {
                MATCH (pp:ProofPoint)-[:VALIDATES_PRODUCT]->(p)
                WHERE toLower(pp.source) CONTAINS 'case study'
            }
            RETURN count(p) AS c
            """
        ).single()
    proof_gaps = gap_rows["c"] if gap_rows else 0

    return {
        "nodes":        {r["label"]: r["c"] for r in node_rows},
        "relationships":{r["rel"]:   r["c"] for r in rel_rows},
        "total_nodes":  total_nodes,
        "total_edges":  total_edges,
        "proof_gaps":   proof_gaps,
    }
