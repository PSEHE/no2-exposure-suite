"""Paths to the original CONTAM project data.

These large files are read-only and are NOT copied into the repo. Adjust the
paths here if the source folders move. `core.export_web_data` reads from these
and writes small, widget-ready JSON into `web_data/`.
"""
from pathlib import Path

# Root of this repo (…/no2-exposure-suite)
REPO_ROOT = Path(__file__).resolve().parent.parent

# --- Original CONTAM project (Science Advances / PNAS Nexus code & data) ---
CONTAM_PROJECT = Path(
    "/Users/yannaikashtan/CONTAM/Kashtan_Wang_Nadeau_Jackson_Code_Data_Updated"
)
SCALEUP = CONTAM_PROJECT / "CONTAM_SCALEUP"

# The canonical 86,400-scenario library (24 houses, full behavioral/environmental grid)
SCENARIO_DICT_NO2 = SCALEUP / "_DICTS" / "scenario_dict_NO2.pkl"
SCENARIO_DICT_CONTA = SCALEUP / "_DICTS" / "scenario_dict_CONTA.pkl"

# Per-house metadata (room->zone mapping) and the CONTAM project (.prj) files
HOUSE_INPUTS = SCALEUP / "DATABASE_HOUSES" / "inputs.csv"
DATABASE_HOUSES = SCALEUP / "DATABASE_HOUSES"

# --- Prior widget assets (richer per-ZIP table + floorplan layout PDFs) ---
EXPOSURE_CALCULATOR = Path("/Users/yannaikashtan/Documents/Exposure_Calculator")
# 30,855 ZIPs with outdoor NO2, categorical winter/summer temp, wind probabilities,
# lat/lon, climate zone, and the full housing-stock distribution.
ZIP_TABLE = EXPOSURE_CALCULATOR / "zips_abbr_updated.csv"
FLOORPLAN_PDFS = EXPOSURE_CALCULATOR / "static" / "Floorplan_Layouts"

# --- Output: widget-ready JSON ---
WEB_DATA = REPO_ROOT / "web_data"

# --- Repo-vendored data (used when the original local data is absent, e.g. on
#     Streamlit Cloud) ---
VENDORED_FLOORPLANS = REPO_ROOT / "floorplans"
HOUSE_WEIGHTS_JSON = WEB_DATA / "house_weights.json"
SCENARIO_LIBRARY_JSON = WEB_DATA / "scenario_library.json"

# --- Persily home library (CONTAM-Lite single-home panel) ---
# 209 raw CS-11 (pre-2000) + 18 TN 2329 (2000-and-later) NIST dwellings,
# transformed at load. Separate from the 24-house scenario library above.
PERSILY_DIR = VENDORED_FLOORPLANS / "persily"
PERSILY_MANIFEST = WEB_DATA / "persily_manifest.json"


def prj_path(house):
    """Path to a floorplan's .prj — the original if present, else the vendored copy."""
    orig = DATABASE_HOUSES / house / f"{house}.prj"
    return orig if orig.exists() else VENDORED_FLOORPLANS / f"{house}.prj"
