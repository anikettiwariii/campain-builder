"""
Marketing Brain graph visualization using pyvis.

render_brain_html(highlight_ids=None) → str

Returns a self-contained HTML string that can be embedded in Streamlit
via st.components.v1.html(). Highlighted node IDs (from a campaign run)
are rendered larger and brighter; background nodes are dimmed.
"""
import json
import os
import tempfile

from pyvis.network import Network

from knowledge.graph_builder import build_marketing_graph

# ── Visual config ─────────────────────────────────────────────────────────────

_NODE_STYLES = {
    "Product":    {"color": "#7B3D9B", "dim": "#3A1A4A", "size": 30},
    "Persona":    {"color": "#2D6B8B", "dim": "#0F2535", "size": 24},
    "Pain":       {"color": "#8B3D3D", "dim": "#2E1010", "size": 20},
    "Channel":    {"color": "#2D8B5B", "dim": "#0F3020", "size": 18},
    "ProofPoint": {"color": "#7B6B2D", "dim": "#2E260F", "size": 16},
}

_EDGE_COLORS = {
    "SOLVES_PAIN":        "#8B3D3D",
    "TARGETS_PERSONA":    "#2D6B8B",
    "EXPERIENCES_PAIN":   "#5A3D3D",
    "REACHES_PERSONA":    "#2D8B5B",
    "VALIDATES_PRODUCT":  "#7B3D9B",
    "SUPPORTS_PAIN":      "#7B6B2D",
    "RESONATES_WITH":     "#4A4A5A",
}

_PYVIS_OPTIONS = {
    "nodes": {
        "font":   {"color": "#C0C0D0", "size": 14, "face": "IBM Plex Mono, monospace"},
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
        "hover":          True,
        "tooltipDelay":   100,
        "navigationButtons": False,
        "zoomView":       True,
        "dragView":       True,
    },
    "layout": {"improvedLayout": True},
}


def render_brain_html(highlight_ids: dict = None, compact: bool = False) -> str:
    """
    Build and return an interactive pyvis graph as an HTML string.

    highlight_ids: optional dict from ctx["_matched_node_ids"] — those nodes
                   and their immediate edges are rendered at full brightness.
                   All other nodes are dimmed.
    """
    G = build_marketing_graph()

    # Flatten highlighted IDs into a single set
    lit_set = set()
    if highlight_ids:
        for id_list in highlight_ids.values():
            lit_set.update(id_list)

    has_highlight = bool(lit_set)
    # compact=True: hide all nodes that weren't traversed for this campaign
    _show_only = lit_set if (compact and has_highlight) else None

    # Compact view: tight physics so 8-15 nodes fill a 500px canvas without overflow
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

    nt = Network(
        height="500px" if (compact and has_highlight) else "720px",
        width="100%",
        bgcolor="#0A0A0F",
        font_color="#A0A0B0",
        directed=True,
        notebook=False,
    )
    nt.set_options(json.dumps(_opts))

    # ── Add nodes ─────────────────────────────────────────────────────────────
    for nid, attr in G.nodes(data=True):
        if _show_only is not None and nid not in _show_only:
            continue
        ntype = attr.get("type", "ProofPoint")
        style = _NODE_STYLES.get(ntype, _NODE_STYLES["ProofPoint"])
        data  = attr.get("data", {})
        # Compact mode shows full ProofPoint claim; full graph uses pre-truncated label
        label = (data.get("claim") or attr.get("label", nid)
                 if (compact and ntype == "ProofPoint")
                 else attr.get("label", nid))

        highlighted = (not has_highlight) or (nid in lit_set)
        color = style["color"] if highlighted else style["dim"]
        size  = style["size"] if highlighted else max(style["size"] - 8, 8)
        opacity = 1.0 if highlighted else 0.35

        # Build tooltip
        if ntype == "Product":
            tip = f"<b>{data.get('name','')}</b><br>{data.get('positioning','')[:120]}…"
        elif ntype == "Persona":
            tip = f"<b>{data.get('title','')}</b><br>{data.get('company_size','')}"
        elif ntype == "Pain":
            tip = f"<b>{data.get('label','')}</b><br>{data.get('description','')[:100]}…"
        elif ntype == "Channel":
            tip = f"<b>{data.get('name','')}</b><br>{data.get('use_case','')[:100]}…"
        else:
            tip = f"{data.get('claim','')[:140]}…<br><i>{data.get('source','')}</i>"

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

    # ── Add edges ─────────────────────────────────────────────────────────────
    for u, v, edata in G.edges(data=True):
        if _show_only is not None and (u not in _show_only or v not in _show_only):
            continue
        rel = edata.get("relationship", "")
        base_color = _EDGE_COLORS.get(rel, "#3A3A4A")

        edge_lit = (not has_highlight) or (u in lit_set and v in lit_set)
        color   = base_color if edge_lit else "#1A1A22"
        width   = 1.5 if edge_lit else 0.5
        label   = rel.replace("_", " ").lower() if edge_lit else ""

        nt.add_edge(u, v, color=color, width=width, title=rel, label=label)

    # Generate to a temp file then read the HTML back
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w") as f:
        tmp_path = f.name
    try:
        nt.save_graph(tmp_path)
        with open(tmp_path) as f:
            html = f.read()
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    # Inject dark body background so iframe matches the app theme
    html = html.replace(
        "<body>",
        '<body style="background:#0A0A0F;margin:0;padding:0;">',
    )
    # In compact mode: freeze physics after stabilization so nodes don't drift,
    # then fit the viewport so every node is visible within the canvas bounds.
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


