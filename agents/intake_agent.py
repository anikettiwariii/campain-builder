import anthropic
import json
import re
import os

from builders.campaign_builder import load_knowledge_graph, parse_json

client = anthropic.Anthropic()
MODEL  = "claude-haiku-4-5-20251001"

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_prompt(name: str) -> str:
    p    = os.path.join(_ROOT, "prompts")
    ctx  = open(os.path.join(p, "system_context.txt")).read().strip()
    role = open(os.path.join(p, name)).read().strip()
    return f"{ctx}\n\n{role}"


# ── Product lists ─────────────────────────────────────────────────────────────

# All recognisable Docebo product names and common short-form references.
# Checked as substrings so ordering matters — put multi-word forms first.
_DOCEBO_PRODUCTS = [
    "agenthub", "agent hub",
    "skills intelligence", "skill intelligence", "skills",
    "enterprise knowledge",
    "advanced analytics",
    "headless learning", "headless",
    "roleplay", "role play",
    "content creator", "content creation",
    "harmony ai", "harmony",
    "content marketplace",
    "docebo learn", "docebo perform",
    "docebo shape", "docebo engage",
    "docebo lms", "docebo",   # bare "docebo" matches when no product is named
]

# Products that imply expansion motion — audience inferred as existing customers.
# Source of truth: campaign_motion field in marketing_brain.json.
# Only products whose motion is exclusively expansion belong here.
# Roleplay and Content Creator support "expansion or net-new" — excluded.
_EXPANSION_ONLY_IDS = {
    "agenthub", "skills_intelligence", "enterprise_knowledge",
    "advanced_analytics", "headless_learning", "harmony_ai",
}
_EXPANSION_ONLY_NAMES = [
    "agenthub", "agent hub",
    "skills intelligence", "skill intelligence", "skills",
    "enterprise knowledge",
    "advanced analytics",
    "headless learning", "headless",
    "harmony ai", "harmony",
]

# Industry verticals — treated as vertical filters within the existing customer
# base, not as net-new acquisition signals, when an expansion-only product matches.
_VERTICALS = [
    "financial services", "banking", "insurance", "healthcare", "pharma",
    "pharmaceutical", "retail", "manufacturing", "technology", "tech",
    "education", "government", "telecom", "energy",
]


# ── Regexes ───────────────────────────────────────────────────────────────────

# Numeric timeline: "8 weeks", "30 days", "3 months"
_TIMELINE_NUMERIC_RE = re.compile(
    r'\b\d+\s*(?:weeks?|days?|months?)\b', re.IGNORECASE
)

# Fuzzy timeline: "one month", "a quarter", "Q2", "by March", "end of year",
# "one week", "one day" etc.
_TIMELINE_FUZZY_RE = re.compile(
    r'\bone\s+day\b|\ba\s+day\b|\bsingle\s+day\b|'
    r'\bone\s+week\b|\ba\s+week\b|'
    r'\bone\s+month\b|\ba\s+month\b|\bmonthly\b|'
    r'\ba\s+quarter\b|\bthis\s+quarter\b|\bnext\s+quarter\b|\bq[1-4]\b|'
    r'\bend\s+of\s+year\b|\beoy\b|'
    r'\ba?\s*sprint\b|\bone\s+sprint\b|'
    r'\bby\s+(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|'
    r'jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b|'
    r'\bend\s+of\s+(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|'
    r'jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b',
    re.IGNORECASE
)

# Fuzzy → canonical week count (first match wins, ordered most-to-least specific).
# "one day" / "a day" is intentionally absent — handled as explicit days, not weeks.
_TIMELINE_FUZZY_MAP = [
    (re.compile(r'\ba\s+quarter\b|\bthis\s+quarter\b|\bnext\s+quarter\b|\bq[1-4]\b',
                re.IGNORECASE), 12),
    (re.compile(r'\bone\s+month\b|\ba\s+month\b|\bmonthly\b', re.IGNORECASE), 4),
    (re.compile(r'\ba?\s*sprint\b|\bone\s+sprint\b', re.IGNORECASE), 2),
    (re.compile(r'\bone\s+week\b|\ba\s+week\b', re.IGNORECASE), 1),
    (re.compile(r'\bend\s+of\s+year\b|\beoy\b', re.IGNORECASE), 16),
]

