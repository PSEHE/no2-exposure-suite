"""Multizone contaminant transport solver.

Given the steady airflow network (from core.airflow) plus cooking sources and
first-order decay, integrates the well-mixed per-zone NO2 mass balance over a
day:

    V_i dC_i/dt = sum_j Q_ji C_j  -  C_i (sum of outflows)  +  Q_out->i C_out
                  +  G_i(t)  -  k V_i C_i

Airflows are steady within a scenario, so the system is linear time-invariant
between cooking on/off changes. We integrate exactly with the matrix exponential
on a fixed time step (handles the fast interzone-fan mixing without instability).
"""
from __future__ import annotations

import numpy as np
from scipy.linalg import expm

from . import airflow as af

# 1 ppb NO2 = 1.88 ug/m^3 = 1.88e-9 kg/m^3
PPB_TO_KGM3 = 1.88e-9

# Range-hood capture -> fraction of stove emissions still reaching the room
HOOD_MULT = {"NoHood": 1.0, "25CE": 0.75, "50CE": 0.5, "75CE": 0.25}

# A representative "average cooking" day (hours, duration min, cooktop, oven).
DEFAULT_COOKING = [
    {"start": 7.5, "min": 15, "cooktop": True, "oven": False},
    {"start": 18.0, "min": 30, "cooktop": True, "oven": True},
]


# Standard single-burner / oven NO2 rates (kg/s), from the .prj source elements —
# used to inject a source into custom floorplans that lack NO2 sources.
STD_COOKTOP_KG_S = 3.1878e-8
STD_OVEN_KG_S = 3.66597e-8


def _no2_sources(model, kitchen_zone=None):
    """Per-zone NO2 source rates (kg/s) split into cooktop and oven.

    If kitchen_zone is given (custom floorplan), inject a standard cooktop+oven
    there. Otherwise read the model's NO2 source elements; if none exist, also
    fall back to a standard source in kitchen_zone (when provided)."""
    if kitchen_zone is not None:
        return {kitchen_zone: STD_COOKTOP_KG_S}, {kitchen_zone: STD_OVEN_KG_S}
    cooktop, oven = {}, {}
    for s in model.sources:
        el = model.source_elements.get(s.element)
        if el is None or el.species != "NO2":
            continue
        rate = el.rate if isinstance(el.rate, (int, float)) else 0.0
        tgt = oven if ("ov" in el.name or "oven" in el.name.lower()) else cooktop
        tgt[s.zone] = tgt.get(s.zone, 0.0) + float(rate)
    return cooktop, oven


