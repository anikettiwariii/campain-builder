# Docebo Campaign Builder Agent — System Documentation

**Version:** Crawl (Walk target: Q3 2026)  
**Last updated:** June 2026  
**Maintainer:** Aniket Tiwari

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Technology Stack](#2-technology-stack)
3. [Marketing Brain Architecture](#3-marketing-brain-architecture)
4. [Neo4j Data Layer](#4-neo4j-data-layer)
5. [Brief Intake and Validation](#5-brief-intake-and-validation)
6. [Graph Traversal and Campaign Structure Resolution](#6-graph-traversal-and-campaign-structure-resolution)
7. [Evidence Classification System](#7-evidence-classification-system)
8. [Campaign Readiness Scoring](#8-campaign-readiness-scoring)
9. [Assumption Confidence System](#9-assumption-confidence-system)
10. [Probabilistic Channel Weights](#10-probabilistic-channel-weights)
11. [The Two-Layer Prompt Architecture](#11-the-two-layer-prompt-architecture)
12. [The Four-Step Claude Pipeline](#12-the-four-step-claude-pipeline)
13. [Evidence-Gated Output Sizing](#13-evidence-gated-output-sizing)
14. [Hypothesis Mode and Output Honesty](#14-hypothesis-mode-and-output-honesty)
15. [Determinism and Consistency](#15-determinism-and-consistency)
16. [Campaign Persistence and Pipeline Management](#16-campaign-persistence-and-pipeline-management)
17. [Asana Integration](#17-asana-integration)
18. [PPTX Generation](#18-pptx-generation)
19. [Known Limitations and Walk Version Upgrades](#19-known-limitations-and-walk-version-upgrades)
20. [Architecture Decisions and Rationale](#20-architecture-decisions-and-rationale)

---

## 1. System Overview

The Docebo Campaign Builder Agent is an AI-assisted B2B marketing campaign system. A marketer submits a plain-text brief describing a Docebo product, a campaign goal, a target audience, and a timeline. The system returns a fully structured campaign — messaging pillars, an asset plan, a phased rollout with Asana-ready tasks, a readiness score, and a downloadable PowerPoint kickoff deck.

The design premise is that an LLM alone is insufficient for brand-safe campaign generation. Claude supplies language; the knowledge graph supplies facts; Python enforces structural rules. The three layers are intentionally kept separate. Claude is never asked to recall Docebo-specific statistics from training data, infer firmographics from personas, or make structural decisions the graph should make.

The system operates in a **crawl stage** — Marketing Brain coverage is limited to 8 Docebo products, 3 personas, 6 pain points, 6 channels, and 11 curated proof points, connected by 95 edges across 34 nodes in a live Neo4j graph. The walk version will extend to Salesforce signal integration, real campaign performance feedback loops, and A/B copy variants grounded in real HubSpot performance data..

**Entry point:** `app.py` (Streamlit). All pipeline modules are imported through `main.py`.

**Pipeline sequence:**
```
Brief → Intake validation → Graph traversal → Brief parsing → Messaging generation
     → Rollout generation → Readiness scoring → Deck distillation → PPTX render
```

---

## 2. Technology Stack

| Layer | Technology | Purpose |
|---|---|---|
| UI | Streamlit | Single-page app, sidebar navigation, campaign output rendering |
| AI | Anthropic Claude (Sonnet 4.6, Haiku 4.5) | Messaging generation, brief parsing, rollout generation |
| Graph (primary) | Neo4j (live, `bolt://localhost:7687`) | Knowledge graph with Cypher-based edge traversal |
| Graph (fallback) | NetworkX + Python | In-process DiGraph, used if Neo4j is unreachable |
| Persistence | SQLite (`campaigns.db`) | Campaign storage, per-user history |
| PPTX generation | Node.js + pptxgenjs | 6-slide campaign kickoff deck |
| PDF/PNG preview | Spire.Presentation + PyMuPDF | PPTX → PDF → PNG for slide previews in UI |
| Project management | Asana API (REST) | Auto-creates Asana projects and tasks from rollout output |
| Fonts | Inter + IBM Plex Mono (Google Fonts) | UI typography |
| Auth | Hardcoded credential dict (`auth.py`) | Single-user access gate (crawl stage) |

**Claude model allocation:**
- `claude-sonnet-4-6` — messaging generation (step 2), requires marketing voice quality
- `claude-haiku-4-5-20251001` — brief parsing (step 1), rollout generation (step 3), deck distillation (step 4): deterministic extraction tasks where speed matters more than prose quality

**Node.js usage is narrow:** `generate_deck.js` is invoked as a subprocess by `deck_builder.py` via `subprocess.run(["node", "generate_deck.js", input_path, output_path])`. The Python layer writes a JSON file; Node reads it and writes the PPTX.

---

## 3. Marketing Brain Architecture

The Marketing Brain is a JSON knowledge graph stored at `knowledge/marketing_brain.json`. It is the single source of truth for all campaign facts — personas, pains, products, channels, proof points, brand voice, and company context.

**Top-level structure:**
```
marketing_brain.json
├── meta              — version, stage, last_updated
├── personas          — buyer roles with pain_ids, motivations, language, objections
├── pains             — pain nodes with severity, customer_quotes, stat, docebo_solution
├── products          — product nodes with positioning, proof_points, email_examples, icp, persona_ids, pain_ids
├── competitors       — competitor nodes with docebo_wins, docebo_loses, displacement_message
├── channels          — channel nodes with owner, best_for_personas, use_case, docebo_approach
├── proof_points      — evidence nodes with claim, source, use_for_products, use_for_pains, use_for_personas
├── notable_customers — flat list of customer names
├── company_facts     — founding year, headquarters, Nasdaq/TSX listing, customer count, acquisitions
├── marketing_team    — team responsibilities (demand gen, PMM, paid social, etc.)
├── tech_stack        — CRM, marketing automation, AI SDR, call intelligence, etc.
├── relationships     — explicit edge list duplicating key graph edges for readability
├── brand_voice       — manifesto, tone, enemy, never_say, always_say, CEO quote
└── company_context   — ARR, NPS, product suite, campaign performance benchmarks
```

**Products in crawl stage (8):** AgentHub, Skills Intelligence, Enterprise Knowledge, Advanced Analytics, Headless Learning, Roleplay, Content Creator, Harmony AI.

**Personas (3):** VP of Learning & Development, Chief People Officer, L&D Program Manager.

**Proof points (11):** A mix of named-customer case studies (Bethany Care Society, MidFirst Bank, Disguise) and platform statistics (80% admin cost reduction, 79% personalization gap, 85% AI readiness, 59% skills performance, etc.).

**Evidence tiers:** Named-customer case studies (highest) → platform stats with attribution → no evidence (placeholder required). Only named-customer case studies with a `VALIDATES_PRODUCT` graph edge satisfy the "verified" proof requirement. Platform stats satisfy "stat" status but not "verified".

`graph_builder.py` loads this JSON once and builds a NetworkX `DiGraph` (module-level singleton). `graph_query.py` routes queries to Neo4j first, falls back to NetworkX if Neo4j is unavailable.

---

## 4. Neo4j Data Layer

The system runs against a live Neo4j instance (`bolt://localhost:7687`, credentials in `.env`). `graph_query.py` routes every brief to Neo4j first; if the connection is unavailable it falls back to the NetworkX in-memory graph silently so the pipeline never hard-fails.

**Node types:** `Product`, `Persona`, `Pain`, `Channel`, `ProofPoint`

**Edge types:**
| Edge | Direction | Meaning |
|---|---|---|
| `SOLVES_PAIN` | Product → Pain | This product addresses this pain |
| `TARGETS_PERSONA` | Product → Persona | This product's primary audience |
| `REACHES_PERSONA` | Channel → Persona | This channel reaches this persona |
| `EXPERIENCES_PAIN` | Persona → Pain | This persona commonly experiences this pain |
| `VALIDATES_PRODUCT` | ProofPoint → Product | This evidence validates this product claim |
| `SUPPORTS_PAIN` | ProofPoint → Pain | This evidence illustrates this pain |
| `RESONATES_WITH` | ProofPoint → Persona | This evidence resonates with this persona |

**Availability detection:** `knowledge/neo4j_connection.py` exposes `is_available()` and `get_driver()`. `graph_query.py` wraps this in a try/except — any connection failure falls back to `_query_graph_networkx()` silently.

**Proof-point validation against Neo4j:** `campaign_builder.py:_pillar_proof_is_live()` runs a Cypher query to verify that a named customer proof point has a `VALIDATES_PRODUCT` edge to the matched product. This is the authoritative check — it prevents a Bethany Care Society case study (healthcare) from appearing on an AgentHub (AI agent) pillar even if the text was used by the LLM.

**Loading the graph into Neo4j:** `neo4j_loader.py` batch-imports all `marketing_brain.json` nodes and edges using the same relationship types listed above. `neo4j_query.py` mirrors the NetworkX traversal logic step-by-step so both backends return the same dict shape.

---

## 5. Brief Intake and Validation

Before running the four-step pipeline, every brief goes through a two-stage gate: a deterministic pre-validation (`is_valid_docebo_brief`) and a smart inference layer (`validate_brief` / `intake_agent`).

### Stage 1 — Pre-validation (no API call)

`agents/intake_agent.py:is_valid_docebo_brief()` checks whether the brief is on-domain:
- If fewer than 3 words: reject with a guidance message
- If any Docebo product name appears: immediately valid
- If L&D / HR / learning keywords appear: valid
- If B2B SaaS keywords appear: valid
- If off-brand keywords appear (Porsche, Nike, restaurant, etc.): reject with specific feedback
- Otherwise: pass through (the intake agent will ask for missing details)

This gate prevents wasted Claude API calls on clearly off-domain submissions.

### Stage 2 — Signal extraction (deterministic, no API call)

`validate_brief()` extracts four signals from the brief text using regex:

| Signal | Detection method |
|---|---|
| `has_product` | Substring match against full list of Docebo product names and aliases |
| `has_goal` | `_GOAL_RE`: number + concrete outcome word (demo, sign-up, lead, MQL, etc.) |
| `has_timeline` | `_TIMELINE_NUMERIC_RE` ("8 weeks") OR `_TIMELINE_FUZZY_RE` ("one month", "next quarter") |
| `has_audience` | `_AUDIENCE_RE` (explicit motion keyword) OR expansion-only product detected |

### Stage 3 — Auto-enrichment or clarifying question

If all four signals are present, `_auto_enrich()` builds an enriched brief by appending:
- Matched persona title and top motivations (from graph traversal)
- Primary pain label and description (from graph traversal)
- Product positioning statement (from graph node)
- TIMELINE CONSTRAINT block (for day-based timelines with exact ceiling)
- Campaign motion framing (expansion vs. net-new, expansion-only products force expansion motion)
- AgentHub pre-launch timing note (when AgentHub is detected)

If a signal is missing, a single Claude call (Haiku, temperature=0) asks exactly one question targeting the highest-priority gap (PRODUCT > GOAL > AUDIENCE > TIMELINE). The question is asked only once — after two exchanges the system forces `force_proceed=True` and enriches with best inference.

**Brief sanitization:** Control characters are stripped, newlines collapsed to spaces, input truncated to 1000 characters. Truncation is flagged in the enriched brief.

---

## 6. Graph Traversal and Campaign Structure Resolution

`knowledge/graph_query.py:query_graph()` returns the context dict that all four pipeline steps receive. The traversal follows a fixed 6-step pattern:

**Step 1 — Seed detection**
Products are detected by: exact name match, `also_known_as` aliases, or keyword signals (`_PRODUCT_SIGNALS`). Personas are detected by: exact title match, `also_known_as` aliases, or keyword signals (`_PERSONA_SIGNALS`).

**Step 2 — Persona expansion**
For each matched product, traverse `TARGETS_PERSONA` edges to add any personas the product targets that weren't directly detected. If still no personas, fall back to first two personas in graph (safety net, not expected in production).

**Step 3 — Pain expansion**
Traverse `SOLVES_PAIN` edges from matched products (product pains take priority). Add `EXPERIENCES_PAIN` edges from matched personas as secondary. Brief-detected pain keywords (`_PAIN_KW`) augment but don't override graph paths.

**Step 4 — Channel expansion**
Traverse reverse `REACHES_PERSONA` edges (predecessors) from matched personas to find which channels reach them. All channels that reach at least one matched persona are included.

**Step 5 — Proof point scoring and filtering**
Each `ProofPoint` node is scored:
- +3 if `VALIDATES_PRODUCT` edge to a matched product
- +2 if `SUPPORTS_PAIN` edge to a matched pain
- +1 if `RESONATES_WITH` edge to a matched persona
- +1 bonus if score ≥ 2 AND proof contains a named customer name

Named-customer proof points are only included if they have at least one relevant edge (product or pain). Platform-stat proof points are included if they resonate with a matched persona. Top 6 by score are returned.

**Step 6 — Output assembly**
Returns a flat context dict:
```python
{
    "matched_products":  [...],   # full product JSON objects
    "matched_personas":  [...],   # full persona JSON objects
    "matched_pains":     [...],   # full pain JSON objects
    "matched_channels":  [...],   # full channel JSON objects
    "proof_points":      [...],   # top-6 scored proof points
    "brand_voice":       {...},   # graph-level attribute
    "company_context":   {...},   # graph-level attribute
    "meta":              {...},   # graph-level attribute
    "_matched_node_ids": {...},   # IDs for sidebar visualization and proof validation
}
```

This dict is the canonical handoff between the knowledge layer and the pipeline. Every downstream step reads from it — no step re-queries the graph.

---

## 7. Evidence Classification System

Evidence quality is a first-class constraint in the system, not an afterthought. Three tiers:

**Tier 1 — Verified named customer (`proof_status: "verified"`):**
The proof point text contains a named customer from the approved list (Bethany Care Society, MidFirst Bank, Disguise, SNCF, Société Générale, Segula Technologies) AND a `VALIDATES_PRODUCT` edge exists in the graph linking that proof point to the matched product. Both conditions are required. A Bethany Care Society case study does not count as verified evidence for AgentHub just because it mentions admin savings.

Verified by `campaign_builder.py:_pillar_proof_is_live()`, which queries Neo4j first (Cypher: MATCH ProofPoint -[VALIDATES_PRODUCT]→ Product) and falls back to NetworkX edge traversal.

**Tier 2 — Platform stat (`proof_status: "stat"`):**
The proof point is a verified statistic with attribution (e.g., "79% of employees say learning is not personalized — Docebo AI Readiness Gap Report 2026") but names no specific customer outcome. These are approved for use but carry a "(platform stat — verify before use)" annotation in the messaging prompt.

**Tier 3 — Placeholder (`proof_status: "needed"`):**
No qualifying evidence exists for this pillar's product/pain combination. The system writes the exact placeholder:
```
[PROOF POINT NEEDED — no [Product]-specific case study in Marketing Brain.]
```

Placeholder propagation is enforced in Python by `_apply_proof_point_placeholders()` after the LLM call. Even if Claude writes a plausible-sounding proof point, the post-call check replaces it with the placeholder if it doesn't pass `_pillar_proof_is_live()`.

**Evidence audiencefit rule:** Named customers are matched to audience context. Bethany Care Society (healthcare/seniors care) is not used for financial services campaigns even if it's in the graph. MidFirst Bank (financial services) is appropriate there. When no industry-matched customer exists, platform stats are used. This is enforced by instructions in `system_context.txt` and the messaging prompt, not by Python code (walk version target: graph edge for industry matching).

---

## 8. Campaign Readiness Scoring

`campaign_builder.py:compute_readiness_score()` computes a two-dimensional readiness score after the messaging step. It is deterministic Python — no Claude call.

### Structure Score (0–100 pts)

Measures what the marketer provided and what the graph could match:

| Criterion | Points | Satisfied when |
|---|---|---|
| Product identified | 25 | `ctx["matched_products"]` is non-empty |
| Persona identified | 20 | `ctx["matched_personas"]` is non-empty |
| Goal explicitly stated | 20 | `_EXPLICIT_GOAL_RE` matches in the raw brief text |
| Timeline explicitly stated | 15 | `_EXPLICIT_TIMELINE_RE` matches in the raw brief text |
| Pain mapped from graph | 10 | `ctx["matched_pains"]` is non-empty |
| Channels validated from graph | 10 | `ctx["matched_channels"]` is non-empty |

Goal and timeline points require the values to appear in the **original brief text** — graph inference does not earn these points. A goal of "20 early access sign-ups" earns 20 pts; a goal inferred from product positioning earns 0.

### Evidence Score (0–100 pts)

Measures what the Marketing Brain can substantiate:

| Criterion | Points | Satisfied when |
|---|---|---|
| Named customer case study | 50 | Any proof point in `ctx["proof_points"]` contains a named customer name |
| Verified benchmark in graph | 25 | Any proof point contains a percentage (e.g., "80%") |
| ICP firmographics in brief | 25 | `_ICP_FIRM_BRIEF_RE` matches in the raw brief (company size, industry, team size) |

### Combined Readiness

```
Readiness = round(Structure × Evidence / 100)
```

This multiplicative formula means both dimensions must be strong for a high score. A perfectly structured brief with no evidence gives 0 readiness. Evidence without structure also depresses the score.

### Status Buckets (4 states)

| Status | Condition |
|---|---|
| Execution Ready | Structure ≥ 70 AND Evidence ≥ 70 |
| Hypothesis | Structure ≥ 70, Evidence < 70 |
| Incomplete | Structure < 70, Evidence ≥ 70 |
| Blocked | Both < 70 |

"Hypothesis" is the most common status — a well-formed brief lacking a named case study for the product.

### Score injection into deck

The readiness dict is computed in `app.py` **before** `distill_for_deck()` is called. It is passed as `readiness=` to `distill_for_deck()`, which injects `readiness_score`, `structure_score`, `evidence_score`, and `status_label` into `meta` and `status_slide` of the deck JSON. The LLM never computes or derives these numbers — it copies them from meta.

---

## 9. Assumption Confidence System

The system tracks which elements of each campaign came from the explicit brief versus system inference. This is surfaced in the UI as an "Inference Load" indicator and a field-by-field classification table.

### Inference Load Levels

Computed by counting how many of the five tracked elements are inferred rather than provided:

| Load | Inferred count |
|---|---|
| Low | 0–2 |
| Medium | 3–4 |
| High | 5+ |

### Tracked elements

| Element | Source classification logic |
|---|---|
| Product | "VERIFIED" if a significant word from the matched product name appears in the brief |
| Goal | "VERIFIED" if `_EXPLICIT_GOAL_RE` matches in brief |
| Timeline | "VERIFIED" if `_EXPLICIT_TIMELINE_RE` matches in brief |
| Audience | "VERIFIED" if `_AUDIENCE_SIGNAL_RE` matches in brief |
| ICP details | "VERIFIED" if `_ICP_FIRM_BRIEF_RE` matches in brief |
| Persona selection | Always "INFERRED" (graph edge traversal) |
| Campaign motion | Always "INFERRED" (product-to-motion mapping) |
| Channels | Always "INFERRED" (graph edge traversal) |

Three elements (persona selection, campaign motion, channels) are always "INFERRED" regardless of brief content because they come from graph edge traversal, not explicit marketer input. This is intentional — the graph makes these structural decisions so marketers don't need to specify them.

### Why this matters

High inference load campaigns should prompt the marketer to provide more specifics before running the campaign. A campaign with product, goal, timeline, and audience all inferred is structurally weaker — the graph is doing too much guessing about what the marketer actually wants.

---

## 10. Probabilistic Channel Weights

Channel selection is fully deterministic — channels are selected by graph traversal (`REACHES_PERSONA` edges), not by LLM inference. The "weights" refer to the order in which channels appear in the matched_channels list, which determines asset plan ordering.

### Channel-to-asset lock map

`campaign_builder.py:_CHANNEL_ASSET_MAP` defines the structural relationship between each channel and its canonical asset:

| Channel | Asset type | Format | Owner |
|---|---|---|---|
| `hubspot_email` | Email nurture sequence | 5-touch HubSpot sequence | Demand Gen |
| `linkedin_sponsored` | LinkedIn ad copy | 3 LinkedIn sponsored variants | Paid Social |
| `in_product` | In-product banner | In-app modal, 2 variants | Product Marketing |
| `webinar` | Webinar deck | Live webinar slide deck | Field Marketing |
| `qualified_outbound` | Outbound sequence | 3-touch SDR/BDR sequence | SDR/BDR |
| `customer_success_outreach` | CSM leave-behind | 1-page PDF leave-behind | Customer Success |

`_build_locked_assets()` generates the asset list pre-LLM by iterating matched channels in graph order, deduplicating by owner (no two assets can have the same owner). Claude is then instructed to write `purpose` only — asset type, format, and owner are already fixed.

`_enforce_locked_assets()` overwrites the LLM's asset list post-call to enforce the locked structure. Claude's purpose text is preserved; everything else is replaced.

This prevents common LLM failures: duplicate owners, invented formats, wrong channel owners, assets for channels that weren't selected by the graph.

---

## 11. The Two-Layer Prompt Architecture

Every Claude call in the pipeline uses a two-part system prompt assembled by `_load_prompt()`:

```python
def _load_prompt(name: str) -> str:
    ctx  = open("prompts/system_context.txt").read()
    role = open(f"prompts/{name}").read()
    return f"{ctx}\n\n{role}"
```

**Layer 1 — `system_context.txt` (always present):**
Defines the agent's two operating modes and their hard constraints:
- **Mode 1 (GTM Reasoning Engine):** Governs all structural decisions. FORBIDS inferring firmographics, campaign motion, timeline, or ICP from persona nodes or general marketing knowledge. Only values from the explicit brief or graph traversal are permitted.
- **Mode 2 (B2B Marketing Voice):** Permits marketing language, narrative framing, and buyer language. Active only in messaging sections. FORBIDS introducing new structural assumptions.

Mode 2 cannot override Mode 1. If Mode 1 left a field as NULL, Mode 2 cannot fill it.

**Layer 2 — Role-specific prompt (varies by pipeline step):**
- `brief_parser.txt` — extraction rules, ICP constraints, output schema
- `messaging_generator.txt` — positioning, pillar, CTA, and asset generation rules
- `rollout_generator.txt` — phase structure, task format, metric sourcing, checkpoint format
- `deck_distiller.txt` — JSON extraction rules for slide-ready output
- `intake_agent.txt` — validation result format and question-asking protocol

**Brand voice enforcement:** `system_context.txt` includes hard lists of forbidden phrases ("leading platform", "robust solution", "leverage", "empower", "seamlessly integrates") and required vocabulary ("foundational", "readiness", "capability", "proof not anecdotes"). These apply to every call.

**Verified named customers:** `system_context.txt` names exactly which customers may be cited and what they proved. Any customer name not in this list is forbidden, even if it appears in the marketing_brain.json (defense-in-depth: the graph is the primary gate, but the prompt adds an explicit guardrail).

---

## 12. The Four-Step Claude Pipeline

The campaign generation pipeline has four sequential steps, each a separate Claude call. All steps receive the same `ctx` dict from graph traversal.

### Step 1 — Brief parsing (`extract_brief_structure`)
**Model:** Haiku 4.5 | **Temperature:** 0.0 | **Max tokens:** 500

Input: enriched brief text + knowledge graph context (personas, pains only)  
Output: structured JSON with `icp`, `pain_point`, `product_angle`, `campaign_goal`, `tone`, `timeline_weeks`

ICP is extracted verbatim from the brief — persona node properties are explicitly excluded from context for this step to prevent firmographic hallucination. If no audience was stated, ICP is an empty string, not a guess.

Goal and timeline are parsed to numeric values. The goal is a `{metric, target, timeframe_days}` object.

### Step 2 — Messaging generation (`generate_messaging`)
**Model:** Sonnet 4.6 | **Temperature:** 0.1 | **Max tokens:** 2000

Input: brief structure (Step 1) + full knowledge graph context (all node types) + locked asset plan + locked proof point assignment  
Output: `positioning_statement`, `pillars[]`, `cta_by_persona`, `asset_plan[]`

Pre-call: `_build_locked_assets()` builds the deterministic asset list by channel traversal.  
Post-call:
1. `_enforce_locked_assets()` — overwrites asset structure, preserves purpose text
2. `_apply_proof_point_placeholders()` — replaces any pillar proof point that fails the named-customer + product-edge check
3. `_compute_data_gaps()` — generates human-readable gap list (missing case study, no email examples, no ICP)
4. `_cap_cta_to_generic()` — Rule 1: if no ICP in brief, collapse CTAs to one generic entry
5. `_cap_assets_if_no_named_proof()` — Rule 2: no named-customer proof → cap asset plan at 3
6. `_enforce_asset_word_limits()` — Rule 6: trim asset purpose fields to 12 words

### Step 3 — Rollout generation (`generate_rollout`)
**Model:** Haiku 4.5 | **Temperature:** 0.0 | **Max tokens:** 3500

Input: condensed campaign structure + matched channels context  
Output: `phases[]`, `success_metrics[]`, `human_review_checkpoints[]`

A condensed input dict is built (not the full JSON) to reduce token usage: ICP, goal, timeline, tone, pillar titles, asset types, asset owners.

Post-call enforcement chain:
1. `_correct_task_owners()` — sync task owner field with team name in task description
2. `_strip_placeholder_tasks()` — remove any task whose description contains a placeholder (LLM should not have done this; defense-in-depth)
3. `_cap_rollout_days()` — clamp any task due_day beyond the timeline ceiling
4. `_cap_phases_if_no_named_proof()` — Rule 3: no named-customer proof → cap at 2 phases
5. `_enforce_task_asset_alignment()` — drop tasks whose owner has no corresponding asset in plan
6. `_enforce_checkpoint_dependencies()` — Checkpoint 1 day = max Phase 1 task day + 1; normalize checkpoint text
7. `_flag_metric_benchmarks()` — replace dollar amounts in metrics 2–4 with placeholder
8. `_flag_invented_numbers()` — replace percentages and count benchmarks in metrics 2–4 with placeholder
9. `_collapse_benchmark_placeholders()` — collapse long LLM [BENCHMARK NEEDED...] text to short canonical form
10. `_enforce_word_limits()` — hard-trim tasks (15w), milestones (10w), metrics (15w)
11. `_inject_blocker_tasks()` — insert Customer Marketing [BLOCKER] tasks in Phase 1 for missing case studies

### Step 4 — Deck distillation (`distill_for_deck`)
**Model:** Haiku 4.5 | **Temperature:** 0.3 | **Max tokens:** 2000

Input: structure + messaging + rollout dicts + today's date + schema  
Output: `deck_content` JSON (7 sections)

Readiness scores are injected post-call by Python — the LLM copies meta values it was told to copy, Python overwrites them with the authoritative computed values.

### Pipeline timing

`PIPELINE_TIMINGS` dict in `campaign_builder.py` is populated by each step using `time.perf_counter()`. The UI displays per-step timing in seconds. Typical end-to-end time: 15–35 seconds depending on Sonnet response length.

---

## 13. Evidence-Gated Output Sizing

The system applies calibration rules that reduce output size when evidence quality is low. These rules are deterministic Python enforced after each LLM call.

| Rule | Trigger | Effect |
|---|---|---|
| Rule 1: Generic CTA | No ICP in brief | Collapse `cta_by_persona` from 3 persona-specific entries to 1 generic entry |
| Rule 2: Asset cap | No named-customer proof point in any pillar | Cap `asset_plan` at 3 assets (down from up to 6) |
| Rule 3: Phase cap | No named-customer proof point in any pillar | Cap rollout at 2 phases (down from 3) |
| Rule 5: Benchmark gate | Any % or count in metrics 2–4; any $ in any metric | Replace with "Benchmark unavailable — request from Marketing Ops." |
| Rule 6: Word limits | Always | Task ≤ 15w, milestone ≤ 10w, asset purpose ≤ 12w, metric ≤ 15w |

The rationale: a campaign without a named case study has lower credibility and should not pretend to full execution readiness. Showing 3 assets and 2 phases instead of 6 assets and 3 phases makes the gap visible rather than hiding it behind apparent completeness.

**Blocker task injection** is a companion mechanism: when pillars have placeholder proof points, a `[BLOCKER]` task is inserted into Phase 1 for Customer Marketing, making the case study gap actionable in Asana.

---

## 14. Hypothesis Mode and Output Honesty

"Hypothesis" is both a readiness status (Structure ≥ 70, Evidence < 70) and a design philosophy. When a campaign lacks evidence for one or more pillars, the system does not silently generate plausible-sounding content to fill the gap. Instead:

1. **Pillar proof points** show the exact placeholder: `[PROOF POINT NEEDED — no [Product]-specific case study in Marketing Brain.]`
2. **One-liners** are left blank or minimal when proof_status is "needed" — the messaging prompt forbids assertive body copy for evidence-gap pillars
3. **Evidence gaps** are surfaced on slide 2 of the deck as a visible amber callout
4. **Data gaps list** is displayed in the UI below the campaign output, listing exactly what is missing and who to ask
5. **Readiness score** on slide 2 is low precisely because evidence is low — the number makes the hypothesis status numerically legible

This approach treats absent evidence as a structural constraint that propagates forward, not a gap to paper over. A campaign with `[PROOF POINT NEEDED]` is more honest and more useful than one with an invented customer quote.

---

## 15. Determinism and Consistency

Multiple mechanisms ensure the pipeline produces consistent, reproducible outputs for the same brief:

**Temperature settings:**
- Brief parsing: 0.0 (fully deterministic)
- Rollout generation: 0.0 (fully deterministic)
- Messaging generation: 0.1 (near-deterministic; minimal variation in word choice)
- Deck distillation: 0.3 (extraction task, some variation in compression style acceptable)

**Python post-processing overrides LLM drift:**
- Asset structure is overwritten deterministically from `_CHANNEL_ASSET_MAP`
- Proof points that fail validation are replaced with the exact placeholder regardless of what Claude wrote
- Benchmark numbers in metrics 2–4 are replaced regardless of what Claude wrote
- Checkpoint day numbers are computed and overwritten by Python, not trusted from LLM output
- Word limits are enforced by trim functions, not by prompt compliance alone

**Graph traversal is deterministic:** The same brief always produces the same context dict (given the same graph). No randomness in node selection, edge traversal, or proof point scoring.

**Readiness scores are deterministic:** `compute_readiness_score()` uses regex matching against the raw brief text. Same brief always produces the same scores.

**What varies between runs:** Positioning statement wording, pillar one-liner wording, CTA phrasing (all at temperature 0.1). The structure, asset plan, owners, proof points, and readiness scores are identical.

---

## 16. Campaign Persistence and Pipeline Management

`db.py` manages a SQLite database (`campaigns.db`) at the project root.

### Schema

```sql
campaigns (
    id                  TEXT PRIMARY KEY,     -- UUID
    username            TEXT NOT NULL,
    created_at          TEXT NOT NULL,        -- ISO 8601 UTC
    brief_text          TEXT,
    product             TEXT,
    goal                TEXT,
    timeline            TEXT,
    primary_persona     TEXT,
    status              TEXT DEFAULT 'Draft',
    confidence_grounded TEXT,                 -- JSON list
    confidence_missing  TEXT,                 -- JSON list
    full_campaign_json  TEXT,                 -- full pipeline output as JSON
    asana_url           TEXT
)
```

`full_campaign_json` stores the complete campaign output including `structure`, `messaging`, `rollout`, `knowledge_context`, `deck_content`, `readiness_*` scores, and the `ctx` dict. This makes the entire campaign state reproducible without re-running the pipeline.

### Status lifecycle

Status is derived from readiness scores, not set by UI buttons (for campaigns with readiness data). `_derive_status()` in `db.py` reads `readiness_structure_score` and `readiness_evidence_score` from `full_campaign_json` and applies the same 4-bucket formula as `compute_readiness_score()`. This ensures sidebar status labels are always in sync with the score.

Legacy campaigns saved before the structure/evidence split fall back to `readiness_score` with a 3-bucket formula (Blocked < 30, Hypothesis < 60, Execution Ready ≥ 60).

### Pipeline summary

`pipeline_summary(username)` returns `(total, execution_ready, blocked)` by iterating all rows and calling `_derive_status()` on each — no stored denormalized counters. The sidebar displays these counts.

### Backfill

`backfill_readiness_scores()` recomputes `readiness_structure_score` and `readiness_evidence_score` for legacy campaigns that lack them, using stored `ctx` and `brief_text`. This runs without API calls — it uses only Python regex and graph data already in the database.

### Campaign re-loading

The sidebar lists all campaigns for the logged-in user ordered by `created_at DESC`. Clicking a campaign calls `_db_get_one()`, deserializes `full_campaign_json`, and restores the full campaign state to Streamlit session state without re-running any Claude calls.

---

## 17. Asana Integration

`builders/asana_builder.py:push_to_asana()` creates an Asana project from a campaign output dict. It uses Asana's REST API directly via `urllib.request` (no SDK dependency).

### Project naming

`_project_name()` builds: `[Product] — [Target] [Metric 2 words] — [N] Weeks` (max 60 chars).  
Example: `AgentHub — 20 Early Access — 4 Weeks`

### Structure created

1. **Project** — created first, dark-purple color, list view
2. **Sections** — one per phase, in order, then "Success Metrics" and "Human Review Checkpoints"
3. **Tasks** — all tasks across all sections created in parallel (3-worker ThreadPoolExecutor)

### Task name format

```
{Owner} — {Task description} · {Due date}
```

Example: `Demand Gen — Build 5-email HubSpot sequence to VP L&D · Jun 22`

The due date is embedded directly in the task name as `"Jun 22"` (not "Wednesday") to bypass Asana's week-relative date display behavior. Asana renders ISO dates within the current week as day names ("Wednesday") — embedding the formatted date in the name ensures consistent display regardless of when the task is viewed.

### Due date calculation

`due_dt = date.today() + timedelta(days=task["due_day"])` where `due_day` is the integer from the rollout output. The Asana task `due_on` field receives the ISO date; the task name receives the `"Jun 22"` format.

### Rate limiting

A 50ms sleep between sequential API calls (`_DELAY = 0.05`) prevents hitting Asana's rate limits. Task creation is parallelized (3 workers) because task creation is the highest-volume operation and Asana's per-project rate limit is generous.

### Checkpoint owner detection

`_checkpoint_owner()` scans the checkpoint text for team names, longest-first (to match "Demand Generation" before "Demand Gen"). Used to populate the task `notes` field with the responsible owner.

---

## 18. PPTX Generation

Campaign kickoff decks are 6-slide PowerPoint files generated by `generate_deck.js` (Node.js + pptxgenjs) and triggered from `deck_builder.py:distill_for_deck()`.

### Generation flow

1. `distill_for_deck()` in Python calls Claude (Haiku) to compress the campaign output into `deck_content` JSON
2. Python injects readiness scores into the JSON post-call
3. Python writes `deck_content` to a temp JSON file
4. Python calls `subprocess.run(["node", "generate_deck.js", input_path, output_path])`
5. Node reads the JSON, builds slides, writes the PPTX file
6. Python reads the PPTX bytes for download button and slide preview

### Slide layout

Uses `pres.layout = "LAYOUT_16x9"` (10" × 5.625" canvas).

| Slide | Name | Key content |
|---|---|---|
| 1 | Title | Product name (44pt), goal, date, summary stat cards |
| 2 | Campaign Status | Readiness score, Structure/Evidence subscores, status label, evidence gaps, ICP/motion/goal/timeline |
| 3 | Messaging Pillars | Positioning statement, 3 pillars (title, one-liner, proof, proof_status coloring) |
| 4 | Asset Plan | Asset count, evidence note, up to 8 assets in table |
| 5 | Phased Rollout | Up to 3 phases (name, days, milestone, tasks, checkpoint) |
| 6 | Success Metrics | Up to 4 metrics, up to 3 governance checkpoints |

### Brand colors

| Constant | Hex | Use |
|---|---|---|
| `BLUE` | `1B3C87` | Slide 2 header, readiness score number |
| `PURPLE` | `7C3FA8` | Slide 1 header bar, campaign kickoff label |
| `WHITE` | `FFFFFF` | Slide 2 background, text on dark backgrounds |
| `LIGHT_GREY` | `F4F5F7` | Readiness card background |
| `MID_GREY` | `8C93A0` | Secondary labels, metadata |
| `DARK` | `1A1A2E` | Slide 1 background |
| `AMBER` | `D97706` | "Hypothesis" status, Evidence subscore, evidence gaps box |
| `GREEN` | `059669` | Structure subscore, verified pillar cards |

### Slide preview rendering

Slide previews in the UI use a two-step conversion:
1. `spire.presentation` (pip package) converts PPTX → PDF
2. `pymupdf` (fitz) rasterizes each PDF page to a PNG at 2.5× scale (effectively 1800×1013 px)

No LibreOffice, ImageMagick, or Ghostscript required.

### `deck_content` JSON schema

The canonical data contract between Python and Node.js:

```json
{
  "meta": { "date", "product", "readiness_score", "structure_score", "evidence_score", "status_label", "evidence_gaps" },
  "title_slide": { "product", "goal", "timeline", "motion" },
  "status_slide": { "icp", "motion", "goal", "timeline", "readiness_score", "structure_score", "evidence_score", "status_label", "evidence_gaps" },
  "pillars_slide": { "positioning", "pillars": [{ "title", "one_liner", "proof_status", "proof" }] },
  "asset_slide": { "asset_count", "evidence_note", "assets": [{ "name", "format", "owner", "purpose" }] },
  "rollout_slide": { "phases": [{ "name", "days", "milestone", "tasks", "checkpoint" }] },
  "metrics_slide": { "metrics": [{ "label", "value", "verified" }], "checkpoints": [{ "day", "teams", "action" }] }
}
```

Readiness scores live in both `meta` (for `dateStr` and title slide) and `status_slide` (for the readiness card). The JS reads `ss.readiness_score != null ? ss.readiness_score : meta.readiness_score` to handle both paths.

---

## 19. Known Limitations and Walk Version Upgrades

### Current limitations (crawl stage)

**Marketing Brain coverage:**
- 8 products, 3 personas, 6 pains, 11 proof points — the universe is narrow
- No product page content, no blog posts, no competitive battle cards
- Email examples only exist for AgentHub and Skills Intelligence
- ICP data exists but firmographic fields (company size, revenue range) are intentionally excluded from LLM context to prevent hallucination

**Evidence gaps:**
- No named-customer case studies for AgentHub, Skills Intelligence, Harmony AI, Headless Learning, Roleplay, Content Creator
- All AgentHub campaigns currently produce "Hypothesis" status (no VALIDATES_PRODUCT evidence)
- Platform stats (79%, 85%, 80%) are available for persona-level pains but don't satisfy the product-level named-customer requirement

**Authentication:**
- Single hardcoded user/password in `auth.py`
- No session expiry, no role-based access, no team collaboration

**Asana integration:**
- Hardcoded API token (rotation required for production)
- No error recovery if project creation succeeds but some tasks fail
- No idempotency — re-pushing the same campaign creates a duplicate project

**PPTX preview:**
- `spire.presentation` requires a commercial license for production use
- PDF conversion can be slow (2–5 seconds per deck)

**No A/B copy variants:** Current system generates one positioning statement and one set of pillar copy. Walk version target: 2–3 variants per component for testing.

### Walk version upgrade targets

| Component | Target |
|---|---|
| Neo4j brain expansion | Ingest product pages, webinar transcripts, and sales call data as new nodes |
| Marketing Brain | Add real-time product pages, webinar recordings, sales call transcripts |
| Salesforce signals | Read account health, product usage, and open opportunity data to inform ICP |
| Named customers | Add Bethany Care Society, MidFirst Bank, Disguise case studies with VALIDATES_PRODUCT edges for actual products |
| Email examples | Add proven subject lines for all 8 products |
| A/B copy variants | Generate 2–3 positioning variants, score by evidence quality |
| Auth | SSO or team accounts with per-user role assignments |
| Asana | Idempotency check, error recovery, re-push detection |
| Benchmark database | Store historical HubSpot performance data so metrics 2–4 have real benchmarks |
| Campaign versioning | Track brief evolution and campaign iterations |

---

## 20. Architecture Decisions and Rationale

### Why graph traversal instead of retrieval-augmented generation

The first version of this system passed `marketing_brain.json` wholesale into the system prompt. This caused two problems: (1) Claude would treat persona properties (company size, team size) as facts about the specific campaign's audience rather than graph-level estimates, and (2) the token budget was consumed by irrelevant nodes for every brief.

Graph traversal solves both: only the nodes relevant to this brief's product + persona + pain combination reach the LLM. Firmographic properties are explicitly stripped from persona nodes before passing to Claude (`company_size` and `team_size` are in the JSON but not in `_fmt_ctx()`'s output).

### Why Python enforces structure instead of trusting Claude

Claude's post-call output is treated as untrusted for structural fields. Asset type, format, and owner are overwritten by `_enforce_locked_assets()`. Proof points are replaced by `_apply_proof_point_placeholders()` if they fail the graph check. Benchmark numbers are replaced by `_flag_invented_numbers()`.

The alternative — writing more detailed instructions and hoping Claude follows them — was rejected because: (a) it produces invisible failures (the LLM writes "sounds right" content that's actually wrong), and (b) it makes the system non-auditable. When Python enforces structure, every structural decision has a clear code path.

### Why the two-mode prompt architecture

The `system_context.txt` Mode 1 / Mode 2 split was designed to give Claude explicit permission to write marketing language in messaging sections while preventing that same latitude from bleeding into structural decisions. Before this split, Claude would apply marketing framing to ICP extraction ("a 500-person financial services company" when the brief said nothing about size or industry) and to channel selection ("LinkedIn is best for this audience" when the graph had already made that decision).

Mode 1 is a hard constraint on what can be claimed as a fact. Mode 2 is permission to frame verified facts in buyer language. They are mutually exclusive by design.

### Why asset plans are locked before the Claude call

The original design let Claude generate the full asset plan (type, format, owner, purpose). This produced two failure modes: (a) duplicate owners (two different assets with "Demand Gen" as owner), and (b) asset types that didn't match the channels the graph selected.

`_CHANNEL_ASSET_MAP` solves this by pre-computing the correct asset structure from the channel list. Claude writes only the `purpose` field. The result is structurally correct by construction, not by prompt compliance.

### Why Haiku for parsing and extraction, Sonnet for messaging

Brief parsing, rollout generation, and deck distillation are extraction and transformation tasks: parse a goal into `{metric, target, timeframe_days}`, generate a task with a 15-word limit, compress a campaign output into slide JSON. These require precision and rule-following, not creative language. Haiku at temperature 0 is faster and cheaper for these tasks.

Messaging generation requires genuine marketing voice — the positioning statement and pillar copy are the highest-stakes outputs and will appear in customer-facing materials. Sonnet's stronger language quality justifies the cost premium for that one step.

### Why readiness scores are computed before deck distillation

Early versions computed readiness after the deck was built, then tried to pass the scores back to the deck. This created a dependency cycle: the deck distiller needed scores, but scores weren't available until after the deck was analyzed.

The correct order: compute readiness from the campaign output (structure, messaging, rollout, ctx), then pass the readiness dict to `distill_for_deck()`, which injects scores post-LLM-call. The LLM never sees or computes these numbers — it receives instructions to copy from meta, and Python overwrites those fields afterward with authoritative values.

### Why evidence quality gates output size

Generating a full 3-phase rollout and 6-asset plan for a campaign with no named-customer evidence creates a false impression of execution readiness. A marketer who sees a complete-looking plan may launch without resolving the case study gap.

The calibration rules (cap at 2 phases, cap at 3 assets) make the evidence gap structural rather than advisory. The output literally cannot reach full size without full evidence. Combined with `[PROOF POINT NEEDED]` placeholders and blocker tasks in Asana, the system makes the gap unmissable at every output level.

---

*End of documentation.*
