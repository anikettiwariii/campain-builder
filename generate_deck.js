"use strict";
const pptxgen = require("pptxgenjs");
const fs = require("fs");

const inputPath = process.argv[2];
const outputPath = process.argv[3] || "kickoff_deck.pptx";

if (!inputPath) { console.error("Usage: node generate_deck.js <input.json> [output.pptx]"); process.exit(1); }

const { deck_content } = JSON.parse(fs.readFileSync(inputPath, "utf8"));

const BLUE       = "1B3C87";
const PURPLE     = "7C3FA8";
const WHITE      = "FFFFFF";
const LIGHT_GREY = "F4F5F7";
const MID_GREY   = "8C93A0";
const DARK       = "1A1A2E";
const AMBER      = "D97706";
const GREEN      = "059669";
const FONT       = "Calibri";

const pres = new pptxgen();
pres.layout = "LAYOUT_16x9";

const meta = deck_content.meta || {};
const dateStr = meta.date || new Date().toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" });
const ts = deck_content.title_slide || {};
const ss = deck_content.status_slide || {};
const ps = deck_content.pillars_slide || {};
const as = deck_content.asset_slide || {};
const rs = deck_content.rollout_slide || {};
const ms = deck_content.metrics_slide || {};

function s(val) {
  if (val == null) return "";
  if (typeof val === "string") return val;
  if (typeof val === "number") return String(val);
  if (Array.isArray(val)) return val.join(", ");
  return String(val);
}

// ── SLIDE 1 — TITLE ──────────────────────────────────────────────────────────
{
  const slide = pres.addSlide();
  slide.background = { color: DARK };

  slide.addShape(pres.ShapeType.rect, { x: 0, y: 0, w: 10, h: 1.1, fill: { color: PURPLE }, line: { color: PURPLE } });
  slide.addText("docebo", { x: 0.5, y: 0.25, w: 3, h: 0.6, fontSize: 22, bold: true, color: WHITE, fontFace: FONT, margin: 0 });
  slide.addText(dateStr, { x: 6.5, y: 0.3, w: 3, h: 0.5, fontSize: 11, color: WHITE, align: "right", fontFace: FONT, margin: 0 });
  slide.addText("CAMPAIGN KICKOFF", { x: 0.6, y: 1.4, w: 9, h: 0.5, fontSize: 12, color: PURPLE, bold: true, charSpacing: 4, fontFace: FONT, margin: 0 });
  slide.addText(s(ts.product) || s(meta.product), { x: 0.6, y: 1.9, w: 9, h: 1.1, fontSize: 44, bold: true, color: WHITE, fontFace: FONT, margin: 0 });
  slide.addText(s(ts.goal), { x: 0.6, y: 3.05, w: 8, h: 0.55, fontSize: 18, color: MID_GREY, fontFace: FONT, margin: 0 });
  slide.addText(dateStr,    { x: 0.6, y: 3.63, w: 6, h: 0.22, fontSize: 12, color: "444466", fontFace: FONT, margin: 0 });

  const stats = [
    { label: "TARGET", value: s(ts.goal).split(" in ")[0] || "20 sign-ups" },
    { label: "TIMELINE", value: s(ts.timeline) },
    { label: "MOTION", value: s(ts.motion).split(" —")[0] || "Early access" },
  ];
  stats.forEach((st, i) => {
    const x = 0.6 + i * 3.1;
    slide.addShape(pres.ShapeType.roundRect, { x, y: 3.85, w: 2.8, h: 1.2, fill: { color: "232347" }, rectRadius: 0.08, line: { color: "3A3A6E", width: 1 } });
    slide.addText(st.value, { x, y: 3.95, w: 2.8, h: 0.55, fontSize: 16, bold: true, color: WHITE, align: "center", fontFace: FONT, margin: 0 });
    slide.addText(st.label, { x, y: 4.52, w: 2.8, h: 0.3, fontSize: 9, color: MID_GREY, align: "center", charSpacing: 2, fontFace: FONT, margin: 0 });
  });

  slide.addText("Never Stop Learning", { x: 0.5, y: 5.25, w: 9, h: 0.3, fontSize: 10, color: "555588", italic: true, fontFace: FONT, margin: 0 });
}

