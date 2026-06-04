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
### 1a. Scaffold + exact-lookup MVP  ✅
- [x] Vite + Svelte 5 + D3 project; single-file build (2.8 MB / 505 KB gzip)
- [x] Design system (neutral/academic): light theme, tokens, responsive shell
- [x] Load embedded scenario library; port lookup + ZIP-weighting + archetype selection to JS
- [x] Manual home picker (24 archetypes) + behavioral controls (hood, use, window, time-in-kitchen)
- [x] Hero exposure readouts + benchmark bar (WHO / Canada) + red/green over-states
### 1b. Graphics + live solver  ✅
- [x] 24-h concentration time-series (kitchen vs bedroom) anchored to exact kitchen-air peak
- [x] Cooking-period shading + adaptive y-axis + 1-hr benchmark line
- [x] House cross-section concentration visual (room-by-room color gradient)
- [x] Continuous fine-tune knobs (cooking amount, kitchen size, ventilation) — physical scaling, =1 at CONTAM defaults
- [x] "What would lower it" before-after comparison (electric / hood / window)
### 1c. Address Tier 1  ✅
- [x] Address input → ZIP (Nominatim/OSM geocode; Census was CORS-blocked) + 5-digit ZIP fast-path (no network)
- [x] ZIP data (bundled) → outdoor NO₂ + climate (winter/summer weighting) + ZIP wind dist + default archetype
- [x] On lookup: auto-set outdoor NO₂ + representative home; everything still adjustable
- [x] Dual worst-hour readout (kitchen air vs personal); graceful errors + clear/manual fallback
- Note: single-file build now 6.6 MB / 1.2 MB gz (ZIP table inlined) — trim in 1d
### 1d. Polish  ⏳ (deploy pending confirmation)
- [x] Plain-language interpretation + guidance copy
- [x] Mobile, accessibility (contrast, focus-visible, reduced-motion, aria), performance
- [x] Payload optimization (zip_data 4.8→3.2 MB; single-file build 1.2→0.89 MB gz)
- [x] About / methods section (data sources, exact-vs-illustrative, limits, citations)
- [x] Neutral/academic branding pass + OpenStreetMap attribution
- [x] Deploy: **LIVE at https://psehe.github.io/no2-exposure-suite/** — GitHub Pages
      from `docs/` on `main` (PSEHE/no2-exposure-suite, public).
      Rebuild + redeploy: `npm run build --prefix explorer && cp explorer/dist/index.html docs/ && git commit -am "rebuild" && git push`.
      (A GitHub Actions auto-deploy can be added later once the token has `workflow` scope.)
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