def simulate(model, *, T_out_C=10.0, wind_ms=2.0, wind_dir=0.0, window_open=0.0,
             hood="NoHood", cooking=None, C_out_ppb=0.0, T_in_C=23.0,
             emission_scale=1.0, kitchen_zone=None, hours=24, dt_min=10):
    """Run a day of NO2 transport. Returns time series + per-zone summary."""
    afr = af.solve_airflow(model, T_out_C=T_out_C, wind_ms=wind_ms, wind_dir=wind_dir,
                           window_open=window_open, T_in_C=T_in_C)
    zone_ids = afr["zone_ids"]
    idx = {z: i for i, z in enumerate(zone_ids)}
    nz = len(zone_ids)
    rho_ref = af.RHO_REF

    # --- volumetric flow structures (m^3/s) ---
    Qin = np.zeros((nz, nz))      # Qin[i][j] = flow from zone j into zone i
    Qout_in = np.zeros(nz)        # outdoor -> zone i
    Qout_out = np.zeros(nz)       # zone i -> outdoor
    for (a, b, w) in afr["path_flows"]:
        vol = w / rho_ref
        src, dst = (a, b) if vol >= 0 else (b, a)
        vol = abs(vol)
        if src == -1 and dst != -1:
            Qout_in[idx[dst]] += vol
        elif dst == -1 and src != -1:
            Qout_out[idx[src]] += vol
        elif src != -1 and dst != -1:
            Qin[idx[dst]][idx[src]] += vol
    for (a, b, Q) in afr["fans"]:
        if a != -1 and b != -1:
            Qin[idx[a]][idx[b]] += Q
            Qin[idx[b]][idx[a]] += Q
        elif a != -1:
            Qout_in[idx[a]] += Q; Qout_out[idx[a]] += Q
        elif b != -1:
            Qout_in[idx[b]] += Q; Qout_out[idx[b]] += Q

    V = np.array([model.zones[z].volume for z in zone_ids])
    k = -model.decay_of("NO2")    # decay_of returns a negative rate; k > 0

    # --- system matrix A: dC/dt = A C + b ---
    A = Qin / V[:, None]                          # off-diagonal inflow terms
    outflow = Qin.sum(axis=0) + Qout_out          # total flow leaving each zone
    np.fill_diagonal(A, 0.0)
    A[np.diag_indices(nz)] = -(outflow / V + k)

    # --- cooking sources -> per-zone generation (kg/s), by on/off state ---
    cooktop, oven = _no2_sources(model, kitchen_zone=kitchen_zone)
    hood_mult = HOOD_MULT.get(hood, 1.0)
    events = DEFAULT_COOKING if cooking is None else cooking

    def b_vector(cooktop_on, oven_on):
        g = np.zeros(nz)  # kg/s
        if cooktop_on:
            for z, r in cooktop.items():
                if z in idx:
                    g[idx[z]] += r * hood_mult * emission_scale
        if oven_on:
            for z, r in oven.items():
                if z in idx:
                    g[idx[z]] += r * hood_mult * emission_scale
        # b_i = [Q_out->i * C_out + (g_i / PPB_TO_KGM3)] / V_i   (ppb/s)
        return (Qout_in * C_out_ppb + g / PPB_TO_KGM3) / V

    # Distinct source states -> precompute steady states.
    states = {}  # (cooktop_on, oven_on) -> (b, C_ss)
    def get_state(ct, ov):
        key = (ct, ov)
        if key not in states:
            b = b_vector(ct, ov)
            try:
                Css = np.linalg.solve(A, -b)
            except np.linalg.LinAlgError:
                Css = np.linalg.lstsq(A, -b, rcond=None)[0]
            states[key] = (b, Css)
        return states[key]

    dt = dt_min * 60.0
    nsteps = int(round(hours * 3600 / dt))
    Phi = expm(A * dt)            # state-transition matrix for one step (exact)

    def active(t_h):
        ct = ov = False
        for e in events:
            if e["start"] <= t_h < e["start"] + e["min"] / 60.0:
                ct = ct or e.get("cooktop", True)
                ov = ov or e.get("oven", False)
        return ct, ov

    C = np.full(nz, C_out_ppb)    # start near outdoor
    times, series = [], []
    for step in range(nsteps + 1):
        t_h = step * dt / 3600.0
        times.append(t_h)
        series.append(C.copy())
        ct, ov = active(t_h)
        _, Css = get_state(ct, ov)
        C = Css + Phi @ (C - Css)

    series = np.array(series)     # (nsteps+1, nz) ppb
    names = {z: model.zones[z].name for z in zone_ids}
    by_zone = {names[z]: series[:, idx[z]] for z in zone_ids}
    # day-average and peak per zone (over the 24 h)
    n24 = int(round(24 * 3600 / dt))
    summary = {
        names[z]: {
            "dayavg": float(np.mean(series[:n24, idx[z]])),
            "peak": float(np.max(series[:n24, idx[z]])),
        }
        for z in zone_ids
    }
    return {
        "t": np.array(times), "series": series, "by_zone": by_zone,
        "summary": summary, "zone_ids": zone_ids, "names": names,
        "whole_home_ach": afr["whole_home_ach"],
    }