// ── SLIDE 2 — CAMPAIGN STATUS ─────────────────────────────────────────────────
{
  const slide = pres.addSlide();
  slide.background = { color: WHITE };

  slide.addShape(pres.ShapeType.rect, { x: 0, y: 0, w: 10, h: 0.7, fill: { color: BLUE }, line: { color: BLUE } });
  slide.addText("docebo", { x: 0.4, y: 0.1, w: 2, h: 0.5, fontSize: 14, bold: true, color: WHITE, fontFace: FONT, margin: 0 });
  slide.addText("CAMPAIGN STATUS", { x: 0, y: 0.1, w: 9.6, h: 0.5, fontSize: 14, bold: true, color: WHITE, align: "right", fontFace: FONT, margin: 0 });

  slide.addText("CAMPAIGN READINESS", { x: 0.5, y: 0.95, w: 4.2, h: 0.35, fontSize: 9, color: MID_GREY, charSpacing: 2, fontFace: FONT, margin: 0 });

  slide.addShape(pres.ShapeType.roundRect, { x: 0.5, y: 1.3, w: 4.2, h: 2.05, fill: { color: LIGHT_GREY }, rectRadius: 0.08, line: { color: "E0E0E0" } });
  slide.addText(s(ss.readiness_score != null ? ss.readiness_score : meta.readiness_score), { x: 0.7, y: 1.4, w: 1.2, h: 0.9, fontSize: 52, bold: true, color: BLUE, fontFace: FONT, margin: 0 });
  slide.addText("/ 100", { x: 1.85, y: 1.75, w: 1, h: 0.5, fontSize: 18, color: MID_GREY, fontFace: FONT, margin: 0 });
  slide.addText("🟡  " + s(ss.status_label || meta.status_label), { x: 0.7, y: 2.35, w: 2.5, h: 0.35, fontSize: 13, bold: true, color: AMBER, fontFace: FONT, margin: 0 });

  const scores = [
    { label: "Structure", val: s(ss.structure_score != null ? ss.structure_score : meta.structure_score) + " / 100", color: GREEN },
    { label: "Evidence",  val: s(ss.evidence_score  != null ? ss.evidence_score  : meta.evidence_score)  + " / 100", color: AMBER },
  ];
  scores.forEach((sc, i) => {
    slide.addText(sc.label, { x: 0.7, y: 2.75 + i * 0.28, w: 1.2, h: 0.27, fontSize: 11, color: MID_GREY, fontFace: FONT, margin: 0 });
    slide.addText(sc.val,   { x: 2.0, y: 2.75 + i * 0.28, w: 1.5, h: 0.27, fontSize: 12, bold: true, color: sc.color, fontFace: FONT, margin: 0 });
  });

  slide.addShape(pres.ShapeType.roundRect, { x: 0.5, y: 3.45, w: 4.2, h: 0.85, fill: { color: "FEF3C7" }, rectRadius: 0.06, line: { color: "FCD34D" } });
  slide.addText("Evidence gaps: " + s(ss.evidence_gaps || meta.evidence_gaps), { x: 0.65, y: 3.52, w: 3.9, h: 0.65, fontSize: 10, color: "92400E", fontFace: FONT, margin: 0, wrap: true });

  // Campaign Name and Concept — left column, below evidence gaps
  slide.addShape(pres.ShapeType.rect, { x: 0.5, y: 4.38, w: 4.2, h: 0.01, fill: { color: "D0D0D8" }, line: { color: "D0D0D8" } });
  slide.addText("CAMPAIGN NAME", { x: 0.5, y: 4.43, w: 4.2, h: 0.18, fontSize: 7.5, color: PURPLE, bold: true, charSpacing: 1.5, fontFace: FONT, margin: 0 });
  slide.addText(s(ss.campaign_name || meta.campaign_name), { x: 0.5, y: 4.59, w: 4.2, h: 0.28, fontSize: 12, bold: true, color: DARK, fontFace: FONT, margin: 0 });
  slide.addText("CAMPAIGN CONCEPT", { x: 0.5, y: 4.9, w: 4.2, h: 0.18, fontSize: 7.5, color: MID_GREY, bold: true, charSpacing: 1.5, fontFace: FONT, margin: 0 });
  slide.addText(s(ss.campaign_concept || meta.campaign_concept), { x: 0.5, y: 5.06, w: 4.2, h: 0.22, fontSize: 8, color: "666688", fontFace: FONT, margin: 0, wrap: true });

  slide.addText("TARGET & MOTION", { x: 5.3, y: 0.95, w: 4.2, h: 0.35, fontSize: 9, color: MID_GREY, charSpacing: 2, fontFace: FONT, margin: 0 });

  const icpItems = [
    { label: "ICP",      value: s(ss.icp) },
    { label: "MOTION",   value: s(ss.motion) },
    { label: "GOAL",     value: s(ss.goal) },
    { label: "TIMELINE", value: s(ss.timeline) },
  ];
  icpItems.forEach((item, i) => {
    const y = 1.3 + i * 0.9;
    slide.addShape(pres.ShapeType.roundRect, { x: 5.3, y, w: 4.2, h: 0.78, fill: { color: WHITE }, rectRadius: 0.06, line: { color: "E8E8E8" } });
    slide.addText(item.label, { x: 5.5, y: y + 0.07, w: 1.2, h: 0.25, fontSize: 8, color: PURPLE, charSpacing: 1, bold: true, fontFace: FONT, margin: 0 });
    slide.addText(item.value, { x: 5.5, y: y + 0.3,  w: 3.8, h: 0.42, fontSize: 10.5, color: DARK, fontFace: FONT, margin: 0, wrap: true });
  });

  slide.addText("docebo  ·  " + s(meta.product) + " Kickoff  ·  " + dateStr, { x: 0.4, y: 5.3, w: 9.2, h: 0.25, fontSize: 8, color: MID_GREY, fontFace: FONT, margin: 0 });
}

