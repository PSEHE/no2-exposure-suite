"""Calibrate the engine's category->physical mapping against the CONTAM library.

Coordinate descent over the air-exchange parameters (wind speeds, seasonal
temperatures, window-open fractions, two-way door coefficient, wind-pressure
scale). The per-use emission scale is linear in concentration, so it is solved
in closed form each evaluation (the scale that centers each use's day-average).

Objective: median absolute % error of occupancy-weighted day-average NO2 vs the
library, across a fixed scenario sample on a few fast (small) houses.

Run:  python -m core.calibrate
"""
from __future__ import annotations

import itertools
import random

import numpy as np

from . import airflow, validate as V, library as lib, prj, config

HOUSES = ["MH-3", "DH-29", "AH-8"]   # small/fast houses for the fit
HOODS = ["NoHood", "50CE"]
USES = ["low", "med", "medNoBk", "high"]
WINDOWS = ["closed", "moderate", "open"]
TEMPS = ["COLD", "COOL", "RT", "WARM"]
WINDS = ["STILL", "BREEZE", "WINDY"]
OCS = ["median", "ninetyfifth_kitchen"]


def _sample(n=45, seed=0):
    random.seed(seed)
    allc = list(itertools.product(HOODS, USES, WINDOWS, TEMPS, WINDS, OCS))
    return random.sample(allc, n)


def _load():
    models = {h: prj.parse_prj(config.DATABASE_HOUSES / h / f"{h}.prj") for h in HOUSES}
    no2, _ = lib.load_library()
    return models, no2


def evaluate(models, no2, scen, metric="dayavg"):
    """Run the engine over the sample at unit emission, solve optimal per-use
    emission scale, and return (median %err, per-use scales, all %errs)."""
    # 1) unit-emission predictions (USE_SCALE = 1 for all cooking uses)
    saved = dict(V.USE_SCALE)
    for u in USES:
        V.USE_SCALE[u] = 1.0
    rows = []  # (house, use, pred_unit, lib)
    try:
        for h in HOUSES:
            m = models[h]
            for (hood, use, window, temp, wind, oc) in scen:
                p = V.predict(m, h, hood, use, window, temp, wind, oc)[metric]
                r = no2[lib.scenario_key(h, hood, use, window, temp, wind, oc)][metric]
                if r > 0.5:
                    rows.append((use, p, r))
    finally:
        V.USE_SCALE.update(saved)
    # 2) optimal per-use scale = median(lib / pred_unit)
    scales = {}
    for u in USES:
        ratios = [r / p for (use, p, r) in rows if use == u and p > 1e-9]
        scales[u] = float(np.median(ratios)) if ratios else 1.0
    # 3) error with optimal scales applied
    errs = [abs(p * scales[use] - r) / r * 100 for (use, p, r) in rows]
    return float(np.median(errs)), scales, np.array(errs)


def calibrate():
    models, no2 = _load()
    scen = _sample()
    # candidate grids per parameter
    grid = {
        ("wind", "STILL"): [0.0, 0.5, 1.0, 1.5],
        ("wind", "BREEZE"): [2.0, 3.0, 4.0, 5.0],
        ("wind", "WINDY"): [5.0, 7.0, 9.0],
        ("door_cd",): [0.2, 0.35, 0.5, 0.65],
        ("wind_mod",): [0.3, 0.5, 0.7, 1.0],
        ("win", "moderate"): [0.03, 0.06, 0.1, 0.15],
        ("win", "open"): [0.3, 0.5, 0.7, 1.0],
        ("temp", "COLD"): [-5.0, 0.0, 3.0],
        ("temp", "WARM"): [27.0, 32.0],
    }

    def get(key):
        if key[0] == "wind": return V.WIND_MS[key[1]]
        if key[0] == "win": return V.WINDOW_OPEN[key[1]]
        if key[0] == "temp": return V.TEMP_C[key[1]]
        if key[0] == "door_cd": return airflow.DOOR_CD
        if key[0] == "wind_mod": return airflow.WIND_MOD

    def setp(key, val):
        if key[0] == "wind": V.WIND_MS[key[1]] = val
        elif key[0] == "win": V.WINDOW_OPEN[key[1]] = val
        elif key[0] == "temp": V.TEMP_C[key[1]] = val
        elif key[0] == "door_cd": airflow.DOOR_CD = val
        elif key[0] == "wind_mod": airflow.WIND_MOD = val

    best_err, best_scales, _ = evaluate(models, no2, scen)
    print(f"start: median day-avg err = {best_err:.1f}%")
    for it in range(3):
        for key, cands in grid.items():
            cur = get(key)
            trials = []
            for v in cands:
                setp(key, v)
                err, scales, _ = evaluate(models, no2, scen)
                trials.append((err, v, scales))
            err, v, scales = min(trials, key=lambda x: x[0])
            if err < best_err - 0.05:
                best_err, best_scales = err, scales
                setp(key, v)
            else:
                setp(key, cur)
        print(f"iter {it}: median day-avg err = {best_err:.1f}%  "
              f"(wind={V.WIND_MS}, win={V.WINDOW_OPEN}, cd={airflow.DOOR_CD}, "
              f"wmod={airflow.WIND_MOD}, temp={V.TEMP_C})")
    print("\nbest USE_SCALE:", {u: round(s, 3) for u, s in best_scales.items()})
    print("best WIND_MS:", V.WIND_MS)
    print("best WINDOW_OPEN:", V.WINDOW_OPEN)
    print("best TEMP_C:", V.TEMP_C)
    print("best DOOR_CD:", airflow.DOOR_CD, "WIND_MOD:", airflow.WIND_MOD)
    return best_err, best_scales


if __name__ == "__main__":
    calibrate()
