# Build Plan & Progress

Living document. Checkboxes track progress; edit freely.

## Decisions locked in
- **Two products**, built sequentially: foundation → Explorer (public) → CONTAM-Lite (research).
- **Explorer**: Vite + Svelte + D3, bundled to a single self-contained HTML; neutral/academic look;
  hybrid engine (exact library lookup + anchored box-model); address Tier 1 now, Tier 2 later.
- **CONTAM-Lite**: Streamlit; full multizone physics port; validation bar ≈ 10–15% vs stored library
  (revisit after first pass); single-home + population panels (population panel includes health outcomes).
- Repo: `~/CONTAM/no2-exposure-suite`, fresh git repo.
- Must substantially improve on the prior Flask prototype at
  `~/Documents/Exposure_Calculator/exposure_calculator.py`.

---

## Phase 0 — Shared foundation  ✅
- [x] Repo scaffold + git init + `.gitignore` + README
- [x] Confirm data schemas (canonical 86,400 library; `zips_abbr_updated.csv`)
- [x] `core/config.py` — source-data paths
- [x] `core/constants.py` — physics + epi constants from the papers
- [x] `core/library.py` — load library + ZIP table; lookup, ZIP-weighting, archetype selection
- [x] `core/export_web_data.py` — emit `web_data/{scenario_library,zip_data,archetypes}.json`
- [x] Parity check: weighted exposure sane for ZIP 94112; 2000/2000 flat-array values match source
- [x] Generate `web_data/` (scenario_library 3.1 MB, zip_data 4.8 MB, archetypes) and commit

## Phase 1 — Explorer (public widget)
### 1a. Scaffold + exact-lookup MVP
- [ ] Vite + Svelte + D3 project; single-file build config
- [ ] Design system (neutral/academic): typography, palette, layout, responsive shell
- [ ] Load embedded scenario library; port lookup + ZIP-weighting + archetype selection to JS
- [ ] Manual home picker + behavioral controls (hood, stove use, window, time-in-kitchen)
- [ ] Hero exposure readout + benchmark gauge (WHO / EPA)
### 1b. Graphics + live solver
- [ ] Animated 24-h concentration time-series (kitchen vs bedroom)
- [ ] House cross-section concentration visual
- [ ] Anchored box-model for continuous knobs (emission/# burners/cook minutes, kitchen & home size, outdoor NO₂, hood capture %, air-exchange)
- [ ] "Switch to electric / turn on the hood" before-after comparison
### 1c. Address Tier 1
- [ ] Address input → US Census geocoder → ZIP
- [ ] Lazy-load ZIP data → outdoor NO₂ + climate + wind + smart housing defaults
- [ ] Graceful fallback to manual mode
### 1d. Polish
- [ ] Plain-language interpretation + guidance copy
- [ ] Mobile, accessibility, performance (payload optimization)
- [ ] Branding pass; deploy to GitHub Pages
- [ ] (Optional) Address Tier 2: property-data API + serverless proxy

## Phase 2 — CONTAM-Lite engine + validation
- [ ] `.prj` parser (zones, flow elements, paths, sources, schedules, ambient)
- [ ] Airflow network solver (Newton-Raphson on zone pressures; stack + wind + fan)
- [ ] Contaminant transport ODE integrator (with NO₂ decay)
- [ ] Validation harness vs stored 86,400 library + per-scenario time-series
- [ ] Hit ~10–15% fidelity; report error distribution

## Phase 3 — CONTAM-Lite app
- [ ] Streamlit single-home panel (all physical knobs + per-zone time-series + benchmarks)
- [ ] Custom floorplan support
- [ ] Population panel (population sliders → reweight library → exposure distribution)
- [ ] Health outcomes (asthma PAF, mortality, costs) + optional maps

---
*Drafted by Claude with prompts engineered by Yannai Kashtan*
