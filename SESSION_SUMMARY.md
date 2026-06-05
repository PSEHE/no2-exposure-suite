# Session Summary & Handoff — NO₂ Exposure Suite

*Updated 2026-06-04. Written so a fresh session can take over with no prior context.*

## ▶ NEXT SESSION — START HERE
**The user will describe UI issues to fix** across the two apps (Explorer = public widget,
CONTAM-Lite = research Streamlit). Begin by asking them to describe the UI issues, then implement.
The physics/feature work (Phase 4) is done; this is a UI/UX polish pass. To run the apps while
working: `python3 -m streamlit run contam-lite/app.py` (port 5181) and
`npm run dev --prefix explorer` (port 5180) — or use the `.claude/launch.json` configs.

After the UI pass, **deploy the Explorer**:
`npm run build --prefix explorer && cp explorer/dist/index.html docs/ && git commit -am "…" && git push`
(GitHub Pages serves `docs/`; ~1 min to go live at psehe.github.io/no2-exposure-suite).

*Update 2026-06-05: Persily full-suite integration COMPLETE — Phase 4a–4e (see `PLAN.md`): the .prj
transformer (validated to ~1% vs the paper homes), the 227-home library, CONTAM-Lite floor-area
selection + interpolation, scheduled mechanical ventilation (each home's own AHS exhaust fans),
the full-building apartment stack with a "which floor do you live on?" selector, and the Explorer
floor-area slider that interpolates among the 24. 4a–4d-apt are committed + pushed (55f22b4); 4e is
committed but NOT yet deployed to docs/ (do that as part of the UI-pass deploy above). Still open:
a reduced-order model for tall apartment high-rises (>200 zones, deferred); the deprecated
`use_container_width`→`width='stretch'` cleanup in contam-lite/app.py.*

---

## 1. What this project is

Two interactive tools built from the multizone **CONTAM** NO₂ modeling behind:
- Kashtan et al. 2024, *Sci. Adv.* 10, eadm8680 (`papers/sciadv.adm8680.pdf`) — NO₂ exposure, health, disparities from gas/propane stoves; the CONTAM methods are here.
- Kashtan et al. 2025, *PNAS Nexus* 4, pgaf341 (`papers/pgaf341.pdf`) — indoor+outdoor NO₂ by ZIP.

**Repo:** `~/CONTAM/no2-exposure-suite` → GitHub **`PSEHE/no2-exposure-suite`** (public, branch `main`, ~19 commits). `PLAN.md` tracks everything with checkboxes; read it.

### Product 1 — Explorer (public, LIVE)
- **https://psehe.github.io/no2-exposure-suite/** — single-file HTML widget (`explorer/`, Vite + Svelte 5 + D3, built to `docs/index.html`, served by GitHub Pages from `docs/`).
- Hybrid engine: **exact lookup** of the 86,400-scenario CONTAM library (embedded JSON) for the 6 grid knobs + an **anchored box-model** for continuous knobs. Address/ZIP personalization. Outdoor handling: full outdoor while outdoors + ventilation-consistent penetration indoors (calibrated so opening windows lowers exposure under clean outdoor).
- Rebuild + redeploy: `npm run build --prefix explorer && cp explorer/dist/index.html docs/ && git commit -am "rebuild" && git push` (Pages auto-redeploys in ~1 min).
- `explorer/src/lib/`: `engine.js` (lookup + ZIP weighting + archetype select + outdoor penetration), `boxmodel.js` (24-h curve), `constants.js`.

### Product 2 — CONTAM-Lite (research, deploy-ready, NOT yet deployed)
- `contam-lite/app.py` (Streamlit). Run: `pip install -r requirements.txt && streamlit run contam-lite/app.py` (preview port 5181 via `.claude/launch.json`).
- **Two panels** (sidebar radio): **Single-home** (live physics engine; pick an archetype OR upload a `.prj`) and **Population & health** (reweight the library by population sliders → exposure + asthma/mortality/cost).
- Deploy is one GitHub-OAuth click on share.streamlit.io (repo + `contam-lite/app.py`); see README. The user is resolving Streamlit auth.

---

## 2. The physics engine (`core/`) — the real CONTAM port

Pipeline: **parse `.prj` → solve airflow network → integrate contaminant transport.**

- `core/prj.py` — parser. `parse_prj(path)` / `parse_prj_text(text, label)` → `PrjModel` (zones+volumes, flow elements, flow paths, source/sinks, species, kinetic-reaction decay, wind-pressure profiles). Scenario macros `$(TEMP)/$(WIND)/$(WINDOW)/$(USE)/$(HOOD)` kept symbolic as `Macro`. Handles the standard CONTAM text format; **skips** ducts/controls.
- `core/airflow.py` — `solve_airflow(model, T_out_C, wind_ms, wind_dir, window_open, ...)`. Newton-Raphson on zone pressures; power-law leak/orifice/door elements `w=C·sign(ΔP)·|ΔP|^n` (C=`params[1]`, n=`params[2]`), stack effect, wind pressure from Cp profiles. **Two-way density-driven flow through open windows** (critical fix). Constant-volume fans (1000 m³/h interzone doors) returned for transport. Returns zone pressures, per-zone + whole-home air exchange, path flows, fans. Calibrated globals `DOOR_CD=0.35`, `WIND_MOD=0.3`.
- `core/transport.py` — `simulate(model, T_out_C, wind_ms, window_open, hood, cooking, C_out_ppb, emission_scale, kitchen_zone, ...)`. Builds the volumetric flow matrix, adds fan mixing + cooking sources + first-order decay (−2.4×10⁻⁴/s), integrates exactly via matrix exponential over 24 h. Returns per-zone time series + summary. `kitchen_zone=<id>` injects a standard cooktop+oven (for custom floorplans without NO₂ sources). Hood capture: NoHood 1.0 / 25CE .75 / 50CE .5 / 75CE .25.
- `core/library.py` — loads the 86,400-scenario library (`scenario_dict_{NO2,CONTA}.pkl`) **OR rebuilds from `web_data/scenario_library.json`** if the pickles are absent (cloud). Scenario key: `{house}_{hood}_{use}_{window}_{temp}_{wind}_{oc}`. Also: ZIP-weighted exposure, `select_archetype` (port of `select_floorplan`).
- `core/population.py` — reweights the library by population distributions (housing stock via `house_weights.json`, hood adoption, cooking intensity, home size, climate) → mean stove NO₂; `health_outcomes` (Lin asthma OR 1.32, Atkinson mortality RR 1.02/10µg/m³, VSL) anchored to the papers' ≈50k asthma / ≈19k deaths / ≈$250B at 38% prevalence.
- `core/validate.py` + `core/calibrate.py` — validation harness (occupancy-weighting port + library comparison) and coordinate-descent calibration of the category→physical mapping.
- `core/constants.py` (unit conv, decay, benchmarks, emission, **epi = Lin 2013 + Atkinson**), `core/config.py` (paths + `prj_path(house)` vendored fallback), `core/export_web_data.py` (emits `web_data/`).

### Key physics facts
- NO₂ decay −2.42×10⁻⁴/s (`.prj` kinetic reactions). CONTA = dummy pollutant, outdoor=100 ppb → `CONTA/100` = outdoor→indoor penetration; CONTB = air-exchange tracer.
- Burner NO₂ rate 3.19×10⁻⁸ kg/s (~115 mg/h). Oven 3.67×10⁻⁸.
- Air exchange ~0–6/h; kitchen peaks 150–400 ppb (matches papers).

### Honest fidelity status
Engine is physically faithful (dynamics, magnitudes, intervention responses correct). Exact match to the stored library: **~17% median day-avg on the 3 calibrated houses, ~40% on held-out**, with a per-type bias (MH/DH over-predict ~1.4×, AH/APT under ~0.86×). A uniform 10–15% isn't reachable globally because **the original scenario-generation macros are not in the repo** (they ran on a Windows machine, `D:/…`, only `simread` post-processing is here) and the ASHRAE-default leakage model has house-specific deviations. User accepted the physically-faithful engine as-is.

---

## 3. Data

**Vendored in the repo (works anywhere, incl. cloud):**
- `floorplans/*.prj` — the **24** archetype floorplans.
- `web_data/scenario_library.json` (3.1 MB, 86,400 × metrics), `zip_data.json` (3.2 MB), `archetypes.json` (24-house metadata: type, volumes, rooms), `house_weights.json` (24-house population weights).

**Original local data (NOT in repo; read by `core/config.py` when present):**
- `~/CONTAM/Kashtan_Wang_Nadeau_Jackson_Code_Data_Updated/CONTAM_SCALEUP/` — `_DICTS/scenario_dict_{NO2,CONTA}.pkl`, `DATABASE_HOUSES/{house}/{house}.prj` + `inputs.csv` (room→zone map for the 24), `Occupancy/*.csv`.
- `~/Documents/Exposure_Calculator/zips_abbr_updated.csv` — per-ZIP outdoor NO₂/climate/wind (used by the Explorer's `zip_data`).
- Note: `DATABASE_HOUSES` also contains extra homes (DH-33, APT-16, …) NOT in the 24; `DH-33` retains per-scenario time-series CSVs (the others were cleaned).

---

## 4. ⭐ NEXT TASK: incorporate Persily's full floorplan set

**Context.** The 24 archetypes were *selected from a pool of 209 residences* modeled in CONTAM by **Persily et al., "A Collection of Homes to Represent the U.S. Housing Stock"** (NIST). The user wants to use the **full Persily set** and **will provide the `.prj` files** in the new session.

**Goal:** make the Persily floorplans usable in **CONTAM-Lite's single-home panel** (the physics engine runs any `.prj`). This is independent of the Explorer's exact-lookup library and the population panel, which stay on the 24 (there are no precomputed CONTAM results for the other Persily homes).

**Suggested steps:**
1. **Get the files** from the user; drop them in `floorplans/persily/` (or similar). Note their count and naming.
2. **Test the parser** on each: `core.prj.parse_prj`. Persily homes may use elements/sections the parser skips (ducts, more AHS, controls). Confirm zones/paths/elements parse; log any failures. Extend the parser only if needed.
3. **Test the engine** (`transport.simulate`) on a sample: does the airflow Newton-Raphson converge, and are kitchen peaks / ACH physically sensible? Watch for homes with duct HVAC (not modeled) or unusual geometry.
4. **Raw vs. modified `.prj`** — important for fidelity. The 24 here were **modified** per the Sci. Adv. Methods: added one NFRC window (1.2 m × 1.5 m) per bedroom/living/kitchen on an exterior wall; replaced open interior doors with **bidirectional 1000 m³/h** flow; added a kitchen NO₂ source (`burn_NO2`) + oven source; removed pre-existing range hoods (hood modeled as emission reduction). **Ask the user whether the Persily files are raw or already modified.** If raw, either apply the same modifications programmatically (write a `.prj` transformer) or run them as-is and inject the kitchen source via `kitchen_zone` (less faithful — no added windows / interzone mixing).
5. **Identify the kitchen zone** per home. `contam-lite/app.py:kitchen_zone_name()` / `has_no2_source()` look for an NO₂ source or a zone literally named `kitchen`. Raw Persily homes likely have **no NO₂ source** → need kitchen identification (by zone name? Persily zone-naming convention?) to drive the `kitchen_zone` injection. Verify how Persily names zones.
6. **Metadata + picker.** Generate volumes/rooms/type for the new homes (extend `core/export_web_data.build_archetypes`, or a new script) and give **CONTAM-Lite its own floorplan list** (e.g., scan `floorplans/`). **Do NOT append to `constants.HOUSES`** — that list of 24 is hard-wired to the scenario-library indexing, the population weighting, and `select_archetype`; breaking it would corrupt the Explorer + population panel. Keep "library houses" (24) separate from "all available floorplans" (24 + Persily).
7. **Scope guardrails:** the Explorer (lookup) and the population panel reweight the **24-house library** only — leave them unchanged. The Persily expansion is a CONTAM-Lite single-home feature (engine runs any geometry). Fidelity on Persily homes is unvalidated (no library to compare against) — note this in the UI.

---

## 5. Optional backlog
- Deploy CONTAM-Lite to Streamlit Cloud (pending the user's auth).
- Per-archetype correction factors to tighten engine→library fidelity toward 10–15%.
- ZIP/county maps; Explorer address Tier-2 (RentCast/ATTOM property lookup + serverless proxy).

---
*Drafted by Claude with prompts engineered by Yannai Kashtan*