// ── SLIDE 3 — MESSAGING PILLARS ───────────────────────────────────────────────
{
  const slide = pres.addSlide();
  slide.background = { color: DARK };

  slide.addShape(pres.ShapeType.rect, { x: 0, y: 0, w: 10, h: 0.7, fill: { color: PURPLE }, line: { color: PURPLE } });
  slide.addText("docebo", { x: 0.4, y: 0.1, w: 2, h: 0.5, fontSize: 14, bold: true, color: WHITE, fontFace: FONT, margin: 0 });
  slide.addText("MESSAGING PILLARS", { x: 0, y: 0.1, w: 9.6, h: 0.5, fontSize: 14, bold: true, color: WHITE, align: "right", fontFace: FONT, margin: 0 });
  slide.addText(s(ps.positioning), { x: 0.5, y: 0.85, w: 9, h: 0.55, fontSize: 19, color: WHITE, italic: true, fontFace: FONT, margin: 0 });

  const pillars = (ps.pillars || []).slice(0, 3);
  pillars.forEach((p, i) => {
    const x = 0.35 + i * 3.15;
    slide.addShape(pres.ShapeType.roundRect, { x, y: 1.55, w: 2.95, h: 3.55, fill: { color: "232347" }, rectRadius: 0.1, line: { color: "3A3A6E" } });
    slide.addText(String(i + 1).padStart(2, "0"), { x: x + 0.2, y: 1.7, w: 0.7, h: 0.5, fontSize: 22, bold: true, color: PURPLE, fontFace: FONT, margin: 0 });
    slide.addText(s(p.title), { x: x + 0.2, y: 2.25, w: 2.55, h: 0.75, fontSize: 13, bold: true, color: WHITE, fontFace: FONT, margin: 0, wrap: true });
    slide.addText(s(p.one_liner), { x: x + 0.2, y: 3.05, w: 2.55, h: 0.9, fontSize: 10.5, color: MID_GREY, fontFace: FONT, margin: 0, wrap: true });

    const proofNeeded = s(p.proof_status) === "needed" || s(p.proof).toLowerCase().includes("proof point needed");
    slide.addShape(pres.ShapeType.roundRect, { x: x + 0.2, y: 4.05, w: 2.55, h: 0.75, fill: { color: proofNeeded ? "3D2800" : "0D2B1A" }, rectRadius: 0.06, line: { color: proofNeeded ? "A05A00" : "1A5C34" } });
    slide.addText((proofNeeded ? "⚠  PROOF POINT NEEDED" : "✓  " + s(p.proof)), { x: x + 0.2, y: 4.1, w: 2.55, h: 0.65, fontSize: 9, color: proofNeeded ? AMBER : "86EFAC", fontFace: FONT, margin: 0, wrap: true, align: "center", valign: "middle" });
  });

  slide.addText("docebo  ·  " + s(meta.product) + " Kickoff  ·  " + dateStr, { x: 0.4, y: 5.3, w: 9.2, h: 0.25, fontSize: 8, color: "555588", fontFace: FONT, margin: 0 });
}

