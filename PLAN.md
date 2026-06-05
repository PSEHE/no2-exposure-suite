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
### Post-launch fix (outdoor component)  ✅
- [x] Outdoor exposure: full outdoor while outdoors + ventilation-consistent penetration indoors;
      window opening now lowers exposure under clean outdoor; electric comparison consistent. Live.

### 1d. Polish  ✅ (deployed)
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
- [x] `.prj` parser (`core/prj.py`): zones, flow elements + paths, source/sinks,
      species, kinetic-reaction decay, wind-pressure profiles, symbolic macros.
      Verified on all 24 houses (decay −2.42e-4/s, CONTA ambient 100 ppb, fan 1000 m³/h).
- [x] Airflow network solver (`core/airflow.py`): Newton-Raphson on zone pressures,
      power-law leak/orifice/door elements, stack effect + wind-pressure profiles.
      Verified: converges 7→170 zones (<100 ms), ACH 0–6/h, correct response to
      temp/wind/window; varies sensibly by house (MH leaky, APT sealed).
- [ ] Calibrate category→physical mapping (COLD/STILL/window levels → temp °C / wind m/s /
      open-fraction; tune wind-pressure modifier) against the library's CONTA-derived air exchange
- [x] Contaminant transport ODE integrator (`core/transport.py`): volumetric flow matrix from
      the airflow solve + 1000 m³/h interzone fan mixing + cooking sources + first-order decay,
      integrated exactly via matrix exponential. Verified: kitchen NO₂ buildup→decay with peaks
      ~150–400 ppb (matches the papers), 75% hood → 75% peak cut, ventilation lowers exposure,
      sealed apartments worse than leaky homes.
- [ ] Calibrate category→physical mapping + validation harness vs stored 86,400 library
- [ ] Hit ~10–15% fidelity; report error distribution
- NOTE: scenario-generation macro VALUES ($(TEMP)/$(WIND)/$(WINDOW)/$(USE)) are
  NOT in the repo (generated on a Windows machine). Engine will take physical
  units directly; validation requires calibrating category→physical mapping
  against the library (per-scenario time-series in Results_NO2/ enable this).

## Phase 3 — CONTAM-Lite app
- [x] Streamlit single-home panel (`contam-lite/app.py`): floorplan picker + physical knobs
      (temp, wind, outdoor NO₂, window, hood, cooking pattern, burner intensity) → live per-zone
      24-h plot + metrics (ACH, kitchen peak / max-1hr / daily) + by-room table + benchmarks.
      Verified running (DH-1: dinner peak 156 ppb, max-1hr 105). Run: `streamlit run contam-lite/app.py`.
- [x] Validation harness (`core/validate.py`) + calibration (`core/calibrate.py`, coordinate
      descent). Fixed a major bug — two-way density-driven flow through open windows (24×→2.8×) —
      and calibrated the category→physical mapping + per-use emission.
      Fidelity (day-avg median): **~17% on the 3 fit houses, but ~40% on held-out houses** with a
      systematic per-type bias (MH/DH over-predict ~1.4×, AH/APT under-predict ~0.86×). Conclusion:
      a single global mapping can't reach a uniform 10–15% across all 24 archetypes — the original
      generation macros aren't in the repo AND the ASHRAE-default leakage model has house-specific
      deviations from CONTAM. The engine is physically faithful (dynamics, magnitudes, intervention
      responses all correct); exact-library match is house-dependent. Options to go further:
      per-archetype correction factor, or recover the original macros. (Domain-expert call.)
- [x] Population panel (`core/population.py` + app): population sliders (gas/propane prevalence,
      hood adoption, cooking intensity, home size, climate) → reweight the exact library →
      population-mean stove NO₂. Verified baseline ≈ 2.8 ppb (papers ~2.4).
- [x] Health outcomes: pediatric asthma (Lin gas-cooking OR 1.32), adult mortality (Atkinson
      RR 1.02/10µg/m³), societal cost (VSL + asthma). Anchored to the papers' national estimates;
      default view reproduces ≈50k asthma / ≈19k deaths / ≈$250B at 38% prevalence.
- [x] Custom floorplan support: upload a CONTAM `.prj` in the single-home panel; if it has no
      NO₂ source, pick a kitchen zone and a standard cooktop+oven is injected.
- [x] Deploy-ready for Streamlit Cloud: vendored 24 floorplans (`floorplans/`) + `house_weights.json`,
      library/floorplan loaders fall back to repo data (verified app runs with original data absent),
      root `requirements.txt` + `.streamlit/config.toml`. Final deploy = one click on share.streamlit.io
      (select repo + `contam-lite/app.py`) — needs the user's GitHub OAuth.
- [ ] (Optional) ZIP/county maps; Explorer address Tier-2; per-archetype fidelity tuning

---
*Drafted by Claude with prompts engineered by Yannai Kashtan*