def render_brain_png(highlight_ids: dict = None, compact: bool = False) -> bytes:
    """Deterministic matplotlib PNG (1200×800, black bg).

    compact=True: only activated nodes.  compact=False: all 34 nodes.
    Returns raw PNG bytes suitable for st.image().
    """
    import io
    import textwrap
    import networkx as nx
    import matplotlib.pyplot as plt
    plt.switch_backend("Agg")

    G = build_marketing_graph()

    lit_set: set = set()
    if highlight_ids:
        for ids in highlight_ids.values():
            lit_set.update(ids)
    has_highlight = bool(lit_set)

    G_sub = G.subgraph(lit_set).copy() if (compact and has_highlight) else G

    k = 2.0 if (compact and has_highlight) else 1.2
    pos = nx.spring_layout(G_sub, seed=42, k=k, iterations=120)

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

    node_list = list(G_sub.nodes())
    colors, sizes, labels = [], [], {}

    for nid in node_list:
        attr  = G_sub.nodes[nid]
        ntype = attr.get("type", "ProofPoint")
        data  = attr.get("data", {})
        colors.append(_C.get(ntype, "#7B6B2D"))
        sizes.append(_S.get(ntype, 1500))

        if ntype == "ProofPoint":
            raw = data.get("claim") or attr.get("label", nid)
            labels[nid] = textwrap.fill(raw, 32)
        elif ntype == "Product":
            labels[nid] = data.get("name") or attr.get("label", nid)
        elif ntype == "Persona":
            labels[nid] = data.get("title") or attr.get("label", nid)
        elif ntype == "Pain":
            labels[nid] = data.get("label") or attr.get("label", nid)
        elif ntype == "Channel":
            labels[nid] = data.get("name") or attr.get("label", nid)
        else:
            labels[nid] = attr.get("label", nid)

    fig, ax = plt.subplots(figsize=(12, 8), dpi=100)
    fig.patch.set_facecolor("#0A0A0F")
    ax.set_facecolor("#0A0A0F")
    ax.axis("off")

    nx.draw_networkx_edges(
        G_sub, pos, ax=ax,
        edge_color="#3A3A5A", arrows=True, arrowsize=15,
        width=0.9, alpha=0.6, node_size=2500,
    )
    nx.draw_networkx_nodes(
        G_sub, pos, ax=ax,
        nodelist=node_list, node_color=colors, node_size=sizes, alpha=0.92,
    )
    nx.draw_networkx_labels(
        G_sub, pos, labels=labels, ax=ax,
        font_size=9, font_color="white", font_family="monospace",
    )
    ax.text(
        0.99, 0.99, "NetworkX in-memory",
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


def graph_stats() -> dict:
    """Return node and edge counts by type — for the legend card."""
    G = build_marketing_graph()
    counts = {}
    for _, attr in G.nodes(data=True):
        t = attr.get("type", "Unknown")
        counts[t] = counts.get(t, 0) + 1
    rel_counts = {}
    for _, _, edata in G.edges(data=True):
        r = edata.get("relationship", "Unknown")
        rel_counts[r] = rel_counts.get(r, 0) + 1

    # Proof gaps: products with no named customer case study VALIDATES_PRODUCT edge.
    # A named case study has source containing "case study" in its data dict.
    named_pp_ids = {
        n for n, a in G.nodes(data=True)
        if a.get("type") == "ProofPoint"
        and "case study" in a.get("data", {}).get("source", "").lower()
    }
    products_with_named_case = {
        v for u, v, e in G.edges(data=True)
        if e.get("relationship") == "VALIDATES_PRODUCT" and u in named_pp_ids
    }
    all_products = {n for n, a in G.nodes(data=True) if a.get("type") == "Product"}
    proof_gaps = len(all_products - products_with_named_case)

    return {
        "nodes":        counts,
        "relationships": rel_counts,
        "total_nodes":  G.number_of_nodes(),
        "total_edges":  G.number_of_edges(),
        "proof_gaps":   proof_gaps,
    }
