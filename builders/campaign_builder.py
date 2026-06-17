import anthropic
import json
import re
import os
import time

client = anthropic.Anthropic()
MODEL  = "claude-sonnet-4-6"
HAIKU  = "claude-haiku-4-5-20251001"

# Populated by each pipeline step — read by app.py for the timing display
PIPELINE_TIMINGS: dict = {}

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Ordered longest-first so "Demand Generation" matches before "Demand Gen",
# and "SDR/BDR" matches before bare "SDR".
_TASK_OWNER_PREFIXES = [
    ("Demand Generation", "Demand Gen"),
    ("Demand Gen",        "Demand Gen"),
    ("Product Marketing", "PMM"),
    ("Marketing Ops",     "Marketing Ops"),
    ("Customer Marketing","Customer Marketing"),
    ("Customer Success",  "Customer Success"),
    ("Field Marketing",   "Field Marketing"),
    ("Paid Social",       "Paid Social"),
    ("SDR/BDR",           "SDR/BDR"),
    ("RevOps",            "RevOps"),
    ("PMM",               "PMM"),
    ("SDR",               "SDR/BDR"),
    ("BDR",               "SDR/BDR"),
]


_ALWAYS_ALLOWED_TASK_OWNERS = {"Marketing Ops", "RevOps", "Customer Marketing"}

# Channel categories that may appear in task text → the signal words that identify them.
# If none of a category's signals appear in the locked asset plan text, tasks containing
# those signals are dropped by _enforce_task_asset_alignment.
_OFF_PLAN_CHANNEL_SIGNALS = [
    ("outbound",    ["outbound", "bdr outreach", "sdr outreach", "cold outreach", "qualified ai outbound"]),
    ("webinar",     ["webinar", "virtual event", "on-demand webinar"]),
    ("paid social", ["linkedin ads", "paid social", "paid linkedin", "sponsored post"]),
    ("paid search", ["google ads", "paid search", "sem campaign"]),
]


def _normalize_owner(raw: str) -> str:
    for prefix, canonical in _TASK_OWNER_PREFIXES:
        if re.match(rf'^{re.escape(prefix)}\b', raw, re.IGNORECASE):
            return canonical
    return raw


def _correct_task_owners(rollout: dict) -> dict:
    """If a task description starts with a team name, sync the owner field to match."""
    for phase in rollout.get("phases", []):
        for task in phase.get("tasks", []):
            text = task.get("task", "")
            for prefix, canonical in _TASK_OWNER_PREFIXES:
                if re.match(rf'^{re.escape(prefix)}\b', text, re.IGNORECASE):
                    task["owner"] = canonical
                    break
    return rollout


# ── Product name scrubber ─────────────────────────────────────────────────────
# Distinctive capitalized product name variants, ordered longest-first per product
# so the regex sub replaces the longest match first.  Generic lowercase aliases
# ("content creation", "AI agents") are intentionally omitted to avoid false positives.
_PRODUCT_SCRUB_NAMES: dict[str, list[str]] = {
    "agenthub":            ["Docebo AgentHub", "AgentHub"],
    "skills_intelligence": ["Docebo Skills Intelligence", "Skills Intelligence"],
    "enterprise_knowledge":["Docebo Enterprise Knowledge", "Enterprise Knowledge"],
    "advanced_analytics":  ["Docebo Advanced Analytics", "Advanced Analytics"],
    "headless_learning":   ["Docebo Headless Learning", "Headless Learning", "Headless LMS"],
    "roleplay":            ["Docebo Roleplay", "Roleplay"],
    "content_creator":     ["Docebo Content Creator", "Content Creator"],
    "harmony_ai":          ["Docebo Harmony AI", "Harmony AI", "Harmony"],
}

_PRODUCT_SHORT_NAMES: dict[str, str] = {
    "agenthub":            "AgentHub",
    "skills_intelligence": "Skills Intelligence",
    "enterprise_knowledge":"Enterprise Knowledge",
    "advanced_analytics":  "Advanced Analytics",
    "headless_learning":   "Headless Learning",
    "roleplay":            "Roleplay",
    "content_creator":     "Content Creator",
    "harmony_ai":          "Harmony AI",
}


def _scrub_product_names(rollout: dict, ctx: dict) -> dict:
    """Strip any non-matched Docebo product name from task descriptions and milestones.

    Replaces the leaked name with the short name of the matched product so the
    description stays coherent rather than just deleting the word.
    """
    matched_ids  = (ctx.get("_matched_node_ids") or {}).get("products") or []
    if not matched_ids:
        return rollout
    matched_id   = matched_ids[0]
    correct_name = _PRODUCT_SHORT_NAMES.get(matched_id)
    if not correct_name:
        return rollout

    # Build compiled patterns for every OTHER product's distinctive name variants.
    # Longest variants first so "Docebo AgentHub" replaces before "AgentHub".
    patterns: list[tuple] = []
    for prod_id, variants in _PRODUCT_SCRUB_NAMES.items():
        if prod_id == matched_id:
            continue
        for variant in sorted(variants, key=len, reverse=True):
            patterns.append((re.compile(re.escape(variant), re.IGNORECASE), correct_name))

    if not patterns:
        return rollout

    def _clean(text: str) -> str:
        for pat, replacement in patterns:
            text = pat.sub(replacement, text)
        return text

    for phase in rollout.get("phases", []):
        if "milestone" in phase:
            phase["milestone"] = _clean(phase["milestone"])
        for task in phase.get("tasks", []):
            if "task" in task:
                task["task"] = _clean(task["task"])

    return rollout


# ── Channel → asset lock map ──────────────────────────────────────────────────
# Structural source of truth: which asset type, format, and owner each activated
# channel implies. Claude fills `purpose` only. Python enforces the rest.
_CHANNEL_ASSET_MAP: dict[str, dict] = {
    "hubspot_email":            {"asset_type": "Email nurture sequence",  "format": "5-touch HubSpot sequence",        "owner": "Demand Gen"},
    "linkedin_sponsored":       {"asset_type": "LinkedIn ad copy",         "format": "3 LinkedIn sponsored variants",    "owner": "Paid Social"},
    "in_product":               {"asset_type": "In-product banner",        "format": "In-app modal, 2 variants",         "owner": "Product Marketing"},
    "webinar":                  {"asset_type": "Webinar deck",             "format": "Live webinar slide deck",          "owner": "Field Marketing"},
    "qualified_outbound":       {"asset_type": "Outbound sequence",        "format": "3-touch SDR/BDR sequence",         "owner": "SDR/BDR"},
    "customer_success_outreach":{"asset_type": "CSM leave-behind",         "format": "1-page PDF leave-behind",          "owner": "Customer Success"},
}


def _build_locked_assets(ctx: dict) -> list:
    """Return the deterministic asset list for this brief's activated channels.

    One asset per channel, ordered by channel order (graph-determined). Owner
    de-duplication is applied to honour the no-duplicate-owner rule structurally,
    not by LLM inference.
    """
    seen_owners: set = set()
    assets: list = []
    for ch in ctx.get("matched_channels", []):
        defn = _CHANNEL_ASSET_MAP.get(ch.get("id", ""))
        if not defn:
            continue
        if defn["owner"] in seen_owners:
            continue
        seen_owners.add(defn["owner"])
        assets.append({**defn, "purpose": ""})
    return assets


def _enforce_locked_assets(messaging: dict, locked: list) -> None:
    """Overwrite asset_type / format / owner from locked list; keep Claude's purpose text."""
    if not locked:
        return
    llm_plan = messaging.get("asset_plan", [])
    # Build purpose lookup: try position first, then fuzzy asset_type match
    purposes: dict[str, str] = {}
    for i, asset in enumerate(llm_plan):
        at  = asset.get("asset_type", "").lower()
        pur = asset.get("purpose", "")
        if pur:
            purposes[str(i)] = pur
            purposes[at]     = pur
    result = []
    for i, defn in enumerate(locked):
        at  = defn["asset_type"].lower()
        pur = purposes.get(str(i)) or purposes.get(at) or ""
        result.append({
            "asset_type": defn["asset_type"],
            "format":     defn["format"],
            "owner":      defn["owner"],
            "purpose":    pur,
        })
    messaging["asset_plan"] = result


_PLACEHOLDER_PREFIXES = ("[PROOF POINT NEEDED", "[BENCHMARK NEEDED")


def _strip_placeholder_tasks(rollout: dict) -> dict:
    """Remove any rollout task whose description leaked a messaging placeholder.

    Placeholders belong in messaging pillars only. If one appears in a task
    description the LLM fabricated it — drop the whole task so it never
    renders in the UI or reaches Asana.
    """
    for phase in rollout.get("phases", []):
        clean = [
            t for t in phase.get("tasks", [])
            if not any(
                t.get("task", "").startswith(pfx) or pfx in t.get("task", "")
                for pfx in _PLACEHOLDER_PREFIXES
            )
        ]
        phase["tasks"] = clean
    return rollout


