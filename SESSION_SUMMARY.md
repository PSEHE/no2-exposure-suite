# Session Summary — NO₂ Exposure Suite

*2026-06-04 — from the CONTAM gas-stove modeling to two interactive products.*

## Goal

Turn the multizone CONTAM NO₂ modeling behind Kashtan et al. (*Sci. Adv.* 2024,
eadm8680; *PNAS Nexus* 2025, pgaf341) into interactive, computationally-cheap
tools — a public-facing widget and a research-grade CONTAM replacement —
faithful to the underlying physics.

## What was done

1. Read the whole source project (86,400-scenario library, per-house `.prj`
   models, the scale-up notebook, the prior Flask prototype) and **both papers**.
2. Scoped two products and confirmed every design fork.
3. Built, verified, and shipped — 10 commits, with a live public site.

## Delivered

| Piece | Status |
|---|---|
| Shared foundation (`core/`: constants, library loader, ZIP weighting, archetype selection, web-data export) | ✅ done, verified (2000/2000 scenarios match source) |
| **Product 1 — Explorer** (public widget) | ✅ **live:** https://psehe.github.io/no2-exposure-suite/ |
| Product 2 — CONTAM-Lite parser (`core/prj.py`) | ✅ done, all 24 houses |
| Product 2 — airflow solver (`core/airflow.py`) | ✅ done, verified (0–6/h ACH, 7–170 zones, <100 ms) |

**Explorer** (Vite + Svelte + D3, single self-contained ~0.9 MB-gzip HTML):
address/ZIP personalization (Census/OpenStreetMap geocode → real outdoor NO₂ +
climate + representative home for the ZIP), behavioral controls, continuous
fine-tune sliders, an animated 24-h kitchen curve, a color-graded house
cross-section, a "what would lower it" card, exact-CONTAM headline numbers, a
dual worst-hour readout, an About/methods section, mobile-responsive, and
accessible. Substantially exceeds the prior Flask prototype on every axis.

**Airflow solver:** the genuine first-principles core — Newton-Raphson on zone
pressures with power-law leakage/orifice/door elements, stack effect, and
wind-pressure profiles; parses any `.prj` and produces physically-correct air
exchange.

## Key technical findings

- **Decay** (−2.42×10⁻⁴/s for NO₂ and CONTA) is in the `.prj` kinetic-reactions
  section; CONTB has none (it is the air-exchange tracer).
- **Outdoor trick:** CONTA is a dummy pollutant with NO₂'s physics and a 100-ppb
  outdoor boundary, so `outdoor × CONTA/100` gives outdoor-attributable indoor
  exposure; the occupancy path's `Outdoor` column (=100 for CONTA, 0 for NO₂)
  already incorporates full-outdoor-while-outdoors.
- **Asthma constants** use Lin, Brunekreef & Gehring 2013 (Int. J. Epidemiol.
  42:1724) per direction: gas-cooking OR 1.32 (1.18–1.48); per-15-ppb NO₂ OR
  1.09 (0.91–1.31); current wheeze OR 1.15 (1.06–1.25). Mortality unchanged
  (Atkinson, RR 1.02 per 10 µg/m³).
- **Scenario-generation macro values** (`$(TEMP)`/`$(WIND)`/`$(WINDOW)`/`$(USE)`)
  are **not in the repo** (generated on a Windows machine). Product 2 takes
  physical units directly; validation will calibrate the category→physical
  mapping against the library's tracer-derived air exchange.
- **Outdoor-component fix** (live): full outdoor while outdoors + ventilation-
  consistent indoor penetration. Opening windows now lowers exposure under
  *clean* outdoor; the electric comparison corrected from a misleading −2% to a
  true −16% in SF (94112). At the default 7-ppb outdoor, opening windows
  correctly *increases* exposure (7 ppb outdoor air is dirtier than the stove's
  indoor contribution — "clean" means low).

## Current state

- Repo: `github.com/PSEHE/no2-exposure-suite` (public, `main`, 10 commits).
  `PLAN.md` tracks all phases with checkboxes.
- **Uncommitted / local only:** a strict-monotonic refinement of the
  window/outdoor behavior (ties penetration to the displayed cooking level so
  clean-outdoor window opening is strictly monotonic; removes a sub-1% blip at
  "moderate"). Built into `docs/` locally but **not committed and not pushed** —
  no effect on the live site or history. Decision pending: keep (commit +
  redeploy) or discard (revert working tree).
- Update the live site: `npm run build --prefix explorer && cp explorer/dist/index.html docs/`,
  then commit + push (GitHub Pages redeploys from `docs/` in ~1 min).

## What remains (Product 2 — CONTAM-Lite)

1. **Calibrate** the category→physical mapping (COLD/STILL/window-levels →
   °C / m·s⁻¹ / open-fraction; tune the wind-pressure modifier) against the
   library's CONTA-derived air exchange.
2. **Contaminant transport solver** — interzone airflows + the 1000 m³/h
   `fan_cvf` door mixing + the −2.4×10⁻⁴/s decay → per-zone time series.
3. **Validation harness** vs the 86,400-scenario library + per-scenario
   time-series (target ~10–15% fidelity); report the error distribution.
4. **Streamlit app** — single-home panel + population panel (population sliders
   → reweight → exposure + health outcomes).

## To resume

Decide keep/discard on the strict-monotonic tweak, then "resume CONTAM-Lite"
to pick up at the calibration step.

---
*Drafted by Claude with prompts engineered by Yannai Kashtan*
