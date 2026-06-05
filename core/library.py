"""Load and query the CONTAM scenario library and the per-ZIP outdoor/climate table.

The scenario library is the 86,400-entry grid of exact CONTAM results:
    key = "{house}_{hood}_{use}_{window}_{temp}_{wind}_{oc}"
    value = {"peak", "hravg", "eighthravg", "dayavg"}  (ppb)
Two libraries exist: NO2 (indoor stove source, outdoor = 0) and CONTA (a dummy
pollutant with NO2's physics, outdoor = 100 ppb). CONTA/100 is the fraction of
outdoor NO2 that penetrates indoors, so:
    outdoor-attributable indoor exposure = (CONTA / 100) * outdoor_NO2.
"""
from __future__ import annotations

import functools
import pickle

import pandas as pd

from . import config, constants

# --- Friendly UI labels -> scenario tokens (reused/extended from the prior widget) ---
HOOD_LABELS = {
    "No hood / rarely used / recirculating": "NoHood",
    "Standard hood, regularly used": "25CE",
    "High-efficiency hood, used consistently": "75CE",
}
USE_LABELS = {
    "Light / reheating (1–2 people)": "low",
    "Average cooking (2–4 people)": "med",
    "Heavy cooking, most meals (5+ people)": "high",
    "No cooking": "zero",
}
WINDOW_LABELS = {
    "Closed all the time": "closed",
    "Open ~4 hours/day": "moderate",
    "Open all the time": "open",
}
OCCUPANCY_LABELS = {
    "Little time in kitchen (5 min/day)": "fifth_kitchen",
    "Typical (35 min in kitchen/day)": "median",
    "A lot of time in kitchen (2.5 h/day)": "ninetyfifth_kitchen",
    "Rarely home (8 h/day outdoors)": "ninetyfifth_outside",
    "Almost always home": "fifth_outside",
}


# --- Loaders (cached) ------------------------------------------------------
@functools.lru_cache(maxsize=1)
def load_library():
    """Return (no2_dict, conta_dict). Uses the original pickles if present,
    else rebuilds from the repo-vendored web_data/scenario_library.json (so the
    app works on hosts without the original data, e.g. Streamlit Cloud)."""
    try:
        with open(config.SCENARIO_DICT_NO2, "rb") as f:
            no2 = pickle.load(f)
        with open(config.SCENARIO_DICT_CONTA, "rb") as f:
            conta = pickle.load(f)
        return no2, conta
    except (FileNotFoundError, OSError):
        return _load_library_from_json()


def _load_library_from_json():
    import json
    with open(config.SCENARIO_LIBRARY_JSON) as f:
        d = json.load(f)
    s = d["schema"]
    no2, conta, i = {}, {}, 0
    for h in s["houses"]:
        for hd in s["hood"]:
            for u in s["use"]:
                for w in s["window"]:
                    for t in s["temp"]:
                        for wd in s["wind"]:
                            for oc in s["oc"]:
                                k = f"{h}_{hd}_{u}_{w}_{t}_{wd}_{oc}"
                                no2[k] = {"peak": d["no2"]["peak"][i], "hravg": d["no2"]["hravg"][i],
                                          "eighthravg": d["no2"]["eighthravg"][i], "dayavg": d["no2"]["dayavg"][i]}
                                conta[k] = {"hravg": d["conta"]["hravg"][i], "dayavg": d["conta"]["dayavg"][i]}
                                i += 1
    return no2, conta


@functools.lru_cache(maxsize=1)
def load_zip_table():
    """Per-ZIP outdoor NO2, climate, wind distribution, and housing stock."""
    df = pd.read_csv(config.ZIP_TABLE)
    df = df.dropna(subset=["ZIP"]).copy()
    df["ZIP"] = df["ZIP"].astype(int)
    return df.set_index("ZIP", drop=False)


# --- Scenario lookup -------------------------------------------------------
def scenario_key(house, hood, use, window, temp, wind, oc):
    return f"{house}_{hood}_{use}_{window}_{temp}_{wind}_{oc}"


def lookup(house, hood, use, window, temp, wind, oc):
    """Exact CONTAM result for one fully-specified scenario."""
    no2, conta = load_library()
    k = scenario_key(house, hood, use, window, temp, wind, oc)
    return {"no2": no2[k], "conta": conta[k]}


