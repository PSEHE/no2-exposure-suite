# NO₂ Exposure Suite

**▶ Live public Explorer: https://psehe.github.io/no2-exposure-suite/**

Interactive tools for estimating residential nitrogen dioxide (NO₂) exposure from
gas and propane stoves, built on the multizone CONTAM modeling from:

- Kashtan et al. (2024), *Nitrogen dioxide exposure, health outcomes, and associated
  demographic disparities due to gas and propane combustion by U.S. stoves*,
  **Science Advances** 10, eadm8680.
- Kashtan et al. (2025), *Integrating indoor and outdoor nitrogen dioxide exposures
  in US homes nationally by ZIP code*, **PNAS Nexus** 4, pgaf341.

The published work runs NIST's CONTAM solver over 24 representative floorplans across
a full grid of behavioral and environmental scenarios. This repo turns that work into
two interactive products that are computationally inexpensive yet faithful to the
underlying CONTAM physics.

## Two products

| | **Explorer** (`explorer/`) | **CONTAM-Lite** (`contam-lite/`) |
|---|---|---|
| Audience | Public / educational | Researchers |
| Platform | Static HTML/JS (Vite + Svelte + D3) | Python web app (Streamlit) |
| Engine | **Hybrid**: exact lookup of the 86,400-scenario CONTAM library + a lightweight anchored box-model for continuous knobs | **Full physics port**: parses any `.prj`, solves the airflow network + contaminant transport, validated against the stored library |
| Scope | Single-home exposure + 24-h time-series | Single-home panel **+** population panel (exposure + health outcomes) |

## Shared foundation (`core/`)

Pure-Python package used by both products and by the data-export step:

- `config.py` — paths to the original CONTAM data (read-only; not copied into the repo).
- `constants.py` — physical and epidemiological constants from the two papers
  (unit conversions, NO₂ decay, air-exchange, emission factors, health benchmarks, risk parameters).
- `library.py` — loads the 86,400-scenario CONTAM library + the per-ZIP outdoor/climate
  table; provides lookup, ZIP-weighted annual exposure, and archetype-selection helpers.
- `export_web_data.py` — emits the trimmed, widget-ready JSON into `web_data/`.

## Data sources (not in repo)

`core/config.py` points at the original project folders:

- Scenario library: `…/Kashtan_Wang_Nadeau_Jackson_Code_Data_Updated/CONTAM_SCALEUP/_DICTS/`
- ZIP table: `…/Documents/Exposure_Calculator/zips_abbr_updated.csv`
- Floorplans (`.prj`) & layout PDFs: `…/CONTAM_SCALEUP/DATABASE_HOUSES/` and `…/Exposure_Calculator/static/Floorplan_Layouts/`

Run `python -m core.export_web_data` to regenerate `web_data/`.

## Running the apps

**Explorer** (public widget): open [the live site](https://psehe.github.io/no2-exposure-suite/),
or `cd explorer && npm install && npm run dev`. Rebuild the single file with
`npm run build --prefix explorer && cp explorer/dist/index.html docs/`.

**CONTAM-Lite** (research app):
```
pip install -r requirements.txt
streamlit run contam-lite/app.py
```
It runs from data vendored in the repo (`floorplans/`, `web_data/`), so it needs no
external files. Two panels: a single-home physics engine (pick an archetype or upload your
own `.prj`) and a population/health panel.

### Deploy CONTAM-Lite to Streamlit Community Cloud
1. Go to https://share.streamlit.io and sign in with GitHub.
2. **Create app → Deploy from GitHub**: repository `PSEHE/no2-exposure-suite`, branch `main`,
   main file `contam-lite/app.py`.
3. Deploy. (Dependencies install from the root `requirements.txt`.)

## Status

See [`PLAN.md`](PLAN.md) for the build plan and current progress.

---
*Drafted by Claude with prompts engineered by Yannai Kashtan*