# Sub-week detection — "1 day", "2 days", "one day", "a day"
_DAY_NUMERIC_RE = re.compile(r'\b(\d+)\s*days?\b', re.IGNORECASE)
_DAY_WORD_RE    = re.compile(r'\bone\s+day\b|\ba\s+day\b|\bsingle\s+day\b', re.IGNORECASE)

# Goal: number + concrete outcome word.
# Vague intent ("get more leads") without a number → False.
# Allow 0-2 qualifier words between the number and the metric so that
# "40 qualified demo requests", "30 net-new leads", "50 sales-qualified MQLs"
# all match. \w[\w\-]* lets hyphenated words like "net-new" count as one token.
# Negative lookahead blocks stop-words / prepositions ("percent of leads" must
# not match as a campaign goal).
_GOAL_RE = re.compile(
    r'\b\d+'
    r'(?:\s+(?!of\b|a\b|the\b|per\b|percent\b|in\b|for\b|and\b|or\b|to\b)\w[\w\-]*){0,2}'
    r'\s*'
    r'(?:demo(?:s|\s+requests?)?|mql(?:s)?|lead(?:s)?|'
    r'sign[\s\-]?up(?:s)?|registrations?|registrants?|'
    r'conversion(?:s)?|completion(?:s)?|opportunit(?:y|ies)|'
    r'case\s+stud(?:y|ies)|pipeline(?:\s+\$[\d,]+k?)?|'
    r'access\s+sign[\s\-]?ups?|waitlist(?:\s+sign[\s\-]?ups?)?|'
    r'early\s+access(?:\s+requests?)?)\b',
    re.IGNORECASE
)

# Audience: explicit motion keyword.
# Expansion-only products set this True in validate_brief() before checking.
_AUDIENCE_RE = re.compile(
    r'\b(?:existing\s+(?:customers?|accounts?|users?|clients?)|'
    r'current\s+(?:customers?|accounts?|users?|clients?)|'
    r'our\s+(?:customers?|accounts?|users?|clients?)|'
    r'net[\s\-]?new|new\s+(?:accounts?|customers?|prospects?)|'
    r'\bprospects?\b|'
    r'expansion\s+(?:motion|play|campaign)|upsell|up[\s\-]sell|acquisition)\b',
    re.IGNORECASE
)

# Dual-motion detected — user said both expansion and net-new, which is invalid.
_DUAL_MOTION_RE = re.compile(
    r'\bboth\b.{0,60}(?:existing|net[\s\-]?new|new\s+accounts?)|'
    r'(?:existing|net[\s\-]?new|new\s+accounts?).{0,60}\bboth\b|'
    r'(?:existing|current)\s+(?:customers?|accounts?).{0,60}net[\s\-]?new|'
    r'net[\s\-]?new.{0,60}(?:existing|current)\s+(?:customers?|accounts?)',
    re.IGNORECASE | re.DOTALL
)


# ── Known persona registry ────────────────────────────────────────────────────
# Must stay in sync with _PERSONA_SIGNALS in neo4j_query.py / graph_query.py.

_KNOWN_PERSONAS = [
    {
        "id":      "vp_ld",
        "title":   "VP of Learning & Development",
        "signals": ["l&d", "learning", "training", "development"],
        "aliases": ["vp of l&d", "vp learning", "head of learning", "director of l&d",
                    "director of learning", "chief learning officer", "clo",
                    "l&d leader", "learning leader"],
    },
    {
        "id":      "cpo",
        "title":   "Chief People Officer",
        "signals": ["people", "hr", "human resources", "workforce", "talent"],
        "aliases": ["chief people", "chro", "chief hr", "chief human resources",
                    "head of people", "head of hr", "vp people", "vp of people",
                    "vp hr", "vp of hr", "hr director", "people director"],
    },
    {
        "id":      "ld_program_manager",
        "title":   "L&D Program Manager",
        "signals": ["program manager", "instructional", "specialist", "coordinator"],
        "aliases": ["l&d manager", "learning manager", "training manager",
                    "instructional designer", "training coordinator", "learning specialist"],
    },
]

# Flat set of all signals + aliases across all personas — used for quick match
_ALL_PERSONA_KEYWORDS: set = set()
for _p in _KNOWN_PERSONAS:
    _ALL_PERSONA_KEYWORDS.update(_p["signals"])
    _ALL_PERSONA_KEYWORDS.update(_p["aliases"])