# --- ZIP info --------------------------------------------------------------
def zip_info(zipcode):
    """Outdoor NO2 (ppb), climate temps, wind probabilities, and location for a ZIP."""
    df = load_zip_table()
    z = int(zipcode)
    if z not in df.index:
        return None
    r = df.loc[z]
    if isinstance(r, pd.DataFrame):  # duplicate ZIPs -> take first
        r = r.iloc[0]
    return {
        "zip": z,
        "outdoor_no2_ppb": float(r["no2_ppb"]),
        "winter_temp": r["winter_temp"],
        "summer_temp": r["summer_temp"],
        "wind": {"STILL": float(r["still"]), "BREEZE": float(r["breeze"]),
                 "WINDY": float(r["windy_grouped"])},
        "climate": r["DOE Climate Zone"],
        "city": r["city"],
        "state": r["STATE"],
        "lat": float(r["lat"]),
        "lon": float(r["lon"]),
    }


# --- ZIP-weighted annual exposure ------------------------------------------
def weighted_annual_exposure(house, hood, use, window, oc, zipcode):
    """Annual-representative exposure for a home in a given ZIP.

    Averages the exact scenarios over the ZIP's two seasonal temperatures
    (50/50) and its wind distribution, then adds the outdoor contribution via
    the CONTA penetration fraction. Mirrors the prior Flask widget's approach
    (the canonical library has no separate air-handler dimension).
    """
    no2, conta = load_library()
    info = zip_info(zipcode)
    if info is None:
        return None
    outdoor = info["outdoor_no2_ppb"]
    temps = [info["summer_temp"], info["winter_temp"]]
    winds = info["wind"]

    stove_long = outdoor_long = stove_hr_w = 0.0
    stove_hr_max = 0.0
    total_w = 0.0
    for temp in temps:
        for wind, p in winds.items():
            coef = 0.5 * p
            if coef == 0:
                continue
            k = scenario_key(house, hood, use, window, temp, wind, oc)
            stove_long += no2[k]["dayavg"] * coef
            outdoor_long += coef * outdoor * conta[k]["dayavg"] / 100.0
            stove_hr_w += no2[k]["hravg"] * coef
            stove_hr_max = max(stove_hr_max, no2[k]["hravg"])
            total_w += coef

    return {
        "stove_longterm_ppb": stove_long,
        "outdoor_longterm_ppb": outdoor_long,
        "total_longterm_ppb": stove_long + outdoor_long,
        "stove_acute_hr_ppb": stove_hr_w,            # distribution-weighted 1-hr
        "stove_acute_hr_max_ppb": stove_hr_max,      # worst-case 1-hr over climate/wind
        "total_acute_hr_ppb": stove_hr_w + outdoor_long,  # + outdoor baseline (approx)
        "outdoor_no2_ppb": outdoor,
        "weight_sum": total_w,
    }


# --- Archetype selection (port of select_floorplan from CONTAM_SCALEUP.ipynb) ---
_OLDER = ("vintage_prior_1940", "vintage_1940_1959", "vintage_1960_1979")


def select_archetype(typehuq, sqftrange, vintage, stories, central_ac):
    """Map household characteristics to one of the 24 CONTAM floorplans.

    typehuq:     'MH' | 'DH' | 'AH' | 'APT'
    sqftrange:   'floor_area_0_1499' | 'floor_area_1500_2499' | 'floor_area_2500_3999' | 'floor_area_4000+'
    vintage:     'vintage_prior_1940' | ... | 'vintage_2010s'
    stories:     'single_story' | anything else
    central_ac:  'yesAHS' | anything else
    """
    yes_ac = central_ac == "yesAHS"
    older = vintage in _OLDER

    if typehuq == "MH":
        if yes_ac:
            if older:
                return "MH-4"
            return "MH-1" if vintage == "vintage_1980_1999" else "MH-2"
        return "MH-3"

    if typehuq == "DH":
        if sqftrange == "floor_area_0_1499":
            if yes_ac:
                return "DH-2"
            return "DH-29" if older else "DH-42"
        if stories == "single_story":
            return "DH-7" if older else "DH-1"
        return "DH-17" if yes_ac else "DH-81"

    if typehuq == "AH":
        if sqftrange == "floor_area_0_1499":
            if stories == "single_story":
                if yes_ac:
                    return "AH-3" if older else "AH-39"
                return "AH-8"
            return "AH-1"
        if sqftrange == "floor_area_1500_2499":
            return "AH-21"
        return "AH-34"

    # APT (default)
    if sqftrange == "floor_area_0_1499":
        if yes_ac:
            return "APT-1"
        if vintage in ("vintage_prior_1940", "vintage_1940_1959"):
            return "APT-4"
        if vintage == "vintage_1960_1979":
            return "APT-5"
        if vintage in ("vintage_1980_1999", "vintage_2000_2009"):
            return "APT-3"
        return "APT-62"
    return "APT-35" if yes_ac else "APT-28"
