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

## Phase 4 — Persily full-suite homes + floor-area selection/interpolation
Goal: the user sets home parameters incl. a **total floor area** and the tool selects/interpolates
the right floorplan. Library = full NIST Persily set: **209 CS-11** (pre-2000) + **18 TN 2329**
(2000-and-later, new construction). The 18 are a vintage *supplement*, not a replacement.
Decisions: pool both; vintage axis = pre-2000 / 2000+; metadata from TN 2329 App B (optional);
**no** leakage re-derivation (keep each home's as-shipped envelope; preserves Sci-Adv calibration).

### 4a. Transformer + validation  ✅
- [x] `core/transform.py: apply_modifications(model)` — raw NIST → Sci-Adv form in memory:
      NO₂ decay −2.416e-4/s; bidirectional 1000 m³/h mixing fan-pairs on interior doorways +
      stairwells; NFRC `std_win_open` windows on bedroom/living/dining/kitchen exterior walls;
      drop zero-volume AHS phantom zones. Idempotent (no-op on the modified 24). Kitchen source
      injected at simulate time via `kitchen_zone` (studios fall back to the living zone).
- [x] `prj.py`: parse `levels` + `model.floor_area_m2()` (conditioned area; matches RECS/AHS sqft).
- [x] **Golden validation**: transform(raw CS-11 X) reproduces modified `floorplans/X` kitchen
      peak to ~1% on 13/15 single-family (AH-21, DH-7 are ground-truth artifacts with 0 fan paths;
      APTs deferred). ACH median ratio 1.02. All 208 non-giant homes simulate; 0 failures.

### 4b. Vendored library + manifest  ✅
- [x] Vendor 227 raw `.prj` under `floorplans/persily/{cs11/<AH,DH,MH,APTS>, tn2329_2000plus}` (17 MB).
- [x] `core/persily.py` loader + `web_data/persily_manifest.json` (type, vintage, floor area,
      stories, zones, single-dwelling vs building). **152 single dwellings** (AH 59 / DH 88 / MH 5),
      928–3896 ft², all simulatable; **75 APT buildings** → Phase 4d.

### 4c. CONTAM-Lite "describe your home" panel  ✅
- [x] New "Choose your home by" modes: **Describe your home** (type + stories + floor-area entry →
      bracket the two nearest homes by area within type, run the live engine on both, **interpolate
      the outputs**; shows the two source homes + weight), **Browse homes** (152-home picker), and
      **Upload a .prj** (kept). Vintage filter auto-shows only when >1 vintage is live.
- [x] Verified live (Streamlit): e.g. Detached 1500 ft² → interpolates DH-9 (1152) + DH-63 (1728),
      kitchen peak 257 ppb / ACH 0.37; all controls render; results bounded.
- [x] **Fixed a runaway (was a detection bug, not physics)**: a 159k-ppb peak traced to the stove
      being injected into a 0.11 m³ phantom AHS node `exh-Kitchen(Ret)` — because the real kitchen
      was misspelled `kithen` (missed) and the exhaust node *contained* "kitchen" (matched). Fixed
      `kitchen_zone_id` (require a living zone; tolerate the `kithen` typo) + `_drop_phantom_zones`
      (drop AHS supply/return/exhaust nodes by name). Windows + interzone mixing were always added
      correctly (12–17 windows / 16–20 fan-paths per 2024 home; ACH 0.01→8–20 as windows open).
- [x] Re-enabled the 12 single-family 2000+ homes: **152 simulatable single dwellings**, max baseline
      kitchen peak 332 ppb (no runaways), vintage axis live in the UI. Golden validation unchanged.
- [~] Residual: new-construction homes are tight (closed-window ACH ~0.01) and lack modeled mechanical
      ventilation, so their *closed-window long-term* NO₂ is conservative (UI note added). **4d** makes
      it realistic. Peaks are physical and they ventilate normally when windows open.

### 4d. Mechanical ventilation (AHS), scheduled  ✅
- [x] `prj.py` parses `simple AHS` (zr/zs nodes) + each path's `flag`/`a#`/`Fahs` airflow.
- [x] `transform._mechanical_ventilation`: per-room net exhaust = return − supply `Fahs` (per AHS);
      recirculating systems (central) net ~0 (mixing, ignored); exhaust-only (kitchen/bath fans)
      net a real extract. Computed on RAW homes only — the modified 24 are left AHS-free (preserves
      their calibration). CS-11 = 0 (balanced recirc); the 12 new-construction homes get ~340 m³/h.
- [x] `airflow.solve_airflow`: net exhaust added as a constant mass sink in the Newton solve →
      building depressurizes → makeup infiltration rises → realistic ACH. `transport`: exhaust pulls
      NO₂ out; makeup is outdoor air.
- [x] **Scheduled** (not continuous): `transport.simulate` is regime-aware — solves the airflow once
      per fan state and integrates piecewise. Kitchen fan runs while cooking, bath fans during showers
      (`DEFAULT_SHOWER`). Empty-`mech_extract` homes (CS-11 + the 24) collapse to one regime →
      unchanged. Result: new-construction day-avg ACH 0.01–1.5, kitchen peaks 117–226, day-avg 6.7–14.6
      (tight, ventilation spikes during cooking/showers). **Golden validation unchanged.** Verified in UI.

### 4d-apt. Apartments — full-building stack effect  ✅
- [x] `core/apartments.py`: full-building multizone solve (stack effect emerges from zone heights +
      the stairwell — no special-casing). Identifies floors, units (by trailing tag, excluding shared
      circulation zones), and the occupant's unit/kitchen.
- [x] CONTAM-Lite "Apartment building" mode: building selector + **"which floor do you live on?"** +
      unit selector → full-building sim → occupant's unit reported (by zone id, handles repeated names).
      Stack varies exposure by floor (e.g. APT-69 floor 1 day-avg 20.7 vs floor 6 19.0). Verified in UI.
- [x] Covers the **49 tractable buildings** (≤200 zones, ≥2 floors). The 20 tall high-rises
      (11–21 storeys, >200 zones) are deferred to a reduced-order stack column.

### 4e. Explorer floor-area selection  ✅
- [x] `export_web_data.build_archetypes` adds `floor_area_ft2` + `stories` to the 24 (full parse);
      `web_data/archetypes.json` regenerated.
- [x] `engine.js`: `homesByArea`/`bracketByArea`/`interpExposure` — bracket the two homes of a type
      nearest in floor area and linearly blend their exposure outputs.
- [x] `App.svelte`: home described by type + a **total-floor-area slider** (range adapts per type);
      shows "interpolating between X and Y"; ZIP sets type+area. Verified: exposure moves with area
      (DH 1,032 ft² → worst 1-hr 50 ppb; 2,072 ft² → 38 ppb). Built (930 KB gz), no console errors.
- [~] Caveats for the UI pass: MH homes are all 928 ft² (degenerate slider); the 24's APT areas are
      building-scale (2,798–32,443 ft²), not unit-scale. Not yet copied to docs/ or deployed.

---
*Drafted by Claude with prompts engineered by Yannai Kashtan*