// ── SLIDE 4 — ASSET PLAN ──────────────────────────────────────────────────────
{
  const slide = pres.addSlide();
  slide.background = { color: WHITE };

  slide.addShape(pres.ShapeType.rect, { x: 0, y: 0, w: 10, h: 0.7, fill: { color: BLUE }, line: { color: BLUE } });
  slide.addText("docebo", { x: 0.4, y: 0.1, w: 2, h: 0.5, fontSize: 14, bold: true, color: WHITE, fontFace: FONT, margin: 0 });
  slide.addText("ASSET PLAN", { x: 0, y: 0.1, w: 9.6, h: 0.5, fontSize: 14, bold: true, color: WHITE, align: "right", fontFace: FONT, margin: 0 });
  slide.addText(s(as.asset_count) + " assets · graph-selected channels", { x: 0.5, y: 0.85, w: 9, h: 0.4, fontSize: 13, color: MID_GREY, italic: true, fontFace: FONT, margin: 0 });

  const cols = ["ASSET", "FORMAT", "OWNER", "PURPOSE"];
  const colW = [2.2, 2.0, 1.6, 3.7];
  const colX = [0.4, 2.65, 4.7, 6.35];

  cols.forEach((c, i) => {
    slide.addShape(pres.ShapeType.rect, { x: colX[i], y: 1.35, w: colW[i] - 0.05, h: 0.35, fill: { color: BLUE }, line: { color: BLUE } });
    slide.addText(c, { x: colX[i] + 0.1, y: 1.35, w: colW[i] - 0.15, h: 0.35, fontSize: 9, bold: true, color: WHITE, charSpacing: 1, fontFace: FONT, margin: 0, valign: "middle" });
  });

  const assets = (as.assets || []).slice(0, 6);
  assets.forEach((a, ri) => {
    const y  = 1.75 + ri * 0.62;
    const bg = ri % 2 === 0 ? WHITE : LIGHT_GREY;
    const cells = [s(a.name), s(a.format), s(a.owner), s(a.purpose || "")];
    cells.forEach((cell, ci) => {
      slide.addShape(pres.ShapeType.rect, { x: colX[ci], y, w: colW[ci] - 0.05, h: 0.55, fill: { color: bg }, line: { color: "E0E0E0" } });
      slide.addText(cell, { x: colX[ci] + 0.1, y: y + 0.05, w: colW[ci] - 0.2, h: 0.45, fontSize: ci === 0 ? 11 : 10, bold: ci === 0, color: ci === 0 ? DARK : ci === 2 ? PURPLE : MID_GREY, fontFace: FONT, margin: 0, wrap: true, valign: "middle" });
    });
  });

  if (as.evidence_note) {
    slide.addShape(pres.ShapeType.roundRect, { x: 0.4, y: 4.6, w: 9.2, h: 0.55, fill: { color: "EEF2FF" }, rectRadius: 0.07, line: { color: "C7D2FE" } });
    slide.addText(s(as.evidence_note), { x: 0.6, y: 4.65, w: 8.8, h: 0.45, fontSize: 9.5, color: "3730A3", fontFace: FONT, margin: 0, wrap: true });
  }

  slide.addText("docebo  ·  " + s(meta.product) + " Kickoff  ·  " + dateStr, { x: 0.4, y: 5.3, w: 9.2, h: 0.25, fontSize: 8, color: MID_GREY, fontFace: FONT, margin: 0 });
}