# Title-indicator words that suggest a persona is being explicitly named.
# Deliberately excludes "manager" / "specialist" / "coordinator" because those
# are already in known signals and would produce false positives.
_TITLE_INDICATOR_RE = re.compile(
    r'\b(?:vp\b|vice[\s-]president|director\b|head\s+of\b|'
    r'chief\s+\w+\s+officer|'
    r'\bcpo\b|\bcmo\b|\bcfo\b|\bcto\b|\bchro\b|\bclo\b|\bcro\b|\bcso\b|\bcoo\b|\bceo\b|'
    r'\bsvp\b|\bevp\b)',
    re.IGNORECASE,
)


def _extract_stated_persona(brief: str) -> str:
    """Best-effort extraction of the stated persona phrase."""
    patterns = [
        r'targeting\s+(?:the\s+)?([A-Z][^,\.;\n]{2,45})',
        r'aimed?\s+at\s+(?:the\s+)?([A-Z][^,\.;\n]{2,45})',
        r'for\s+(?:the\s+)?([A-Z][^,\.;\n]{2,40})',
        r'audience[:\s]+([^,\.;\n]{2,45})',
        r'persona[:\s]+([^,\.;\n]{2,45})',
        r'\b((?:VP|CRO|CFO|CTO|CMO|CEO|CHRO|CLO|SVP|EVP|'
        r'Chief|Director|Head\s+of)[^,\.;\n]{2,45})',
    ]
    for pat in patterns:
        m = re.search(pat, brief, re.IGNORECASE)
        if m:
            return m.group(1).strip()[:50]
    return "the persona in your brief"


def check_persona_match(brief: str) -> dict:
    """
    Validate that any persona mentioned in the brief matches a known graph node.

    Returns {"matched": True} when:
      - a known signal / alias is found in the brief, OR
      - the brief contains no explicit role/title (system decides from product)

    Returns {"matched": False, "stated": str, "available": list} when the brief
    contains a job-title indicator word but none of the known persona signals
    match — meaning the marketer specified a persona not in the Marketing Brain.
    """
    b = brief.lower()

    # Any known signal or alias → recognised persona
    if any(kw in b for kw in _ALL_PERSONA_KEYWORDS):
        return {"matched": True}

    # No title indicator → marketer didn't specify a persona; graph decides
    if not _TITLE_INDICATOR_RE.search(brief):
        return {"matched": True}

    # Title indicator present but nothing matched → unrecognised persona
    return {
        "matched":   False,
        "stated":    _extract_stated_persona(brief),
        "available": [p["title"] for p in _KNOWN_PERSONAS],
    }


# ── Pre-validation signals (used by is_valid_docebo_brief) ───────────────────

_LD_CONTEXT = [
    "learning", "training", "l&d", "human resources", "workforce",
    "skills", "lms", "e-learning", "elearning", "upskill", "reskill",
    "instructional", "course", "talent", "competency", "onboarding",
    "performance management", "capability",
]

_SAAS_CONTEXT = [
    "saas", "b2b", "enterprise software", "software platform",
    "software company", "platform",
]

_MARKETING_CONTEXT = [
    "campaign", "launch", "awareness", "promote", "promotion", "marketing",
    "demo", "demos", "leads", "pipeline", "webinar", "mql", "demand gen",
    "market", "grow", "drive", "generate",
]

