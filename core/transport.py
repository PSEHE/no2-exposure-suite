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

# Default shower schedule — bath exhaust fans run during these windows.
DEFAULT_SHOWER = [(7.0, 20.0), (20.0, 20.0)]   # (start hour, duration minutes)


# Standard single-burner / oven NO2 rates (kg/s), from the .prj source elements —
# used to inject a source into custom floorplans that lack NO2 sources.
STD_COOKTOP_KG_S = 3.1878e-8
STD_OVEN_KG_S = 3.66597e-8

# Opening fraction of an "open" window — the library's calibrated open state.
OPEN_FRACTION = 0.7


def window_schedule_from_hours(hours_per_day, *, cooking=None, during_cooking=True,
                               start_hour=12.0, open_value=OPEN_FRACTION):
    """Build a window_schedule from a daily open-time budget (hours).

    during_cooking=True (and a cooking pattern given): a window opens when each
    cooking event STARTS and lingers after the burners turn off — the budget is
    split across events in proportion to each event's duration. Blocks cascade:
    if the previous opening is still running when the next meal starts, the next
    block begins where it left off (total open time is preserved, not
    double-counted). Time past midnight wraps to the morning.

    during_cooking=False (or no cooking events): one contiguous block starting
    at `start_hour`, wrapping past midnight if needed.

    hours<=0 -> [] (closed all day); hours>=24 -> open all day.
    `open_value` may be a scalar fraction or a {zone_id: fraction} dict
    (e.g. kitchen window only)."""
    h = float(hours_per_day)
    if h <= 0:
        return []
    if h >= 24:
        return [{"start": 0.0, "hours": 24.0, "open": open_value}]

    events = sorted(cooking or [], key=lambda e: e["start"]) if during_cooking else []
    blocks = []
    if events:
        total_min = sum(e["min"] for e in events)
        if total_min > 0:
            cursor = 0.0
            for e in events:
                share = h * (e["min"] / total_min)
                s = max(float(e["start"]), cursor)
                blocks.append((s, share))
                cursor = s + share
    if not blocks:
        blocks = [(float(start_hour), h)]

    # wrap anything past midnight to the morning (clamped before the first block)
    sched = []
    overflow = 0.0
    for s, d in blocks:
        if s >= 24.0:
            overflow += d
            continue
        if s + d > 24.0:
            overflow += (s + d) - 24.0
            d = 24.0 - s
        sched.append({"start": s, "hours": d, "open": open_value})
    if overflow > 0:
        first = min(b["start"] for b in sched) if sched else 24.0
        sched.insert(0, {"start": 0.0, "hours": min(overflow, first),
                         "open": open_value})
    return sched


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
             emission_scale=1.0, kitchen_zone=None, hours=24, dt_min=10,
             shower_schedule=None, window_schedule=None,
             leakage_scale=1.0, mixing_scale=1.0, return_internals=False):
    """Run a day of NO2 transport. Returns time series + per-zone summary.

    Mechanical exhaust fans and window openings run on SCHEDULES, so the
    airflow network changes over the day: the kitchen fan runs while cooking,
    bath fans during showers, windows follow window_schedule. We solve the
    airflow once per distinct (fan state, window state) regime and integrate
    transport piecewise (an exact matrix-exponential step per regime). Homes
    with no mechanical exhaust and no window schedule collapse to a single
    regime — identical to the steady-airflow behavior.

    window_open: fraction 0..1 for all windows, or {zone_id: fraction}.
    window_schedule: list of {"start": hour, "hours": duration, "open": value}
    intervals; `open` (scalar or dict) REPLACES window_open while active
    (first matching interval wins), reverting to window_open otherwise.

    Diagnostics knobs (all no-op at defaults): leakage_scale / mixing_scale pass
    through to the airflow solver; return_internals attaches exact per-step
    integrals (∫C dt via the same matrix-exponential machinery), per-step
    outdoor-exchange and emission vectors, and the overnight system matrix —
    enough to audit mass balance and modal time constants externally."""
    rho_ref = af.RHO_REF
    base_mech = getattr(model, "mech_extract", {}) or {}
    # Split exhaust into the kitchen fan (runs while cooking) and bath/other fans
    # (run during showers), by zone name.
    kit_mech, bath_mech = {}, {}
    for z, q in base_mech.items():
        nm = model.zones[z].name.lower() if z in model.zones else ""
        (kit_mech if "kit" in nm else bath_mech)[z] = q

    events = DEFAULT_COOKING if cooking is None else cooking
    showers = DEFAULT_SHOWER if shower_schedule is None else shower_schedule

    def cooking_active(t_h):
        ct = ov = False
        for e in events:
            if e["start"] <= t_h < e["start"] + e["min"] / 60.0:
                ct = ct or e.get("cooktop", True)
                ov = ov or e.get("oven", False)
        return ct, ov

    def shower_active(t_h):
        return any(s <= t_h < s + d / 60.0 for s, d in showers)

    def _wkey(w):
        """Hashable regime key for a window state (scalar or per-room dict)."""
        return tuple(sorted(w.items())) if isinstance(w, dict) else float(w)

    def win_state(t_h):
        if window_schedule:
            for e in window_schedule:
                if e["start"] <= t_h < e["start"] + e.get("hours", 0.0):
                    return e["open"]
        return window_open

    # --- airflow per regime (kitchen fan, bath fan, window state) ---
    afr_cache = {}
    def airflow_for(kf, bf, ws):
        key = (kf, bf, _wkey(ws))
        if key not in afr_cache:
            me = {}
            if kf:
                me.update(kit_mech)
            if bf:
                me.update(bath_mech)
            afr_cache[key] = af.solve_airflow(
                model, T_out_C=T_out_C, wind_ms=wind_ms, wind_dir=wind_dir,
                window_open=ws, T_in_C=T_in_C, mech_extract=me,
                leakage_scale=leakage_scale, mixing_scale=mixing_scale)
        return afr_cache[key]

    zone_ids = airflow_for(False, False, win_state(0.0))["zone_ids"]
    idx = {z: i for i, z in enumerate(zone_ids)}
    nz = len(zone_ids)
    V = np.array([model.zones[z].volume for z in zone_ids])
    k = -model.decay_of("NO2")    # decay_of returns a negative rate; k > 0

    def build(afr):
        """Return (system matrix A, outdoor-inflow vector) for one airflow state."""
        Qin = np.zeros((nz, nz)); Qout_in = np.zeros(nz); Qout_out = np.zeros(nz)
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
                Qin[idx[a]][idx[b]] += Q; Qin[idx[b]][idx[a]] += Q
            elif a != -1:
                Qout_in[idx[a]] += Q; Qout_out[idx[a]] += Q
            elif b != -1:
                Qout_in[idx[b]] += Q; Qout_out[idx[b]] += Q
        for (z, q_kgs) in afr.get("mech_exhaust", []):     # exhaust to outdoor
            if z in idx:
                Qout_out[idx[z]] += q_kgs / rho_ref
        A = Qin / V[:, None]
        outflow = Qin.sum(axis=0) + Qout_out
        np.fill_diagonal(A, 0.0)
        A[np.diag_indices(nz)] = -(outflow / V + k)
        return A, Qout_in, Qout_out

    cooktop, oven = _no2_sources(model, kitchen_zone=kitchen_zone)
    hood_mult = HOOD_MULT.get(hood, 1.0)

    def g_vec(ct, ov):
        g = np.zeros(nz)  # kg/s per zone
        if ct:
            for z, r in cooktop.items():
                if z in idx:
                    g[idx[z]] += r * hood_mult * emission_scale
        if ov:
            for z, r in oven.items():
                if z in idx:
                    g[idx[z]] += r * hood_mult * emission_scale
        return g

    def b_vector(Qout_in, ct, ov):
        return (Qout_in * C_out_ppb + g_vec(ct, ov) / PPB_TO_KGM3) / V

    dt = dt_min * 60.0
    nsteps = int(round(hours * 3600 / dt))

    reg_cache = {}     # regime key -> (A, Phi, Qout_in, Qout_out, ach, living_ach)
    def regime(kf, bf, ws):
        key = (kf, bf, _wkey(ws))
        if key not in reg_cache:
            afr = airflow_for(kf, bf, ws)
            A, Qoi, Qoo = build(afr)
            reg_cache[key] = (A, expm(A * dt), Qoi, Qoo,
                              afr["whole_home_ach"], afr["living_ach"])
        return reg_cache[key]

    mint_cache = {}    # regime key -> A^-1 (Phi - I), for exact per-step ∫C dt
    def step_integral_op(kf, bf, ws):
        key = (kf, bf, _wkey(ws))
        if key not in mint_cache:
            A, Phi, _, _, _, _ = regime(kf, bf, ws)
            mint_cache[key] = np.linalg.solve(A, Phi - np.eye(nz))
        return mint_cache[key]

    state_cache = {}   # (regime, ct, ov) -> steady-state C
    def steady(kf, bf, ws, ct, ov):
        key = (kf, bf, _wkey(ws), ct, ov)
        if key not in state_cache:
            A, _, Qoi, _, _, _ = regime(kf, bf, ws)
            b = b_vector(Qoi, ct, ov)
            try:
                Css = np.linalg.solve(A, -b)
            except np.linalg.LinAlgError:
                Css = np.linalg.lstsq(A, -b, rcond=None)[0]
            state_cache[key] = Css
        return state_cache[key]

    C = np.full(nz, C_out_ppb)    # start near outdoor
    times, series, ach_t, liv_t = [], [], [], []
    cint_t, g_t, qoi_t, qoo_t = [], [], [], []       # internals (diagnostics)
    ws = win_state(0.0)
    for step in range(nsteps + 1):
        t_h = step * dt / 3600.0
        ct, ov = cooking_active(t_h)
        kf = bool(kit_mech) and (ct or ov)            # kitchen fan runs while cooking
        bf = bool(bath_mech) and shower_active(t_h)   # bath fans run during showers
        ws = win_state(t_h)                           # window state (scheduled)
        _, Phi, Qoi, Qoo, ach, liv = regime(kf, bf, ws)
        times.append(t_h); series.append(C.copy()); ach_t.append(ach); liv_t.append(liv)
        Css = steady(kf, bf, ws, ct, ov)
        if return_internals and step < nsteps:
            # exact ∫C dt over the step: Css·dt + A⁻¹(Φ−I)(C0 − Css)
            cint_t.append(Css * dt + step_integral_op(kf, bf, ws) @ (series[-1] - Css))
            g_t.append(g_vec(ct, ov) / PPB_TO_KGM3)   # emission, ppb·m³/s per zone
            qoi_t.append(Qoi); qoo_t.append(Qoo)      # outdoor exchange, m³/s per zone
        C = Css + Phi @ (series[-1] - Css)

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
    out = {
        "t": np.array(times), "series": series, "by_zone": by_zone,
        "summary": summary, "zone_ids": zone_ids, "names": names,
        "whole_home_ach": float(np.mean(ach_t[:n24])),   # day-average air exchange
        "living_ach": float(np.mean(liv_t[:n24])),       # day-average, living zones
    }
    if return_internals:
        A_night, _, _, _, _, _ = regime(False, False, ws)  # fans-off, overnight windows
        out["internals"] = {
            "Cint": np.array(cint_t),      # (nsteps, nz) ppb·s — exact ∫C dt per step
            "g": np.array(g_t),            # (nsteps, nz) ppb·m³/s — emission rate
            "Qout_in": np.array(qoi_t),    # (nsteps, nz) m³/s — outdoor → zone
            "Qout_out": np.array(qoo_t),   # (nsteps, nz) m³/s — zone → outdoor
            "A_overnight": A_night,        # (nz, nz) 1/s — fans-off transport matrix
            "V": V, "k": k, "dt_s": dt, "C_out_ppb": C_out_ppb,
        }
    return out