// ── SLIDE 5 — PHASED ROLLOUT ──────────────────────────────────────────────────
{
  const slide = pres.addSlide();
  slide.background = { color: DARK };

  slide.addShape(pres.ShapeType.rect, { x: 0, y: 0, w: 10, h: 0.7, fill: { color: BLUE }, line: { color: BLUE } });
  slide.addText("docebo", { x: 0.4, y: 0.1, w: 2, h: 0.5, fontSize: 14, bold: true, color: WHITE, fontFace: FONT, margin: 0 });
  slide.addText("PHASED ROLLOUT", { x: 0, y: 0.1, w: 9.6, h: 0.5, fontSize: 14, bold: true, color: WHITE, align: "right", fontFace: FONT, margin: 0 });

  const phases = (rs.phases || []).slice(0, 2);
  phases.forEach((ph, pi) => {
    const x = 0.35 + pi * 4.9;
    slide.addShape(pres.ShapeType.roundRect, { x, y: 0.8, w: 4.55, h: 4.4, fill: { color: "1C1C3A" }, rectRadius: 0.1, line: { color: "3A3A6E" } });
    slide.addText((s(ph.name) || "Phase " + (pi + 1)).toUpperCase(), { x: x + 0.2, y: 0.9,  w: 3,   h: 0.3,  fontSize: 8,  color: PURPLE, bold: true, charSpacing: 1, fontFace: FONT, margin: 0 });
    slide.addText(s(ph.days || ph.weeks),                             { x: x + 0.2, y: 1.22, w: 4.1, h: 0.35, fontSize: 17, bold: true, color: WHITE, fontFace: FONT, margin: 0 });
    slide.addText("↳ " + s(ph.milestone),                        { x: x + 0.2, y: 1.6,  w: 4.1, h: 0.45, fontSize: 9.5, color: MID_GREY, italic: true, fontFace: FONT, margin: 0, wrap: true });

    const tasks = (ph.tasks || []).slice(0, 3);
    tasks.forEach((task, ti) => {
      const ty = 2.15 + ti * 0.62;
      slide.addShape(pres.ShapeType.roundRect, { x: x + 0.2, y: ty, w: 4.1, h: 0.55, fill: { color: "28284A" }, rectRadius: 0.06, line: { color: "3A3A6E" } });
      slide.addText(s(task), { x: x + 0.35, y: ty + 0.08, w: 3.75, h: 0.4, fontSize: 9, color: WHITE, fontFace: FONT, margin: 0, wrap: true });
    });

    if (ph.checkpoint) {
      slide.addShape(pres.ShapeType.roundRect, { x: x + 0.2, y: 4.05, w: 4.1, h: 0.65, fill: { color: "1A2F1A" }, rectRadius: 0.06, line: { color: "2D5A2D" } });
      slide.addText("✓  CHECKPOINT", { x: x + 0.35, y: 4.1,  w: 2,    h: 0.25, fontSize: 8,   bold: true, color: GREEN, charSpacing: 1, fontFace: FONT, margin: 0 });
      slide.addText(s(ph.checkpoint),     { x: x + 0.35, y: 4.33, w: 3.75, h: 0.3,  fontSize: 8.5, color: "86EFAC", fontFace: FONT, margin: 0, wrap: true });
    }
  });

  slide.addText("docebo  ·  " + s(meta.product) + " Kickoff  ·  " + dateStr, { x: 0.4, y: 5.3, w: 9.2, h: 0.25, fontSize: 8, color: "555588", fontFace: FONT, margin: 0 });
}

