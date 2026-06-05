"""Validate the from-first-principles engine against the stored CONTAM library.

For a scenario the library indexes by category (house, hood, use, window, temp,
wind, occupancy), we:
  1. map the categories to physical conditions (the mapping below — calibrated
     against the library, since the original generation macros aren't in the repo),
  2. run the airflow + transport engine to get per-zone NO2 over the day,
  3. apply the SAME occupancy weighting the library used (concatenate the
     concentration in whichever room the person occupies; outdoor -> 0 for the
     stove-NO2 run; 'Other'/away excluded),
  4. extract peak / 1-hr / 8-hr / daily metrics and compare to scenario_dict.
"""
from __future__ import annotations

import functools
import numpy as np
import pandas as pd

from . import config, prj, transport
from . import library as lib

# --- category -> physical mapping (calibrated against the library) ---
TEMP_C = {"COLD": -5.0, "COOL": 10.0, "RT": 20.0, "WARM": 30.0}
WIND_MS = {"STILL": 0.0, "BREEZE": 5.0, "WINDY": 5.0}
WINDOW_OPEN = {"closed": 0.0, "moderate": 0.03, "open": 0.7}
USE_COOKING = {
    "zero": [],
    "low": [{"start": 7.7, "min": 10, "cooktop": True, "oven": False}],
    "med": [{"start": 7.5, "min": 15, "cooktop": True, "oven": False},
            {"start": 18.0, "min": 30, "cooktop": True, "oven": True}],
    "medNoBk": [{"start": 7.5, "min": 15, "cooktop": True, "oven": False},
                {"start": 18.0, "min": 35, "cooktop": True, "oven": False}],
    "high": [{"start": 7.0, "min": 20, "cooktop": True, "oven": True},
             {"start": 12.0, "min": 15, "cooktop": True, "oven": False},
             {"start": 18.0, "min": 40, "cooktop": True, "oven": True}],
}
# Emission scale per use, calibrated so closed-window day-avg matches the library
# (the cooking schedules above emit more than the library's per-use burner-minutes).
USE_SCALE = {"zero": 0.0, "low": 1.0, "med": 0.267, "medNoBk": 0.313, "high": 0.646}


@functools.lru_cache(maxsize=1)
def _inputs():
    return pd.read_csv(config.HOUSE_INPUTS).set_index("House")


def house_rooms(house):
    r = _inputs().loc[house]
    def z(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0
    return {"Kitchen": z(r.Kitchen), "Livingroom": z(r.Livingroom),
            "Bedroom1": z(r.Bedroom1), "Bedroom2": z(r.Bedroom2)}


@functools.lru_cache(maxsize=8)
def _occ(name):
    return pd.read_csv(config.SCALEUP / "Occupancy" / f"{name}.csv")


def occupancy_metrics(res, house, oc_name, interval=600):
    """Occupancy-weighted personal-exposure metrics from an engine result."""
    rooms = house_rooms(house)
    zone_ids = res["zone_ids"]
    col = {z: i for i, z in enumerate(zone_ids)}
    series = res["series"]              # (nsteps+1, nz) ppb
    sched = _occ(oc_name)

    path = []
    for _, row in sched.iterrows():
        start = int(row["Start"] * 3600 / interval)
        stop = int(row["Stop"] * 3600 / interval)
        room = row["Room"]
        if room == "Other":
            continue
        if room == "Outdoor":
            path.extend([0.0] * max(0, stop - start))      # stove NO2 outdoors = 0
            continue
        zid = rooms.get(room, 0)
        if zid in col:
            path.extend(list(series[start:stop, col[zid]]))
        else:
            path.extend([0.0] * max(0, stop - start))
    a = np.array(path[: int(86400 / interval)], dtype=float)
    if a.size == 0:
        return {"peak": 0, "hravg": 0, "eighthravg": 0, "dayavg": 0}
    hr = int(3600 / interval); ehr = int(28800 / interval)
    def rollmax(w):
        if a.size < w:
            return float(np.mean(a))
        return float(np.max(np.convolve(a, np.ones(w) / w, mode="valid")))
    return {"peak": float(np.max(a)), "dayavg": float(np.mean(a)),
            "hravg": rollmax(hr), "eighthravg": rollmax(ehr)}


def predict(model, house, hood, use, window, temp, wind, oc):
    res = transport.simulate(
        model, T_out_C=TEMP_C[temp], wind_ms=WIND_MS[wind],
        window_open=WINDOW_OPEN[window], hood=hood,
        cooking=USE_COOKING[use], emission_scale=USE_SCALE[use], C_out_ppb=0.0)
    return occupancy_metrics(res, house, oc)


def compare(house, scenarios, metric="dayavg"):
    """Return (predicted, library) arrays for a list of scenario-tuples."""
    model = prj.parse_prj(config.DATABASE_HOUSES / house / f"{house}.prj")
    no2, _ = lib.load_library()
    pred, ref = [], []
    for (hood, use, window, temp, wind, oc) in scenarios:
        if use == "zero":
            continue
        p = predict(model, house, hood, use, window, temp, wind, oc)
        key = lib.scenario_key(house, hood, use, window, temp, wind, oc)
        pred.append(p[metric]); ref.append(no2[key][metric])
    return np.array(pred), np.array(ref)