# Consumer brands and off-domain categories that have nothing to do with Docebo.
_OFFBRAND_RE = re.compile(
    r'\b(?:porsche|nike|bmw|mercedes|toyota|ford|honda|audi|tesla|volkswagen|'
    r'ferrari|lamborghini|'
    r'coca.cola|pepsi|starbucks|mcdonalds|subway|chipotle|'
    r'walmart|target|costco|'
    r'restaurant|cafe|diner|hotel|motel|airline|'
    r'shoes|clothing|apparel|fashion|jewelry|jewellery|'
    r'cars?|automobile|truck|motorcycle|boat|yacht|'
    r'grocery|supermarket|pizza|burger|'
    r'spa|salon)\b',
    re.IGNORECASE
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _infer_timeline_weeks(brief: str) -> int | None:
    """Return week count inferred from a fuzzy timeline phrase, or None."""
    for pattern, weeks in _TIMELINE_FUZZY_MAP:
        if pattern.search(brief):
            return weeks
    return None


def _sanitize_brief(brief: str) -> tuple:
    """Remove control characters, normalize newlines, truncate to 1000 chars.

    Returns (sanitized_brief, was_truncated).
    """
    cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', brief)
    cleaned = re.sub(r'[\r\n]+', ' ', cleaned).strip()
    truncated = len(cleaned) > 1000
    return cleaned[:1000], truncated


# ── Public API ────────────────────────────────────────────────────────────────

def is_valid_docebo_brief(brief: str) -> dict:
    """
    Pre-validation gate — deterministic, no API call.

    Returns {"valid": True} or {"valid": False, "reason": "..."}.
    Must be called before validate_brief() / intake_agent() to reject
    clearly off-topic submissions without wasting the question budget.
    """
    b = brief.lower().strip()

    if len(b.split()) < 3:
        return {"valid": False, "reason": "Brief is too short. Describe what you want to campaign for."}

    # Docebo product name → immediately valid
    if any(p in b for p in _DOCEBO_PRODUCTS):
        return {"valid": True}

    # L&D / HR / learning context → valid
    if any(kw in b for kw in _LD_CONTEXT):
        return {"valid": True}

    # B2B SaaS context → valid
    if any(kw in b for kw in _SAAS_CONTEXT):
        return {"valid": True}

    offbrand_match = _OFFBRAND_RE.search(brief)
    if offbrand_match:
        return {
            "valid":  False,
            "reason": f"'{offbrand_match.group(0)}' is not a Docebo product or L&D context.",
        }

    # Anything that isn't clearly off-domain passes through — intake agent
    # will ask for missing details (product, goal, audience, timeline).
    return {"valid": True}


def validate_brief(brief: str) -> dict:
    """
    Smart inference — deterministic, no Claude call.

    Asks only when missing information would materially change the campaign
    structure (channel mix, conversion event type, audience motion).
    Infers everything it reasonably can.
    """
    b = brief.lower()

    # PRODUCT — explicit name or common short-form reference
    has_product = any(p in b for p in _DOCEBO_PRODUCTS)

    # GOAL — number + concrete outcome word; vague intent without a number → False
    has_goal = bool(_GOAL_RE.search(brief))

    # TIMELINE — numeric ("8 weeks") OR fuzzy phrase ("one month", "next quarter")
    has_timeline = bool(
        _TIMELINE_NUMERIC_RE.search(brief) or _TIMELINE_FUZZY_RE.search(brief)
    )

    # AUDIENCE — explicit motion keyword, or implied by expansion-only product
    has_audience = bool(_AUDIENCE_RE.search(brief))
    if not has_audience and any(p in b for p in _EXPANSION_ONLY_NAMES):
        has_audience = True

    return {
        "has_product":  has_product,
        "has_goal":     has_goal,
        "has_timeline": has_timeline,
        "has_audience": has_audience,
    }


def _auto_enrich(brief: str, ctx: dict, truncated: bool = False) -> str:
    """Build enriched brief from original + knowledge graph without a Claude call."""
    parts    = [brief.strip()]
    b        = brief.lower()
    products = ctx.get("matched_products", [])

    is_expansion = any(prod.get("id", "") in _EXPANSION_ONLY_IDS for prod in products)

    personas = ctx.get("matched_personas", [])
    if personas:
        p    = personas[0]
        line = f"Target persona: {p['title']}"  # company_size is INFERRED — not injected into brief
        if p.get("motivations"):
            line += f". Motivated by: {', '.join(p['motivations'][:2])}"
        parts.append(line + ".")

    if products:
        parts.append(f"Product: {products[0].get('name', '')}")

    # Detect explicit day count (numeric or word form)
    _day_m  = _DAY_NUMERIC_RE.search(brief)
    _day_tl = int(_day_m.group(1)) if _day_m else (1 if _DAY_WORD_RE.search(brief) else None)

    if _day_tl is not None:
        # Day-based timeline: always add an explicit ceiling note so brief_parser
        # and rollout generator know the exact day count, not a week approximation.
        _wk = -(-_day_tl // 7)  # ceiling division (no math import needed)
        parts.append(
            f"TIMELINE CONSTRAINT: Campaign is exactly {_day_tl} day{'s' if _day_tl != 1 else ''}. "
            f"Set timeline_weeks = {_wk}. "
            f"No task or checkpoint may be scheduled beyond day {_day_tl}."
        )
        if _day_tl < 7:
            parts.append(
                f"Note: {_day_tl}-day timeline is extremely short for a full campaign. "
                f"agent will structure for maximum velocity with highest-impact channels only."
            )
    elif not _TIMELINE_NUMERIC_RE.search(brief):
        # Fuzzy/word-based timeline with no numeric match — infer weeks
        inferred = _infer_timeline_weeks(brief)
        if inferred:
            parts.append(
                f"Inferred timeline: {inferred} weeks "
                f"(derived from timeline reference in brief, use this as timeline_weeks)."
            )

    # Explicitly frame expansion motion so no downstream step infers net-new
    if is_expansion:
        vertical = next((v for v in _VERTICALS if v in b), "")
        if vertical:
            parts.append(
                f"Campaign motion: Expansion (existing Docebo LMS customers only). "
                f"'{vertical.title()}' is the target vertical within the existing customer base, "
                f"not a net-new acquisition signal."
            )
        else:
            parts.append(
                "Campaign motion: Expansion (existing Docebo LMS customers only). "
                "This product is available exclusively to current Docebo accounts."
            )

    # AgentHub: pre-launch timing + correct goal framing
    if any(prod.get("id", "") == "agenthub" for prod in products):
        parts.append(
            "PRODUCT NOTE: AgentHub launches Fall 2026 and is not yet live. "
            "Campaign motion must target existing Docebo LMS customers for early access sign-ups "
            "or waitlist requests, not standard demos. The campaign goal should be framed as "
            "exclusive early access pipeline, not product trial activation."
        )

    if truncated:
        parts.append("Note: brief was truncated to 1000 characters for processing.")

    return " ".join(parts)


def intake_agent(brief: str, force_proceed: bool = False) -> dict:
    """
    Pre-flight check before the full pipeline.

    Returns {"proceed": True, "enriched_brief": "..."} or
            {"proceed": False, "question": "...", "reasoning": "..."}

    force_proceed=True skips validation entirely and enriches with best
    inference — used when the caller has exhausted the question budget (max 2).

    Fast path: all four signals confirmed → no API call, deterministic.
    Slow path: one signal missing → single Claude question, temperature=0.
    """
    brief, _truncated = _sanitize_brief(brief)
    ctx = load_knowledge_graph(brief)

    if force_proceed:
        return {"proceed": True, "enriched_brief": _auto_enrich(brief, ctx, _truncated)}

    validation = validate_brief(brief)

    if all(validation.values()):
        return {"proceed": True, "enriched_brief": _auto_enrich(brief, ctx, _truncated)}

    _PRIORITY = ["PRODUCT", "GOAL", "AUDIENCE", "TIMELINE"]
    _KEY_MAP  = {
        "GOAL":     "has_goal",
        "AUDIENCE": "has_audience",
        "PRODUCT":  "has_product",
        "TIMELINE": "has_timeline",
    }
    missing       = [lbl for lbl in _PRIORITY if not validation[_KEY_MAP[lbl]]]

    # Hard block: "both" is not a valid audience answer. Intercept before Claude.
    _is_dual_motion = _DUAL_MOTION_RE.search(brief) or (
        not validation["has_audience"] and re.search(r'\bboth\b', brief, re.IGNORECASE)
    )
    if _is_dual_motion and (not missing or missing[0] == "AUDIENCE"):
        return {
            "proceed": False,
            "question": (
                "A campaign requires a single motion: channel selection, messaging tone, "
                "and asset plan all depend on it. "
                "Is this campaign targeting existing Docebo customers or net-new accounts?"
            ),
            "reasoning": (
                "Running two motions simultaneously creates an ambiguous campaign that "
                "cannot be executed. The system needs one clear answer to determine "
                "the channel mix, conversion event, and messaging tone."
            ),
        }

    ask_directive = f"Ask ONLY about the missing element: {missing[0]}"

    validation_block = (
        f"VALIDATION RESULT: has_product={validation['has_product']}, "
        f"has_goal={validation['has_goal']}, has_timeline={validation['has_timeline']}, "
        f"has_audience={validation['has_audience']}\n"
        f"CONFIRMED MISSING: {', '.join(missing)}\n"
        f"{ask_directive}"
    )

    r = client.messages.create(
        model=MODEL,
        max_tokens=600,
        temperature=0,
        system=_load_prompt("intake_agent.txt"),
        messages=[{
            "role": "user",
            "content": f"{validation_block}\n\n---\n\nBRIEF:\n{brief}",
        }],
    )
    result = parse_json(r.content[0].text)

    # Hard guarantee: if Claude decides to proceed, replace its enriched_brief with
    # the deterministic version so goal/timeline/product/audience are never rephrased.
    if result.get("proceed"):
        result["enriched_brief"] = _auto_enrich(brief, ctx, _truncated)
    return result