// ── SLIDE 6 — SUCCESS METRICS & GOVERNANCE ────────────────────────────────────
{
  const slide = pres.addSlide();
  slide.background = { color: WHITE };

  slide.addShape(pres.ShapeType.rect, { x: 0, y: 0, w: 10, h: 0.7, fill: { color: PURPLE }, line: { color: PURPLE } });
  slide.addText("docebo", { x: 0.4, y: 0.1, w: 2, h: 0.5, fontSize: 14, bold: true, color: WHITE, fontFace: FONT, margin: 0 });
  slide.addText("SUCCESS METRICS & GOVERNANCE", { x: 0, y: 0.1, w: 9.6, h: 0.5, fontSize: 14, bold: true, color: WHITE, align: "right", fontFace: FONT, margin: 0 });

  slide.addText("SUCCESS METRICS", { x: 0.5, y: 0.85, w: 4.3, h: 0.3, fontSize: 9, color: MID_GREY, charSpacing: 2, bold: true, fontFace: FONT, margin: 0 });

  const metrics = (ms.metrics || []).slice(0, 4);
  metrics.forEach((m, i) => {
    const y        = 1.2 + i * 0.78;
    const verified = m.verified === true;
    slide.addShape(pres.ShapeType.roundRect, { x: 0.5, y, w: 4.3, h: 0.68, fill: { color: verified ? "F0FDF4" : "FFFBEB" }, rectRadius: 0.06, line: { color: verified ? "86EFAC" : "FCD34D" } });
    slide.addText(s(m.label).toUpperCase(), { x: 0.65, y: y + 0.05, w: 3.9, h: 0.2,  fontSize: 7.5, color: MID_GREY, charSpacing: 1, fontFace: FONT, margin: 0 });
    slide.addText(s(m.value),               { x: 0.65, y: y + 0.26, w: 3.9, h: 0.35, fontSize: 10,  bold: verified, color: verified ? "166534" : "92400E", fontFace: FONT, margin: 0, wrap: true });
  });

  slide.addText("HUMAN REVIEW CHECKPOINTS", { x: 5.2, y: 0.85, w: 4.3, h: 0.3, fontSize: 9, color: MID_GREY, charSpacing: 2, bold: true, fontFace: FONT, margin: 0 });

  const checkpoints = (ms.checkpoints || []).slice(0, 3);
  checkpoints.forEach((cp, i) => {
    const y = 1.2 + i * 1.12;
    slide.addShape(pres.ShapeType.roundRect, { x: 5.2, y, w: 4.3, h: 1.0, fill: { color: LIGHT_GREY }, rectRadius: 0.08, line: { color: "E0E0E0" } });
    slide.addText(s(cp.day),    { x: 5.35, y: y + 0.08, w: 0.8, h: 0.3,  fontSize: 13,  bold: true, color: BLUE,   fontFace: FONT, margin: 0 });
    slide.addText(s(cp.teams),  { x: 6.2,  y: y + 0.1,  w: 3.1, h: 0.28, fontSize: 9.5, bold: true, color: PURPLE, fontFace: FONT, margin: 0 });
    slide.addText(s(cp.action), { x: 5.35, y: y + 0.42, w: 3.95, h: 0.5,  fontSize: 9,   color: MID_GREY, fontFace: FONT, margin: 0, wrap: true });
  });

  slide.addText("docebo  ·  " + s(meta.product) + " Kickoff  ·  " + dateStr, { x: 0.4, y: 5.3, w: 9.2, h: 0.25, fontSize: 8, color: MID_GREY, fontFace: FONT, margin: 0 });
}

// ── WRITE ─────────────────────────────────────────────────────────────────────
pres.writeFile({ fileName: outputPath })
  .then(() => { console.log("saved: " + outputPath); process.exit(0); })
  .catch(err => { console.error(err); process.exit(1); });