def _load_prompt(name: str) -> str:
    p    = os.path.join(_ROOT, "prompts")
    ctx  = open(os.path.join(p, "system_context.txt")).read().strip()
    role = open(os.path.join(p, name)).read().strip()
    return f"{ctx}\n\n{role}"


def parse_json(text: str) -> dict:
    text  = text.strip()
    text  = re.sub(r'^```(?:json)?\s*', '', text)
    text  = re.sub(r'\s*```$', '', text).strip()
    start = text.find('{')
    end   = text.rfind('}')
    if start != -1 and end != -1:
        text = text[start:end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError as _exc:
        # Recovery for truncated responses (e.g. max_tokens reached mid-string).
        # Truncate to error position, strip trailing incomplete fragments,
        # close any open string, then close open braces.
        repair = text[:_exc.pos].rstrip(', \t\n\r')
        depth, in_str, escaped = 0, False, False
        for ch in repair:
            if escaped:
                escaped = False
                continue
            if in_str:
                if ch == '\\':
                    escaped = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
        if in_str:
            repair += '"'
        repair += '}' * max(0, depth)
        return json.loads(repair)


# ── Word-limit enforcement ────────────────────────────────────────────────────

# Words that are incomplete/dangling when they appear at the end of a truncated string.
# Prepositions, articles, coordinating conjunctions, bare auxiliaries, and common
# transitional words that signal an unfinished phrase.
_TRIM_TRAIL: frozenset = frozenset({
    "a", "an", "the",
    "and", "or", "but", "nor", "for", "so", "yet",
    "in", "on", "at", "by", "to", "of", "up", "as",
    "via", "with", "from", "into", "onto", "than",
    "is", "are", "be", "was", "were",
    "if", "when", "while", "after", "before", "during",
})


def _trim_words(text: str, max_words: int) -> str:
    """Trim text to at most max_words at a clean word boundary.

    After the hard cap, trailing function words and bare numbers are stripped so
    the result does not end mid-phrase (e.g. '…prospects in' or '…in 45').
    Never trims below half of max_words to avoid over-stripping short phrases.
    """
    if not text:
        return text
    words = text.split()
    if len(words) <= max_words:
        return text
    words = words[:max_words]
    # Strip trailing function words / bare numbers, but keep at least half the limit.
    min_keep = max(1, max_words // 2)
    while len(words) > min_keep:
        tail = words[-1].lower().rstrip(".,;:!?")
        if tail in _TRIM_TRAIL or re.match(r"^\d+$", tail):
            words.pop()
        else:
            break
    return " ".join(words)


_DOLLAR_RE = re.compile(r'\$\d[\d,]*(?:\.\d+)?')
_LLM_BENCH_RE = re.compile(r'\[BENCHMARK NEEDED[^\]]*\]', re.DOTALL)
_BENCHMARK_SHORT = "Benchmark unavailable: request from Marketing Ops."


def _flag_metric_benchmarks(rollout: dict) -> dict:
    """Replace any invented dollar-amount benchmark in success metrics with a short form."""
    out = []
    for i, m in enumerate(rollout.get("success_metrics", [])):
        if i == 0 or "Benchmark unavailable" in m:
            out.append(m)
        elif _DOLLAR_RE.search(m):
            prefix = _DOLLAR_RE.split(m)[0].strip().rstrip("—,: ").strip()
            out.append(f"{prefix}: {_BENCHMARK_SHORT}" if prefix else _BENCHMARK_SHORT)
        else:
            out.append(m)
    rollout["success_metrics"] = out
    return rollout


def _collapse_benchmark_placeholders(rollout: dict) -> dict:
    """Replace long LLM-generated [BENCHMARK NEEDED...] text with the short canonical form."""
    out = []
    for i, m in enumerate(rollout.get("success_metrics", [])):
        if i == 0 or "Benchmark unavailable" in m:
            out.append(m)
        elif _LLM_BENCH_RE.search(m):
            prefix = _LLM_BENCH_RE.split(m)[0].strip().rstrip("—,: ").strip()
            out.append(f"{prefix}: {_BENCHMARK_SHORT}" if prefix else _BENCHMARK_SHORT)
        else:
            out.append(m)
    rollout["success_metrics"] = out
    return rollout


def _enforce_word_limits(rollout: dict) -> dict:
    """Hard-trim tasks (15w), milestones (10w), and metrics (15w) after LLM generation."""
    for phase in rollout.get("phases", []):
        if "milestone" in phase:
            phase["milestone"] = _trim_words(phase["milestone"], 10)
        for task in phase.get("tasks", []):
            if "task" in task:
                # Strip trailing ", Day N" before trimming — day is rendered separately from due_day
                cleaned = _TASK_DAY_RE.sub("", task["task"]).strip().rstrip(",").strip()
                task["task"] = _trim_words(cleaned, 15)
    rollout["success_metrics"] = [
        m if "Benchmark unavailable" in m else _trim_words(m, 15)
        for m in rollout.get("success_metrics", [])
    ]
    return rollout


# ── Invented-number detection ─────────────────────────────────────────────────

_NUM_RE       = re.compile(r'\b(\d+(?:\.\d+)?)\b')
_PCT_RE       = re.compile(r'(\d+(?:\.\d+)?)\s*%')
_TASK_DAY_RE  = re.compile(r',?\s*Day\s+\d+\s*$', re.IGNORECASE)
_COUNT_BENCH_RE = re.compile(
    r'\d+\+?\s+(?:webinar\s+)?(?:registrant|attendee|sign-?up|signup)s?',
    re.IGNORECASE,
)

_METRIC_TYPE_RES = [
    (re.compile(r'\bopen\b',                     re.I), 'email open rate'),
    (re.compile(r'\bCTR\b|click.through|click.rate', re.I), 'email CTR'),
    (re.compile(r'\battend',                     re.I), 'webinar attendance rate'),
    (re.compile(r'\breply\b|response\s+rate',    re.I), 'reply rate'),
    (re.compile(r'\bconver',                     re.I), 'conversion rate'),
    (re.compile(r'\bregistrant\b|\battendee\b',  re.I), 'registrant target'),
    (re.compile(r'\bsign.?up\b',                 re.I), 'sign-up target'),
]


def _metric_type_label(text: str) -> str:
    for pat, label in _METRIC_TYPE_RES:
        if pat.search(text):
            return label
    return 'benchmark'


def _bench_placeholder(metric_type: str) -> str:
    return _BENCHMARK_SHORT


def _graph_numbers(ctx: dict) -> set:
    """Collect all explicit numbers from verified knowledge graph nodes."""
    nums: set = set()
    for pp in ctx.get("proof_points", []):
        nums.update(_NUM_RE.findall(pp.get("claim", "")))
    for pain in ctx.get("matched_pains", []):
        for field in ("stat", "description"):
            nums.update(_NUM_RE.findall(pain.get(field, "") or ""))
    for prod in ctx.get("matched_products", []):
        for pp_text in prod.get("proof_points", []):
            if isinstance(pp_text, str):
                nums.update(_NUM_RE.findall(pp_text))
    return nums


def _flag_invented_numbers(rollout: dict, ctx: dict) -> dict:
    """Replace invented percentages and count benchmarks in metrics 2-4 with the short form.
    Any % in metrics 2-4 is treated as invented — the graph stores proof claims, not benchmark rates."""
    metrics = rollout.get("success_metrics", [])
    out: list = []
    for i, m_orig in enumerate(metrics):
        if i == 0:
            out.append(m_orig)
            continue
        if "Benchmark unavailable" in m_orig:
            out.append(m_orig)
            continue
        needs_bench = bool(_PCT_RE.search(m_orig)) or bool(_COUNT_BENCH_RE.search(m_orig))
        if needs_bench:
            label = _metric_type_label(m_orig).title()
            out.append(f"{label}: {_BENCHMARK_SHORT}")
        else:
            out.append(m_orig)
    rollout["success_metrics"] = out
    return rollout


# ── Calibration rules ────────────────────────────────────────────────────────

def _enforce_cta_personas(messaging: dict, ctx: dict) -> None:
    """Remove any CTA whose key does not correspond to a graph-matched persona.

    Matching is word-overlap (words ≥ 4 chars) so 'VP of L&D' matches
    'VP of Learning & Development' even if Claude abbreviates the title.
    """
    ctas = messaging.get("cta_by_persona")
    if not ctas or not isinstance(ctas, dict):
        return
    matched = ctx.get("matched_personas") or []
    if not matched:
        return
    allowed_titles = [p.get("title", "").lower() for p in matched]

    def _matches_any(key: str) -> bool:
        kl = key.lower()
        for title in allowed_titles:
            if kl == title or kl in title or title in kl:
                return True
            key_words   = {w for w in kl.split()    if len(w) >= 4}
            title_words = {w for w in title.split() if len(w) >= 4}
            if key_words & title_words:
                return True
        return False

    messaging["cta_by_persona"] = {k: v for k, v in ctas.items() if _matches_any(k)}


def _cap_cta_to_generic(messaging: dict, structure: dict) -> None:
    """Rule 1: If no ICP audience signal in brief, collapse CTAs to one generic entry."""
    icp = (structure.get("icp") or "").strip().lower()
    if icp and icp not in ("not specified", "n/a", "not mentioned", ""):
        return
    ctas = messaging.get("cta_by_persona", {})
    if not ctas:
        return
    first_val = next(iter(ctas.values()), "")
    messaging["cta_by_persona"] = {"General": first_val}


def _has_named_proof(messaging: dict) -> bool:
    """True if any pillar has a real (non-placeholder) proof point."""
    return any(
        not p.get("proof_point", "").startswith("[PROOF POINT NEEDED")
        for p in messaging.get("pillars", [])
    )


def _cap_assets_if_no_named_proof(messaging: dict) -> None:
    """Rule 2: No named-customer proof points → cap asset plan at 3."""
    if not _has_named_proof(messaging):
        messaging["asset_plan"] = messaging.get("asset_plan", [])[:3]


def _enforce_concept_punctuation(messaging: dict) -> None:
    """Strip em-dashes and disallowed punctuation from campaign_concept."""
    concept = messaging.get("campaign_concept")
    if not concept:
        return
    import re
    # Replace em-dash, en-dash, and common substitutes with a comma+space
    cleaned = re.sub(r"[—–‒]", ",", concept)
    # Strip colons, semicolons, parentheses, brackets
    cleaned = re.sub(r"[;:()\[\]]", "", cleaned)
    # Collapse multiple commas or spaces left behind
    cleaned = re.sub(r",\s*,+", ",", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    messaging["campaign_concept"] = cleaned


def _enforce_asset_word_limits(messaging: dict) -> None:
    """Rule 6: Trim asset purpose fields to max 12 words."""
    for asset in messaging.get("asset_plan", []):
        if "purpose" in asset:
            asset["purpose"] = _trim_words(asset["purpose"], 12)


def _cap_phases_if_no_named_proof(rollout: dict, messaging: dict) -> dict:
    """Rule 3: No named-customer proof points → cap rollout at 2 phases."""
    if not _has_named_proof(messaging) and len(rollout.get("phases", [])) > 2:
        rollout["phases"] = rollout["phases"][:2]
    return rollout


def _enforce_asset_task_owners(rollout: dict, messaging: dict) -> dict:
    """Overwrite task owner when task text references a specific asset from the plan.

    Catches cases where the LLM assigns the wrong team to a task that clearly
    belongs to a specific asset type (e.g. in-product banner → PMM, not CS).
    """
    asset_plan = messaging.get("asset_plan", [])

    # Build keyword → canonical owner list, longest keyword first for greedy matching.
    kw_map: list[tuple[str, str]] = []
    for asset in asset_plan:
        owner = asset.get("owner", "")
        if not owner:
            continue
        canonical = _normalize_owner(owner)
        asset_type = asset.get("asset_type", "").lower().strip()
        if asset_type:
            kw_map.append((asset_type, canonical))
            # Rollout prompt tells LLM to write "in-app" instead of "in-product banner"
            if "in-product" in asset_type or "banner" in asset_type:
                kw_map.append(("in-app", canonical))

    kw_map.sort(key=lambda x: len(x[0]), reverse=True)

    for phase in rollout.get("phases", []):
        for task in phase.get("tasks", []):
            task_text = task.get("task", "").lower()
            for kw, owner in kw_map:
                if kw and kw in task_text:
                    task["owner"] = owner
                    break
    return rollout


def _enforce_task_asset_alignment(rollout: dict, messaging: dict) -> dict:
    """Drop tasks that reference channels or asset types not in the locked asset plan.

    Two checks:
    1. Owner filter — task owner must appear in the plan or be always-allowed.
    2. Channel text filter — task description must not reference a channel category
       (outbound, webinar, paid social, etc.) that is absent from the plan's
       asset_type, format, and purpose fields.
    """
    asset_plan = messaging.get("asset_plan", [])

    # ── Owner filter ──────────────────────────────────────────────────────────
    plan_owners = {
        _normalize_owner(a.get("owner", ""))
        for a in asset_plan
        if a.get("owner")
    }
    allowed_owners = plan_owners | _ALWAYS_ALLOWED_TASK_OWNERS

    # ── Channel text filter ───────────────────────────────────────────────────
    # Concatenate all plan asset fields to form the "allowed channel corpus".
    # Owner is included so that an owner's name (e.g. "Paid Social") prevents
    # the channel-signal filter from blocking tasks that start with that owner.
    plan_text = " ".join(
        " ".join([
            a.get("asset_type", ""),
            a.get("format", ""),
            a.get("purpose", ""),
            a.get("owner", ""),
        ]).lower()
        for a in asset_plan
    )

    # Build the set of terms to block in task descriptions: any signal for a
    # channel category that does not appear anywhere in the plan corpus.
    blocked_terms: set = set()
    for _channel, _signals in _OFF_PLAN_CHANNEL_SIGNALS:
        if not any(sig in plan_text for sig in _signals):
            blocked_terms.update(_signals)

    def _task_ok(t: dict) -> bool:
        task_text = t.get("task", "").lower()
        owner = _normalize_owner(t.get("owner", ""))
        if owner not in allowed_owners:
            return False
        if any(sig in task_text for sig in blocked_terms):
            return False
        return True

    for phase in rollout.get("phases", []):
        phase["tasks"] = [t for t in phase.get("tasks", []) if _task_ok(t)]
    return rollout


# ── Knowledge graph retrieval ─────────────────────────────────────────────────

def load_knowledge_graph(brief: str) -> dict:
    """Delegate to the NetworkX graph query layer."""
    from knowledge.graph_query import query_graph
    return query_graph(brief)


def _fmt_ctx(ctx: dict, include: set = None) -> str:
    """Format knowledge graph nodes. Pass include={'personas','pains','products','channels','proof_points'} to filter.
    brand_voice is always injected regardless of the include filter."""
    lines = ["=== KNOWLEDGE GRAPH CONTEXT ==="]

    # Brand voice and company context are never filtered — they apply to every campaign
    bv = ctx.get("brand_voice", {})
    if bv:
        lines.append("\nBRAND VOICE:")
        lines.append(f"  Manifesto: {bv.get('manifesto', '')}")
        lines.append(f"  Tone: {bv.get('tone', '')}")
        lines.append(f"  Enemy: {bv.get('enemy', '')}")
        lines.append(f"  Belief: {bv.get('belief', '')}")
        lines.append(f"  Problem we solve: {bv.get('problem_we_solve', '')}")
        if bv.get("never_say"):
            lines.append(f"  Never say: {', '.join(bv['never_say'])}")
        if bv.get("always_say"):
            lines.append(f"  Always say: {', '.join(bv['always_say'])}")
        if bv.get("kyle_quote"):
            lines.append(f"  CEO quote: \"{bv['kyle_quote']}\"")

    cc = ctx.get("company_context", {})
    if cc:
        lines.append("\nCOMPANY CONTEXT:")
        lines.append(f"  Positioning: {cc.get('positioning', '')}")
        lines.append(f"  Scale: {cc.get('customers', '')} customers, {cc.get('revenue_2025', '')}")
        lines.append(f"  Analyst: {cc.get('analyst_recognition', '')}")
        if cc.get("notable_customers"):
            lines.append(f"  Notable customers: {', '.join(cc['notable_customers'][:6])}")
        if cc.get("brand_pillars"):
            lines.append(f"  Brand pillars: {', '.join(cc['brand_pillars'])}")
        if cc.get("campaign_results"):
            for label, result in cc["campaign_results"].items():
                lines.append(f"  Campaign proof ({label}): {result}")
        if cc.get("product_suite"):
            lines.append(f"  Full product suite: {', '.join(cc['product_suite'])}")

    if include is None or "personas" in include:
        for p in ctx.get("matched_personas", []):
            lines.append(f"\nPERSONA: {p['title']}")  # company_size/team_size are INFERRED — kept out of LLM context
            if p.get("language"):
                lines.append(f"  Language: {', '.join(p['language'][:5])}")
            if p.get("motivations"):
                lines.append(f"  Motivated by: {', '.join(p['motivations'][:3])}")
            if p.get("objections"):
                lines.append(f"  Top objection: {p['objections'][0]}")

    if include is None or "pains" in include:
        for pain in ctx.get("matched_pains", []):
            lines.append(f"\nPAIN [{pain['id']}]: {pain['label']}")
            lines.append(f"  {pain['description']}")
            if pain.get("customer_quotes"):
                lines.append(f"  Quote: \"{pain['customer_quotes'][0]}\"")
            if pain.get("stat"):
                lines.append(f"  Stat: {pain['stat']}")
            if pain.get("docebo_solution"):
                lines.append(f"  Solution: {pain['docebo_solution']}")

    # "products_slim" omits email_examples (used by brief parser, not messaging generator)
    _include_products = (
        include is None or "products" in include or "products_slim" in include
    )
    if _include_products:
        _slim = include is not None and "products_slim" in include and "products" not in include
        for prod in ctx.get("matched_products", []):
            lines.append(f"\nPRODUCT: {prod['name']}")
            lines.append(f"  {prod.get('positioning','')}")
            if prod.get("detect_grow_validate"):
                lines.append(f"  Loop: {prod['detect_grow_validate']}")
            if prod.get("key_differentiator"):
                lines.append(f"  Key diff: {prod['key_differentiator']}")
            for pp in prod.get("proof_points", [])[:3]:
                lines.append(f"  • {pp}")
            if prod.get("ceo_quote"):
                lines.append(f"  CEO: \"{prod['ceo_quote']}\"")
            if prod.get("icp"):
                icp = prod["icp"]
                # company_size and revenue_range are INFERRED firmographic estimates — strip from LLM context.
                # Keep tech_signals, exclude, and industries (qualifying fit signals, not size guesses).
                if icp.get("industries"):
                    lines.append(f"  Target industries: {', '.join(icp['industries'])}")
                if icp.get("tech_signals"):
                    lines.append(f"  Qualifying signals: {'; '.join(icp['tech_signals'])}")
                if icp.get("exclude"):
                    lines.append(f"  Exclude: {'; '.join(icp['exclude'])}")
            if prod.get("email_examples") and not _slim:
                lines.append("  Email examples (ground copy and CTAs in these proven patterns):")
                for persona_id, ex in prod["email_examples"].items():
                    for sl in ex.get("subject_lines", []):
                        lines.append(f"    [{persona_id}] SL: {sl}")
                    if ex.get("hook"):
                        lines.append(f"    [{persona_id}] Hook: {ex['hook']}")

    if include is None or "channels" in include:
        for ch in ctx.get("matched_channels", []):
            lines.append(f"\nCHANNEL: {ch['name']} (Owner: {ch['owner']})")
            lines.append(f"  Use: {ch.get('use_case','')}")
            if ch.get("docebo_approach"):
                lines.append(f"  Approach: {ch['docebo_approach']}")

    if include is None or "proof_points" in include:
        if ctx.get("proof_points"):
            lines.append("\nPROOF POINTS:")
            for pp in ctx["proof_points"]:
                lines.append(f"  • {pp['claim']} [{pp['source']}]")

    return "\n".join(lines)


# ── Pipeline steps ────────────────────────────────────────────────────────────

# Persona Layer 1 signal keywords — mirrors the detection maps in graph_query.py / neo4j_query.py.
# Used in compute_readiness_score to re-derive persona_from_brief from the explicit brief,
# not from the enriched brief that query_graph receives.
_PERSONA_KW: dict = {
    "vp_ld":              ["l&d", "learning", "training", "development"],
    "cpo":                ["people", "hr", "human resources", "workforce", "talent"],
    "ld_program_manager": ["program manager", "instructional", "specialist", "coordinator"],
}

# ICP post-processing — restores firmographic details that Haiku drops from the ICP field.
_ICP_RANGE_RE = re.compile(
    r'(\d[\d,]*)\s*(?:to|[-–])\s*(\d[\d,]*)\s*(?:employees?|people|staff|FTEs?)',
    re.IGNORECASE,
)
_ICP_SINGLE_EMPLOYEE_RE = re.compile(r'(\d[\d,]+)\s*employees?\b', re.IGNORECASE)
_ICP_INDUSTRY_KW = [
    "B2B SaaS", "SaaS", "financial services", "fintech", "banking",
    "healthcare", "manufacturing", "retail", "technology", "software",
    "e-commerce", "professional services", "media",
]
_ICP_TECH_KW = [
    "internal engineering team", "engineering team", "internal dev team",
    "internal dev", "Salesforce", "existing LMS", "HubSpot", "Workday",
    "SAP", "in-house team",
]


def _patch_icp_from_brief(icp: str, raw_brief: str) -> str:
    """Restore employee ranges, industry, and tech signals dropped by the LLM."""
    if not icp or not raw_brief:
        return icp
    brief_lower = raw_brief.lower()
    patches: list = []

    # Fix truncated employee range (e.g. "3000 employees" → "1000 to 3000 employees")
    m_range = _ICP_RANGE_RE.search(raw_brief)
    if m_range:
        lo = m_range.group(1).replace(",", "")
        hi = m_range.group(2).replace(",", "")
        full = f"{m_range.group(1)} to {m_range.group(2)} employees"
        m_single = _ICP_SINGLE_EMPLOYEE_RE.search(icp)
        if m_single:
            single_val = m_single.group(1).replace(",", "")
            if single_val == hi and lo not in icp.replace(",", ""):
                # Only upper bound present — splice in the full range
                icp = icp[: m_single.start()] + full + icp[m_single.end():]
        elif lo not in icp.replace(",", "") and hi not in icp.replace(",", ""):
            patches.append(full)

    # Restore missing industry signal
    for kw in _ICP_INDUSTRY_KW:
        if kw.lower() in brief_lower and kw.lower() not in icp.lower():
            patches.append(kw)
            break

    # Restore missing tech/team signal
    for kw in _ICP_TECH_KW:
        if kw.lower() in brief_lower and kw.lower() not in icp.lower():
            patches.append(kw)
            break

    if patches:
        icp = icp.rstrip().rstrip(",") + ", " + ", ".join(patches)
    return icp


def extract_brief_structure(brief: str, ctx: dict) -> dict:
    _t0 = time.perf_counter()
    r = client.messages.create(
        model=HAIKU,
        max_tokens=500,
        temperature=0.0,
        system=_load_prompt("brief_parser.txt"),
        messages=[{"role": "user", "content": f"{_fmt_ctx(ctx, {'personas', 'pains'})}\n\n---\n\nBRIEF:\n{brief}"}]
    )
    PIPELINE_TIMINGS["01_parse_brief"] = round(time.perf_counter() - _t0, 2)
    return parse_json(r.content[0].text)


_NAMED_CUSTOMER_NAMES = [
    "Bethany Care Society", "MidFirst Bank", "Disguise",
    "SNCF", "Société Générale", "Segula Technologies",
]


def _pillar_proof_is_live(proof_text: str, product_id: str) -> bool:
    """
    True only when the proof point text cites a named customer that has a
    VALIDATES_PRODUCT edge to the matched product in the graph.

    Both conditions are required:
      1. A named customer name appears in the proof text.
      2. That customer's proof point node has a VALIDATES_PRODUCT edge to product_id.

    Stats (even those with product edges) do not satisfy condition 1, so they
    return False and always render as placeholders.
    """
    if not product_id:
        return False
    # Try Neo4j first, fall back to NetworkX
    try:
        from knowledge.neo4j_connection import is_available
        if is_available():
            from knowledge.neo4j_connection import get_driver
            driver = get_driver()
            with driver.session() as session:
                result = session.run(
                    """
                    MATCH (pp:ProofPoint)-[:VALIDATES_PRODUCT]->(prod:Product {id: $product_id})
                    WHERE pp.claim IS NOT NULL
                    RETURN pp.claim AS claim
                    """,
                    product_id=product_id,
                ).data()
            for row in result:
                claim = row.get("claim", "")
                if not any(name in claim for name in _NAMED_CUSTOMER_NAMES):
                    continue
                if any(name in proof_text for name in _NAMED_CUSTOMER_NAMES if name in claim):
                    return True
            return False
    except Exception:
        pass
    # NetworkX fallback
    from knowledge.graph_builder import build_marketing_graph
    G = build_marketing_graph()
    for nid, attr in G.nodes(data=True):
        if attr.get("type") != "ProofPoint":
            continue
        claim = attr.get("data", {}).get("claim", "")
        if not any(name in claim for name in _NAMED_CUSTOMER_NAMES):
            continue
        if not any(name in proof_text for name in _NAMED_CUSTOMER_NAMES if name in claim):
            continue
        if (G.has_edge(nid, product_id)
                and G[nid][product_id].get("relationship") == "VALIDATES_PRODUCT"):
            return True
    return False


def _apply_proof_point_placeholders(messaging: dict, ctx: dict) -> dict:
    """Replace any pillar proof point that lacks a named-customer + product-edge backing."""
    products     = ctx.get("matched_products", [])
    product_id   = ((ctx.get("_matched_node_ids") or {}).get("products") or [None])[0]
    product_name = products[0].get("name", "this product").replace("Docebo ", "") if products else "this product"
    placeholder  = (
        f"[PROOF POINT NEEDED: no {product_name}-specific case study in Marketing Brain.]"
    )
    for pillar in messaging.get("pillars", []):
        proof = pillar.get("proof_point", "")
        if proof.startswith("[PROOF POINT NEEDED"):
            continue
        if not _pillar_proof_is_live(proof, product_id):
            pillar["proof_point"] = placeholder
    return messaging


def _compute_data_gaps(messaging: dict, ctx: dict) -> list:
    """Deterministic post-processing: flag knowledge gaps the marketer must resolve before publishing.

    Called AFTER _apply_proof_point_placeholders so every amber pillar already
    carries the [PROOF POINT NEEDED marker — no need to re-detect (platform stat.
    """
    gaps = []
    products = ctx.get("matched_products", [])
    if not products:
        gaps.append("No product matched in Marketing Brain. Campaign may be generic. Specify the Docebo product in your brief.")
        return gaps

    product      = products[0]
    product_name = product.get("name", "this product").replace("Docebo ", "")

    # Pillar proof point quality — all amber pillars now carry the placeholder marker
    placeholder_pillars = [
        p.get("title", f"Pillar {i}")
        for i, p in enumerate(messaging.get("pillars", []), 1)
        if p.get("proof_point", "").startswith("[PROOF POINT NEEDED")
    ]
    if placeholder_pillars:
        titles = " · ".join(f'"{t}"' for t in placeholder_pillars)
        gaps.append(
            f"No {product_name}-specific named-customer case study in brain for: {titles}. "
            f"Request a verified case study from Customer Marketing."
        )

    # Email copy grounding
    if not product.get("email_examples"):
        gaps.append(
            f"No email copy examples in brain for {product_name}. Subject lines and hooks are LLM-generated "
            f"without Docebo-grounded patterns. Add proven subject lines to marketing_brain.json before writing sequences."
        )

    # ICP firmographic criteria
    if not product.get("icp"):
        gaps.append(
            f"No ICP firmographic criteria for {product_name} in brain. LinkedIn audience, 6sense, and "
            f"Demandbase targeting will be generic. Add company size, industries, and tech signals to marketing_brain.json."
        )

    return gaps


def generate_messaging(structure: dict, ctx: dict) -> dict:
    _t0 = time.perf_counter()

    # Pre-compute locked structural decisions before any Claude call
    locked_assets = _build_locked_assets(ctx)

    # Number proof points so Claude can reference them by index in each pillar
    pp_list = ctx.get("proof_points", [])
    pp_lock_lines = "\n".join(
        f"PP{i + 1}: {pp['claim']} [{pp.get('source', '')}]"
        for i, pp in enumerate(pp_list[:6])
    )

    locked_block = (
        "LOCKED STRUCTURE — graph decisions below are final. Do not modify.\n\n"
        "PROOF POINT ASSIGNMENT (use in this exact order — PP1 → Pillar 1, PP2 → Pillar 2, PP3 → Pillar 3):\n"
        f"{pp_lock_lines}\n\n"
        "LOCKED ASSET PLAN (copy asset_type, format, owner verbatim; write purpose only — max 15 words):\n"
        f"{json.dumps(locked_assets, indent=2)}"
    )

    _ctx_str = _fmt_ctx(ctx, {'personas', 'pains', 'products_slim', 'proof_points', 'channels'})
    r = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        temperature=0.1,
        system=_load_prompt("messaging_generator.txt"),
        messages=[{"role": "user", "content": (
            f"{_ctx_str}\n\n---\n\n"
            f"CAMPAIGN STRUCTURE:\n{json.dumps(structure, indent=2)}\n\n---\n\n"
            f"{locked_block}"
        )}]
    )
    result = parse_json(r.content[0].text)

    # Hard punctuation rule: campaign_concept must use only commas and full stops
    _enforce_concept_punctuation(result)

    # Remove any CTA whose persona key was not returned by the graph traversal
    _enforce_cta_personas(result, ctx)

    # Python-level enforcement: overwrite any LLM drift in asset structure
    _enforce_locked_assets(result, locked_assets)

    # Placeholders first — so _compute_data_gaps detects the [PROOF POINT NEEDED marker
    result = _apply_proof_point_placeholders(result, ctx)
    result["data_gaps"] = _compute_data_gaps(result, ctx)

    # Calibration rules (applied after proof-point grounding so we know evidence quality)
    _cap_cta_to_generic(result, structure)           # Rule 1
    _cap_assets_if_no_named_proof(result)            # Rule 2
    _enforce_asset_word_limits(result)               # Rule 6 (asset purposes 12w)

    PIPELINE_TIMINGS["02_messaging"] = round(time.perf_counter() - _t0, 2)
    return result


def _cap_rollout_days(rollout: dict, max_day: int) -> dict:
    """Clamp any task due_day that the LLM placed beyond the timeline ceiling."""
    for phase in rollout.get("phases", []):
        for task in phase.get("tasks", []):
            try:
                if int(task.get("due_day", 0) or 0) > max_day:
                    task["due_day"] = max_day
            except (TypeError, ValueError):
                pass
    return rollout


def _inject_blocker_tasks(rollout: dict, messaging: dict, ctx: dict) -> dict:
    """Insert a Customer Marketing [BLOCKER] task in Phase 1 for each pillar missing a case study."""
    products     = ctx.get("matched_products", [])
    product_name = products[0].get("name", "this product") if products else "this product"

    blocker_pillars = [
        p.get("title", f"Pillar {i}")
        for i, p in enumerate(messaging.get("pillars", []), 1)
        if p.get("proof_point", "").startswith("[PROOF POINT NEEDED")
    ]
    if not blocker_pillars or not rollout.get("phases"):
        return rollout

    phase1 = rollout["phases"][0]
    for pillar_title in blocker_pillars:
        phase1.setdefault("tasks", []).insert(0, {
            "task":    f"[BLOCKER] Insert approved {product_name} case study into '{pillar_title}' before publishing",
            "owner":   "Customer Marketing",
            "due_day": 1,
        })
    return rollout


_CP_DAY_RE = re.compile(r'\bDay\s+(\d+)\b', re.IGNORECASE)

# Matches "by Day N" with optional preceding phrases ("written approval by Day 5")
_SECONDARY_DAY_RE = re.compile(
    r',?\s*(?:(?:written\s+)?(?:approval|sign[- ]?off)|go/no-go\s+decision)?\s*by\s+Day\s+\d+',
    re.IGNORECASE,
)

_CP_SUFFIX = "approve or revise with written sign-off in Asana."


def _format_owner_list(owners: set) -> str:
    """Format a set of owner names as 'A and B' or 'A, B and C'."""
    sorted_owners = sorted(owners)
    if not sorted_owners:
        return "Marketing Ops"
    if len(sorted_owners) == 1:
        return sorted_owners[0]
    if len(sorted_owners) == 2:
        return f"{sorted_owners[0]} and {sorted_owners[1]}"
    return ", ".join(sorted_owners[:-1]) + f" and {sorted_owners[-1]}"


def _rewrite_cp_owners(cp_str: str, cp_day: int, owners: set, review_subject: str) -> str:
    """Rebuild a checkpoint string with the given owner list and day, preserving asset text."""
    if owners:
        owner_str = _format_owner_list(owners)
    else:
        # Fall back: extract whatever owner text Claude wrote
        m = _CP_DAY_RE.search(cp_str)
        rest = cp_str[m.end():].strip().lstrip(":").strip() if m else cp_str
        owner_str = rest.split(" review ")[0].strip() if " review " in rest else "Marketing Ops"

    # Try to preserve the assets description Claude generated
    review_m = re.search(r'\breview\s+(.+?)(?:,\s*approve\s+or\s+revise|$)', cp_str, re.IGNORECASE)
    assets = review_m.group(1).strip() if review_m else review_subject

    return f"Day {cp_day}: {owner_str} review {assets}, {_CP_SUFFIX}"


def _normalize_checkpoint_text(cp_str: str) -> str:
    """Strip secondary 'Day N' references; enforce canonical sign-off suffix."""
    m = _CP_DAY_RE.search(cp_str)
    if m:
        prefix = cp_str[: m.end()]   # preserves "Day 9" or "Day 16"
        body   = cp_str[m.end():]
    else:
        prefix, body = "", cp_str

    # Remove "by Day N" clauses (with optional leading phrase)
    body = _SECONDARY_DAY_RE.sub('', body)
    # Remove any remaining bare "Day N" mentions
    body = re.sub(r',?\s*Day\s+\d+\b', '', body, flags=re.IGNORECASE)
    body = body.strip().rstrip('.,;')

    if not re.search(r'sign[- ]?off\s+in\s+Asana', body, re.IGNORECASE):
        body += ', approve or revise with written sign-off in Asana.'
    elif not body.endswith('.'):
        body += '.'

    return (prefix + body).strip()


def _inject_missing_asset_tasks(rollout: dict, messaging: dict, timeline_days: int) -> dict:
    """Inject stub draft/launch tasks for any asset owner absent from Phase 1 or Phase 2.

    Claude omits assets when its 3-4 task-per-phase limit forces choices among owners.
    This function guarantees every locked asset owner appears in both phases, with
    minimal task text that stays well under the 15-word limit.

    Only Phase 1 (draft/approve) and Phase 2 (launch/activate) are checked.
    Phase 3 is the attribution phase and does not require asset-owner coverage.
    """
    asset_plan = messaging.get("asset_plan", [])
    phases     = rollout.get("phases", [])
    if not asset_plan or len(phases) < 2:
        return rollout

    # Phase day boundaries mirror the rollout_generator.txt formula.
    p1_end   = max(1, int(timeline_days * 0.33))
    p2_start = p1_end + 1
    p2_end   = max(p2_start, int(timeline_days * 0.66))

    # Deduplicated owner → first asset entry (preserves plan ordering).
    asset_by_owner: dict = {}
    for asset in asset_plan:
        owner = _normalize_owner((asset.get("owner") or "").strip())
        if owner and owner not in asset_by_owner:
            asset_by_owner[owner] = asset

    def _phase_owners(idx: int) -> set:
        return {
            _normalize_owner((t.get("owner") or "").strip())
            for t in phases[idx].get("tasks", [])
            if t.get("owner")
        }

    def _phase_last_day(idx: int, default: int) -> int:
        days = [
            int(t["due_day"])
            for t in phases[idx].get("tasks", [])
            if isinstance(t.get("due_day"), (int, float)) and int(t.get("due_day", 0)) > 0
        ]
        return max(days) if days else default

    p1_owners = _phase_owners(0)
    p2_owners = _phase_owners(1)
    p1_last   = _phase_last_day(0, p1_end)
    p2_last   = _phase_last_day(1, p2_end)

    for owner, asset in asset_by_owner.items():
        fmt        = (asset.get("format") or "").strip()
        asset_type = (asset.get("asset_type") or "asset").strip()
        ref        = fmt if fmt else asset_type  # format string is more specific

        if owner not in p1_owners:
            phases[0]["tasks"].append({
                "task":    f"{owner} drafts {ref} for review",
                "owner":   owner,
                "due_day": max(1, min(p1_last, p1_end)),
            })

        if owner not in p2_owners:
            phases[1]["tasks"].append({
                "task":    f"{owner} launches {ref}",
                "owner":   owner,
                "due_day": max(p2_start, min(p2_last, p2_end)),
            })

    return rollout


_DECK_CREATE_RE = re.compile(
    r'\b(?:build|creat|develop|draft|design|prepar|finaliz|complet|assembl|produc)\w*\b'
    r'.*\bdeck\b'
    r'|\bdeck\b.*\b(?:build|creat|develop|draft|design|prepar|finaliz|complet|assembl|produc)\w*\b',
    re.IGNORECASE,
)
_WEBINAR_PROMO_RE = re.compile(
    r'\b(?:launch|promot|schedul|host|run|deliver|send|push|activat|register|registrant|invit)\w*\b',
    re.IGNORECASE,
)


def _enforce_webinar_deck_phase(rollout: dict, timeline_days: int) -> dict:
    """Move webinar deck creation tasks from Phase 2/3 into Phase 1.

    Building the deck is a prerequisite for promotion. Claude sometimes places
    deck creation in Phase 2 because Field Marketing already has a speaker-sourcing
    task in Phase 1, satisfying the per-owner coverage check. This enforcer explicitly
    relocates any deck-creation task found in Phase 2 or later to Phase 1.
    """
    phases = rollout.get("phases", [])
    if len(phases) < 2:
        return rollout

    p1_end = max(1, int(timeline_days * 0.33))

    def _p1_has_deck() -> bool:
        return any(
            "deck" in (t.get("task") or "").lower()
            and bool(_DECK_CREATE_RE.search(t.get("task") or ""))
            for t in phases[0].get("tasks", [])
        )

    def _p1_last_day() -> int:
        days = [
            int(t["due_day"])
            for t in phases[0].get("tasks", [])
            if isinstance(t.get("due_day"), (int, float)) and int(t.get("due_day", 0)) > 0
        ]
        return max(days) if days else p1_end

    for phase_idx in range(1, len(phases)):
        if _p1_has_deck():
            break
        remaining = []
        for task in phases[phase_idx].get("tasks", []):
            task_text = (task.get("task") or "").strip()
            owner     = (task.get("owner") or "").lower()
            is_fm     = "field marketing" in owner
            has_deck  = "deck" in task_text.lower()
            is_create = bool(_DECK_CREATE_RE.search(task_text))
            is_promo  = bool(_WEBINAR_PROMO_RE.search(task_text))
            if is_fm and has_deck and is_create and not is_promo:
                task["due_day"] = _p1_last_day()
                phases[0]["tasks"].append(task)
            else:
                remaining.append(task)
        phases[phase_idx]["tasks"] = remaining

    return rollout


_P2_CREATE_VERB_RE = re.compile(
    r'\b(creates?|builds?|drafts?|writes?|develops?|designs?|prepares?|finalizes?|assembles?|produces?)\b',
    re.IGNORECASE,
)

_DEPLOY_VERB_BY_OWNER: dict = {
    "customer success":  "delivers",
    "sdr/bdr":           "sends",
    "paid social":       "activates",
    "field marketing":   "delivers",
    "demand gen":        "activates",
    "product marketing": "deploys",
    "pmm":               "deploys",
    "marketing ops":     "deploys",
    "revops":            "reports on",
}


def _enforce_phase2_deployment_language(rollout: dict) -> dict:
    """Replace creation verbs in Phase 2 tasks with deployment verbs.

    Phase 1 = foundation (create, build, draft). Phase 2 = activation (deploy,
    deliver, activate). An owner who created an asset in Phase 1 must use
    deployment language for that asset in Phase 2, not creation language again.
    """
    phases = rollout.get("phases", [])
    if len(phases) < 2:
        return rollout

    p1_creators: set = set()
    for task in phases[0].get("tasks", []):
        if _P2_CREATE_VERB_RE.search(task.get("task", "")):
            _own = _normalize_owner((task.get("owner") or "").strip()).lower()
            if _own:
                p1_creators.add(_own)

    for task in phases[1].get("tasks", []):
        _own = _normalize_owner((task.get("owner") or "").strip()).lower()
        if _own not in p1_creators:
            continue
        task_text = task.get("task", "")
        if not _P2_CREATE_VERB_RE.search(task_text):
            continue
        deploy_verb = _DEPLOY_VERB_BY_OWNER.get(_own, "deploys")
        task["task"] = _P2_CREATE_VERB_RE.sub(deploy_verb, task_text, count=1)

    return rollout


def _enforce_checkpoint_dependencies(rollout: dict) -> dict:
    """Hard-set Checkpoint 1 to phase1_max+1; floor Checkpoint 2 at phase2_max+1."""
    phases      = rollout.get("phases", [])
    checkpoints = rollout.get("human_review_checkpoints", [])
    if not phases or not checkpoints:
        return rollout

    def _max_task_day(phase: dict) -> int:
        days = []
        for task in phase.get("tasks", []):
            try:
                d = int(task.get("due_day", 0) or 0)
                if d > 0:
                    days.append(d)
            except (TypeError, ValueError):
                pass
        return max(days) if days else 0

    def _set_day(cp_str: str, target_day: int) -> str:
        """Replace the first 'Day N' occurrence with target_day, then normalise text."""
        m = _CP_DAY_RE.search(cp_str)
        fixed = (f"Day {target_day}: {cp_str}" if not m
                 else cp_str[:m.start()] + f"Day {target_day}" + cp_str[m.end():])
        return _normalize_checkpoint_text(fixed)

    def _floor_day(cp_str: str, min_day: int) -> str:
        """Only replace first 'Day N' if N < min_day, then normalise text."""
        m = _CP_DAY_RE.search(cp_str)
        if not m:
            return _normalize_checkpoint_text(cp_str)
        fixed = (cp_str[:m.start()] + f"Day {min_day}" + cp_str[m.end():]
                 if int(m.group(1)) < min_day else cp_str)
        return _normalize_checkpoint_text(fixed)

    updated   = list(checkpoints)
    phase_max = [_max_task_day(p) for p in phases]

    def _phase_owners(idx: int) -> set:
        return {
            _normalize_owner((t.get("owner") or "").strip())
            for t in (phases[idx].get("tasks", []) if idx < len(phases) else [])
            if t.get("owner")
        }

    # Checkpoint 1 reviews Phase 1 — ALWAYS equals last Phase 1 task day + 1
    _cp1_day = 0
    if len(updated) > 0 and len(phase_max) > 0 and phase_max[0] > 0:
        _cp1_day = phase_max[0] + 1
        updated[0] = _set_day(updated[0], _cp1_day)
        updated[0] = _rewrite_cp_owners(updated[0], _cp1_day, _phase_owners(0), "all Phase 1 asset drafts")

    # Phase 2 must not begin until at least one day after Checkpoint 1 sign-off.
    # Shift any Phase 2 task whose due_day falls on or before CP1 to CP1 + 1,
    # then recompute phase_max[1] so the CP2 floor uses the updated values.
    if _cp1_day > 0 and len(phases) > 1:
        for _t2 in phases[1].get("tasks", []):
            try:
                if int(_t2.get("due_day", 0) or 0) <= _cp1_day:
                    _t2["due_day"] = _cp1_day + 1
            except (TypeError, ValueError):
                pass
        if len(phase_max) > 1:
            phase_max[1] = _max_task_day(phases[1])

    # Checkpoint 2 reviews Phase 2 — must be at least last Phase 2 task day + 1
    if len(updated) > 1 and len(phase_max) > 1 and phase_max[1] > 0:
        _cp2_day = phase_max[1] + 1
        updated[1] = _floor_day(updated[1], _cp2_day)
        # Extract the actual day that _floor_day settled on
        _cp2_m = _CP_DAY_RE.search(updated[1])
        _cp2_actual = int(_cp2_m.group(1)) if _cp2_m else _cp2_day
        updated[1] = _rewrite_cp_owners(updated[1], _cp2_actual, _phase_owners(1), "Phase 2 performance")

    # Checkpoint 3 is not day-adjusted but still needs text normalisation
    if len(updated) > 2:
        updated[2] = _normalize_checkpoint_text(updated[2])

    rollout["human_review_checkpoints"] = updated
    return rollout


def generate_rollout(structure: dict, messaging: dict, ctx: dict) -> dict:
    _t0 = time.perf_counter()
    pillar_titles = [p.get("title", "") for p in messaging.get("pillars", [])]

    # Compute exact day ceiling: prefer goal.primary.timeframe_days over weeks × 7
    _goal      = structure.get("campaign_goal") or {}
    _primary   = _goal.get("primary", {}) if isinstance(_goal, dict) else {}
    _goal_days = int(_primary.get("timeframe_days", 0) or 0) if isinstance(_primary, dict) else 0
    _tl_weeks  = int(structure.get("timeline_weeks") or 8)
    _tl_days   = _goal_days if _goal_days else _tl_weeks * 7

    condensed = {
        "icp":            structure.get("icp"),
        "goal":           structure.get("campaign_goal"),
        "timeline_weeks": _tl_weeks,
        "timeline_days":  _tl_days,
        "tone":           structure.get("tone"),
        "pillars":        pillar_titles,
        "asset_assignments": [
            {"asset_type": a.get("asset_type"), "format": a.get("format"), "owner": a.get("owner")}
            for a in messaging.get("asset_plan", [])
            if a.get("asset_type") and a.get("owner")
        ],
    }
    r = client.messages.create(
        model=HAIKU,
        max_tokens=3500,
        temperature=0.0,
        system=_load_prompt("rollout_generator.txt"),
        messages=[{"role": "user", "content": f"{_fmt_ctx(ctx, {'channels'})}\n\n---\n\nROLLOUT INPUT:\n{json.dumps(condensed)}"}]
    )
    result = _correct_task_owners(parse_json(r.content[0].text))
    result = _enforce_asset_task_owners(result, messaging)        # lock owners to plan asset types
    result = _scrub_product_names(result, ctx)
    result = _strip_placeholder_tasks(result)
    result = _cap_rollout_days(result, _tl_days)
    result = _cap_phases_if_no_named_proof(result, messaging)     # Rule 3
    result = _enforce_task_asset_alignment(result, messaging)     # drop off-plan tasks
    result = _inject_missing_asset_tasks(result, messaging, _tl_days)  # fill gaps left by task-per-phase limit
    result = _enforce_webinar_deck_phase(result, _tl_days)            # deck creation must precede promotion
    result = _enforce_phase2_deployment_language(result)              # Phase 2 verbs: deploy/deliver, not create/build
    result = _enforce_checkpoint_dependencies(result)
    result = _flag_metric_benchmarks(result)           # Rule 5: $ → short form
    result = _flag_invented_numbers(result, ctx)       # Rule 5: % and counts → short form
    result = _collapse_benchmark_placeholders(result)  # Rule 5: LLM [BENCHMARK NEEDED...] → short form
    result = _enforce_word_limits(result)              # Rule 6: task 15w, milestone 10w
    PIPELINE_TIMINGS["03_rollout"] = round(time.perf_counter() - _t0, 2)
    return result


# ── Campaign Readiness Score ──────────────────────────────────────────────────

_NAMED_CUSTOMERS = [
    "Bethany Care Society", "MidFirst Bank", "Disguise",
    "SNCF", "Société Générale", "Segula Technologies",
]

_PCT_BENCH_RE = re.compile(r'\d+(?:\.\d+)?\s*%')

# Detect an explicitly stated numeric goal in the brief text.
# Two patterns, neither crosses a sentence boundary ([^.!?\n]):
#   A — number then qualifier words then metric  ("20 qualified early access sign-ups")
#   B — metric then qualifier words then number  ("sign-ups goal of 20", "target: 5 leads")
_GOAL_METRIC_STEM = (
    r'sign.?up|lead|demo|registrant|attendee|conversation|conversion|'
    r'meeting|opportunit|MQL|SQL|download|trial|webinar|pipeline|contact|prospect'
)
_EXPLICIT_GOAL_RE = re.compile(
    r'(?:'
    r'\b\d+\b[^.!?\n]{0,60}?\b(?:' + _GOAL_METRIC_STEM + r')'
    r'|'
    r'\b(?:' + _GOAL_METRIC_STEM + r')\w*[^.!?\n]{0,60}?\b\d+\b'
    r')',
    re.IGNORECASE,
)

# Detect an explicitly stated timeline in the brief text (digit OR natural language)
_EXPLICIT_TIMELINE_RE = re.compile(
    r'(?:'
    r'\b\d+\s*(?:week|day|month)s?'                                                      # "6 weeks", "30 days"
    r'|\b(?:a|an|one)\s+(?:week|month|day)\b'                                            # "a week", "one month"
    r'|\b(?:two|three|four|five|six|seven|eight|nine|ten|twelve)\s+(?:week|day|month)s?' # "two weeks"
    r'|\bnext\s+(?:week|month)\b'                                                        # "next month"
    r')',
    re.IGNORECASE,
)

# Detect explicit audience targeting signal in the brief text.
# "targeting" is intentionally excluded — it matches goal quantities ("targeting 10 demos")
# as often as audience types. All real audience signals are covered by the patterns below.
_AUDIENCE_SIGNAL_RE = re.compile(
    r'(?:'
    r'\baimed?\s+at\b'
    r'|\bexisting\s+(?:\w+\s+)?customers?\b'
    r'|\bnet-?new\b'
    r'|\bVP\b'
    r'|\bCLO\b'
    r'|\bCHRO\b'
    r'|\bL&D\b'
    r'|\blearning\s+(?:leaders?|executives?|managers?)\b'
    r')',
    re.IGNORECASE,
)

# Detect explicit ICP FIRMOGRAPHICS only: company size, team size, industry.
# Does NOT match role titles (VP, Director, CLO — persona signals, not firmographics).
# Does NOT match audience type (existing customers, net-new — motion signals).
_ICP_FIRM_BRIEF_RE = re.compile(
    r'(?:'
    r'\bmid-?market\b'
    r'|\bSMB\b'
    r'|\bsmall\s+(?:business|company|firm|team)\b'
    r'|\blarge\s+(?:company|enterprise|organization)\b'
    r'|\bstartup\b'
    r'|\b\d+\s*\+?\s*(?:employee|seat|person|user)s?\b'
    r'|\b(?:financial\s+service|healthcare|retail|manufacturing|banking|pharma|'
    r'insurance|education|hospitality)(?:s|\s+sector|\s+industry)?\b'
    r')',
    re.IGNORECASE,
)


def compute_readiness_score(
    structure: dict,
    messaging: dict,
    rollout: dict,
    ctx: dict,
    explicit_brief: str = "",
) -> dict:
    """
    Two-dimension Campaign Readiness Score.

    Structure Score (100 pts): what was provided and graph-matched.
    Evidence Score  (100 pts): what the Marketing Brain can substantiate.
    Readiness = round(Structure × Evidence / 100).

    Returns:
        {
            "structure_score": int,
            "evidence_score":  int,
            "readiness":       int,   # combined 0-100
            "score":           int,   # alias for readiness (used by sidebar)
            "status":          str,
            "structure_reqs":  list,
            "evidence_reqs":   list,
            "product_ok":      bool,  # stored for sidebar status derivation
            "persona_ok":      bool,
        }
    """
    _eb = explicit_brief or ""

    def _req(label: str, weight: int, satisfied: bool) -> dict:
        ok = bool(satisfied)
        return {"label": label, "weight": weight, "satisfied": ok, "pts": weight if ok else 0}

    # ── Structure dimension ───────────────────────────────────────────────────
    # Only points for what the user or graph explicitly provided.

    s_product  = _req("Product matched from brief",    25, bool(ctx.get("matched_products")))
    s_persona  = _req("Persona selected from graph",  20, bool(ctx.get("matched_personas")))

    # Goal/Timeline: score only if the value appears in explicit brief text.
    s_goal     = _req("Goal explicitly stated", 20, bool(_EXPLICIT_GOAL_RE.search(_eb)))
    s_timeline = _req("Timeline explicitly stated", 15, bool(_EXPLICIT_TIMELINE_RE.search(_eb)))

    s_pain     = _req("Pain mapped from graph",     10, bool(ctx.get("matched_pains")))
    s_channels = _req("Channels validated from graph", 10, bool(ctx.get("matched_channels")))

    structure_reqs  = [s_product, s_persona, s_goal, s_timeline, s_pain, s_channels]
    structure_score = sum(r["pts"] for r in structure_reqs)

    # ── Evidence dimension ────────────────────────────────────────────────────
    _proof_pps        = ctx.get("proof_points") or []
    _matched_prod_ids = set((ctx.get("_matched_node_ids") or {}).get("products") or [])

    # Identify which proof points have a VALIDATES_PRODUCT edge to a matched product.
    # Named customer case study requires both this edge AND a named customer in the claim.
    # A product stat (VALIDATES_PRODUCT but no named customer) counts only for Verified benchmark.
    _green_pp_ids: set = set()
    try:
        from knowledge.neo4j_connection import is_available as _n4j_ev_ok
        if _n4j_ev_ok() and _matched_prod_ids:
            from knowledge.neo4j_connection import get_driver as _n4j_ev_drv
            with _n4j_ev_drv().session() as _ev_s:
                for _ev_pid in _matched_prod_ids:
                    _green_pp_ids.update(
                        row["id"] for row in _ev_s.run(
                            "MATCH (pp:ProofPoint)-[:VALIDATES_PRODUCT]->(prod:Product {id: $pid}) "
                            "RETURN pp.id AS id",
                            pid=_ev_pid,
                        ).data()
                    )
    except Exception:
        try:
            from knowledge.graph_builder import build_marketing_graph as _ev_build_g
            _ev_G = _ev_build_g()
            for _ev_pp_id, _ev_attr in _ev_G.nodes(data=True):
                if _ev_attr.get("type") != "ProofPoint":
                    continue
                if any(
                    _ev_G.has_edge(_ev_pp_id, _ev_prod_id)
                    and _ev_G[_ev_pp_id][_ev_prod_id].get("relationship") == "VALIDATES_PRODUCT"
                    for _ev_prod_id in _matched_prod_ids
                ):
                    _green_pp_ids.add(_ev_pp_id)
        except Exception:
            pass

    _named_proof = any(
        pp.get("id", "") in _green_pp_ids
        and any(name in pp.get("claim", "") for name in _NAMED_CUSTOMERS)
        for pp in _proof_pps
    )
    e_proof = _req("Named customer case study", 50, _named_proof)

    # Verified benchmark: must be a separate product-validated proof point, not the
    # named case study already counted above (which would double-count MidFirst Bank etc.)
    _bench_ok = any(
        _PCT_BENCH_RE.search(pp.get("claim", ""))
        and pp.get("id", "") in _green_pp_ids
        and not any(name in pp.get("claim", "") for name in _NAMED_CUSTOMERS)
        for pp in _proof_pps
    )
    e_bench   = _req("Verified benchmark in graph", 25, _bench_ok)

    _icp_in_brief = bool(_ICP_FIRM_BRIEF_RE.search(_eb))
    e_icp = _req("ICP firmographics in brief", 25, _icp_in_brief)

    evidence_reqs  = [e_proof, e_bench, e_icp]
    evidence_score = sum(r["pts"] for r in evidence_reqs)

    # ── Combined readiness ────────────────────────────────────────────────────
    readiness = round(structure_score * evidence_score / 100)

    _product_ok = s_product["satisfied"]
    _persona_ok = s_persona["satisfied"]

    # Status: independent 4-bucket check on each dimension's threshold
    _struct_high = structure_score >= 70
    _evid_high   = evidence_score  >= 70
    if _struct_high and _evid_high:
        status = "Execution Ready"
    elif _struct_high and not _evid_high:
        status = "Hypothesis"
    elif not _struct_high and _evid_high:
        status = "Incomplete"
    else:
        status = "Blocked"

    # ── Inference Load ────────────────────────────────────────────────────────
    # Determine which elements came from the explicit brief vs graph/system inference.

    # Product: VERIFIED if the product name or any alias appears verbatim in the brief (case-insensitive).
    # Checks the full name, name without "Docebo " prefix, and all also_known_as aliases as substrings
    # so multi-word names like "agent hub" match regardless of how they are spelled in the brief.
    _prod_nodes = ctx.get("matched_products") or []
    _product_in_brief = False
    if _prod_nodes and _eb:
        _eb_lower = _eb.lower()
        _p0 = _prod_nodes[0]
        _full_name = (_p0.get("name") or "").strip()
        _name_variants: set = set()
        if _full_name:
            _name_variants.add(_full_name.lower())
            _name_variants.add(_full_name.lower().replace("docebo ", "").strip())
        for _alias in (_p0.get("also_known_as") or []):
            if _alias:
                _name_variants.add(_alias.lower().strip())
        _product_in_brief = any(v in _eb_lower for v in _name_variants if v)

    _provided_elements: list = []
    _inferred_elements: list = []

    if _product_in_brief:
        _provided_elements.append("Product")
    else:
        _inferred_elements.append("Product")

    if bool(_EXPLICIT_GOAL_RE.search(_eb)):
        _provided_elements.append("Goal")
    else:
        _inferred_elements.append("Goal")

    if bool(_EXPLICIT_TIMELINE_RE.search(_eb)):
        _provided_elements.append("Timeline")
    else:
        _inferred_elements.append("Timeline")

    if bool(_AUDIENCE_SIGNAL_RE.search(_eb)):
        _provided_elements.append("Audience")
    else:
        _inferred_elements.append("Audience")

    if _icp_in_brief:
        _provided_elements.append("ICP details")
    else:
        _inferred_elements.append("ICP details")

    # Persona selection: Provided only if a persona title, alias, or signal keyword appears
    # in the *explicit* brief text (Layer 1).  ctx["persona_from_brief"] is derived from the
    # enriched brief (which auto-enrich may have added persona language to) so we re-derive
    # here from _eb, which is the raw explicit brief passed to this function.
    _persona_from_explicit = False
    _eb_lower = _eb.lower()
    for _mp in (ctx.get("matched_personas") or []):
        _title = (_mp.get("title") or "").lower()
        if _title and _title in _eb_lower:
            _persona_from_explicit = True
            break
        for _aka in (_mp.get("also_known_as") or []):
            if _aka and _aka.lower() in _eb_lower:
                _persona_from_explicit = True
                break
        if _persona_from_explicit:
            break
    if not _persona_from_explicit:
        _persona_from_explicit = any(
            any(kw in _eb_lower for kw in kws)
            for kws in _PERSONA_KW.values()
        )

    if _persona_from_explicit:
        _provided_elements.append("Persona selection")
    else:
        _inferred_elements.append("Persona selection")

    # Always graph-derived
    _inferred_elements += ["Campaign motion", "Channels"]

    _infer_count = len(_inferred_elements)
    if _infer_count <= 2:
        _inference_load = "Low"
    elif _infer_count <= 4:
        _inference_load = "Medium"
    else:
        _inference_load = "High"

    # ── Field classification ──────────────────────────────────────────────────
    # VERIFIED = came from a graph edge or explicit brief text.
    # INFERRED = derived from a node property or system reasoning without a stated source.
    _field_classification = {
        "Product":           "VERIFIED",  # keyword match to product node
        "Primary persona":   "VERIFIED" if _persona_from_explicit else "INFERRED",
        "Goal":              "VERIFIED" if s_goal["satisfied"]     else "INFERRED",
        "Timeline":          "VERIFIED" if s_timeline["satisfied"] else "INFERRED",
        "Channels":          "VERIFIED",  # REACHES_PERSONA edges only
        "Pain nodes":        "VERIFIED" if s_pain["satisfied"]     else "INFERRED",
        "Audience":          "VERIFIED" if bool(_AUDIENCE_SIGNAL_RE.search(_eb)) else "INFERRED",
        "ICP firmographics": "INFERRED",  # persona node properties — not from brief
        "Campaign motion":   "INFERRED",  # product-to-motion mapping — not from brief
        "Company size":      "INFERRED",  # persona node property
        "Team size":         "INFERRED",  # persona node property
    }

    return {
        "structure_score":       structure_score,
        "evidence_score":        evidence_score,
        "readiness":             readiness,
        "score":                 readiness,          # sidebar alias
        "status":                status,
        "structure_reqs":        structure_reqs,
        "evidence_reqs":         evidence_reqs,
        "product_ok":            _product_ok,
        "persona_ok":            _persona_ok,
        "inference_load":        _inference_load,
        "provided_elements":     _provided_elements,
        "inferred_elements":     _inferred_elements,
        "field_classification":  _field_classification,
        # Re-derived from explicit brief (not enriched brief) — used by Graph Decisions rationale
        "persona_from_explicit":   _persona_from_explicit,
        "timeline_from_explicit":  s_timeline["satisfied"],
    }
