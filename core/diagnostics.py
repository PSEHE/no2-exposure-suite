"""Physics diagnostics for the CONTAM-Lite engine.

Four diagnostic families, all pure computation (no Streamlit):

A. Sensitivity sweeps — 1-D knob sweeps (temperature, wind, window opening,
   interzone mixing, envelope leakage, indoor temp) with concentration, ACH,
   and kitchen-exchange responses; plus a tornado table of one-step responses.
B. Physics self-checks — exact mass-balance closure (uses the transport
   solver's own matrix-exponential integrals, so the residual should be at
   float precision), post-cooking decay-tail fits vs. the system's slowest
   eigenmode, the well-mixed box-model limit, and power-law scaling slopes.
C. Library ground truth — engine vs. the exact 86,400-scenario CONTAM library
   (requires the original data folders; guarded by have_library_truth()).
D. Airflow internals — kitchen boundary-flow breakdown and per-zone states
   for eyeballing the solved network.

The engine knobs `leakage_scale` and `mixing_scale` (see core.airflow) default
to 1.0 everywhere, so nothing here perturbs the calibrated engine behavior.
"""
from __future__ import annotations

import itertools
import json
import random

import numpy as np
import pandas as pd

from . import airflow as af
from . import config, persily, transform, transport

PPB_TO_KGM3 = transport.PPB_TO_KGM3

# scenario keys forwarded to the standalone airflow solve used for flow metrics
AF_KEYS = ("T_out_C", "wind_ms", "wind_dir", "window_open", "T_in_C",
           "leakage_scale", "mixing_scale")

# ---------------------------------------------------------------- homes/roles

def paper_homes():
    """The 24 paper archetypes with room->zone ids (from web_data)."""
    with open(config.WEB_DATA / "archetypes.json") as f:
        return json.load(f)


def zone_roles(model, arch=None):
    """Kitchen/bedroom zone ids + living-zone id list for metric extraction.

    Paper homes use the archetype room map; other homes fall back to the
    transform's kitchen finder and name matching for a bedroom."""
    roles = {"kitchen": None, "bedroom": None}
    if arch:
        rooms = arch.get("rooms", {})
        roles["kitchen"] = rooms.get("kitchen") or None
        roles["bedroom"] = rooms.get("bedroom1") or rooms.get("bedroom2") or None
    if not roles["kitchen"]:
        roles["kitchen"] = transform.kitchen_zone_id(model)
    if not roles["bedroom"]:
        beds = [z.id for z in model.zones.values()
                if "bed" in z.name.lower() and transform.is_living(z.name)]
        roles["bedroom"] = beds[0] if beds else None
    roles["living"] = [zid for zid, z in model.zones.items()
                       if transform.is_living(z.name)]
    return roles


# ------------------------------------------------------------ kitchen boundary

def kitchen_boundary(model, afr, kzid):
    """Break the solved flows crossing the kitchen boundary into labeled rows.

    Returns rows (counterpart, kind, q_in/q_out m³/h, interior?) plus totals:
    interzone exchange (inflow from other rooms), outdoor exchange, and the
    kitchen turnover rate (total inflow / kitchen volume, 1/h)."""
    rho = af.RHO_REF
    names = {zid: z.name for zid, z in model.zones.items()}
    names[-1] = "outdoors"
    KIND = {23: "envelope leak", 25: "orifice", 27: "window/door (power-law)"}
    rows = []
    for (a, b, w), tc in zip(afr["path_flows"], afr["path_types"]):
        if kzid not in (a, b):
            continue
        vol = w / rho * 3600.0                       # signed m³/h, a -> b
        into = vol if b == kzid else -vol            # + = into the kitchen
        other = a if b == kzid else b
        rows.append(dict(counterpart=names.get(other, str(other)),
                         kind=KIND.get(tc, f"type {tc}"),
                         q_in=max(into, 0.0), q_out=max(-into, 0.0),
                         interior=(other != -1)))
    for (a, b, Q) in afr["fans"]:
        if kzid not in (a, b):
            continue
        other = a if b == kzid else b
        interior = (a != -1 and b != -1)
        rows.append(dict(counterpart=names.get(other, str(other)),
                         kind="doorway mixing fan" if interior
                              else "two-way window exchange",
                         q_in=Q * 3600.0, q_out=Q * 3600.0, interior=interior))
    for (z, qkg) in afr.get("mech_exhaust", []):
        if z == kzid:
            rows.append(dict(counterpart="outdoors", kind="mech exhaust",
                             q_in=0.0, q_out=qkg / rho * 3600.0, interior=False))
    inter_in = sum(r["q_in"] for r in rows if r["interior"])
    out_in = sum(r["q_in"] for r in rows if not r["interior"])
    vol_k = model.zones[kzid].volume if kzid in model.zones else float("nan")
    return {"rows": rows, "interzone_m3h": inter_in, "outdoor_m3h": out_in,
            "turnover_ach": (inter_in + out_in) / vol_k if vol_k else float("nan")}


# ------------------------------------------------------------------ one case

def _rolling_max(a, w):
    if a.size < w:
        return float(np.mean(a))
    return float(np.max(np.convolve(a, np.ones(w) / w, mode="valid")))


def run_case(model, roles, scenario, kitchen_zone=None,
             internals=False, include_res=False):
    """Run one scenario; return a FLAT metrics dict (+ res if asked).

    The flow metrics (kitchen exchange, per-zone ACH) come from a fans-off
    airflow solve at the same conditions, i.e. the overnight regime."""
    res = transport.simulate(model, kitchen_zone=kitchen_zone,
                             return_internals=internals, **scenario)
    afr = af.solve_airflow(model, mech_extract={},
                           **{k: v for k, v in scenario.items() if k in AF_KEYS})
    kb = kitchen_boundary(model, afr, roles["kitchen"])

    t = res["t"]
    dt_h = float(t[1] - t[0])
    n24 = int(round(24.0 / dt_h))
    w1h = max(1, int(round(1.0 / dt_h)))
    zi = {z: i for i, z in enumerate(res["zone_ids"])}
    ser = res["series"]

    def zone_metrics(zid, prefix):
        if zid is None or zid not in zi:
            return {f"{prefix}_peak": np.nan, f"{prefix}_max1h": np.nan,
                    f"{prefix}_dayavg": np.nan}
        a = ser[:n24, zi[zid]]
        return {f"{prefix}_peak": float(np.max(a)),
                f"{prefix}_max1h": _rolling_max(a, w1h),
                f"{prefix}_dayavg": float(np.mean(a))}

    liv = [z for z in roles["living"] if z in zi] or list(zi)
    wv = np.array([model.zones[z].volume for z in liv])
    wv = wv / wv.sum()
    havg = ser[:, [zi[z] for z in liv]] @ wv       # volume-weighted living avg

    out = {
        "living_ach": res["living_ach"],
        "whole_home_ach": res["whole_home_ach"],
        "kitchen_outdoor_ach": float(afr["ach"].get(roles["kitchen"], np.nan)),
        "kitchen_exchange_m3h": kb["interzone_m3h"],
        "kitchen_outdoor_m3h": kb["outdoor_m3h"],
        "kitchen_turnover_ach": kb["turnover_ach"],
        "homeavg_dayavg": float(np.mean(havg[:n24])),
        "solver_converged": bool(afr["solver"]["converged"]),
        "solver_iterations": int(afr["solver"]["iterations"]),
        "solver_residual_rel": float(afr["solver"]["mass_residual_rel"]),
        **zone_metrics(roles["kitchen"], "kitchen"),
        **zone_metrics(roles["bedroom"], "bedroom"),
    }
    if include_res:
        out["res"] = res
        out["afr"] = afr
        out["boundary"] = kb
    return out


# -------------------------------------------------------------- A. sweeps

SWEEPS = {
    "T_out_C": ("Outdoor temperature (°C)", np.linspace(-15, 38, 15)),
    "wind_ms": ("Wind speed (m/s)", np.linspace(0, 12, 13)),
    "window_open": ("Window opening fraction", np.round(np.linspace(0, 1, 11), 2)),
    "mixing_scale": ("Interzone mixing × (doorway fans)",
                     np.round(np.linspace(0, 2, 11), 2)),
    "leakage_scale": ("Envelope leakage ×", np.round(np.geomspace(0.25, 4, 11), 3)),
    "T_in_C": ("Indoor temperature (°C)", np.linspace(15, 28, 14)),
}

SWEEP_OUTPUTS = ["living_ach", "whole_home_ach", "kitchen_outdoor_ach",
                 "kitchen_exchange_m3h", "kitchen_turnover_ach", "kitchen_peak",
                 "kitchen_max1h", "kitchen_dayavg", "bedroom_dayavg",
                 "homeavg_dayavg", "solver_converged"]


def sweep(model, roles, base_scenario, knob, values=None, kitchen_zone=None):
    """1-D sweep of one knob; every other condition held at the base scenario."""
    values = SWEEPS[knob][1] if values is None else values
    recs = []
    for v in values:
        sc = dict(base_scenario)
        sc[knob] = float(v)
        m = run_case(model, roles, sc, kitchen_zone=kitchen_zone)
        recs.append({knob: float(v), **{k: m[k] for k in SWEEP_OUTPUTS}})
    return pd.DataFrame(recs)


def _step_window(s):
    if isinstance(s, dict):     # per-room state: bump every room
        return {k: min(1.0, v + 0.1) for k, v in s.items()}
    return min(1.0, s + 0.1)


TORNADO_STEPS = {
    "T_out_C": ("Outdoor temp +5 °C", lambda s: s + 5.0),
    "wind_ms": ("Wind +1 m/s", lambda s: s + 1.0),
    "window_open": ("Window opening +0.1", _step_window),
    "mixing_scale": ("Interzone mixing ×1.5", lambda s: s * 1.5),
    "leakage_scale": ("Envelope leakage ×1.5", lambda s: s * 1.5),
    "T_in_C": ("Indoor temp +2 °C", lambda s: s + 2.0),
    "emission_scale": ("Emission ×1.5", lambda s: s * 1.5),
}


def tornado(model, roles, base_scenario, kitchen_zone=None,
            outputs=("kitchen_dayavg", "bedroom_dayavg", "kitchen_peak",
                     "living_ach")):
    """One defined step per knob from the base point; % change per output."""
    defaults = {"mixing_scale": 1.0, "leakage_scale": 1.0, "T_in_C": 23.0,
                "emission_scale": 1.0}
    sc0 = {**defaults, **base_scenario}
    m0 = run_case(model, roles, sc0, kitchen_zone=kitchen_zone)
    rows = []
    for knob, (label, step_fn) in TORNADO_STEPS.items():
        sc = dict(sc0)
        sc[knob] = step_fn(sc0[knob])
        m1 = run_case(model, roles, sc, kitchen_zone=kitchen_zone)
        row = {"perturbation": label}
        for o in outputs:
            row[o] = (100.0 * (m1[o] - m0[o]) / m0[o]
                      if m0[o] and np.isfinite(m0[o]) else np.nan)
        rows.append(row)
    return pd.DataFrame(rows), m0


# --------------------------------------------------------- B. self-checks

def mass_balance(res):
    """Exact 24-h NO₂ mass budget from the solver's own step integrals.

    emitted + outdoor-in − exfiltrated − decayed = Δstorage; the residual
    should sit at float precision if the transport integration is exact."""
    it = res["internals"]
    Cint, g = it["Cint"], it["g"]
    Qoi, Qoo = it["Qout_in"], it["Qout_out"]
    V, k, dt, Cout = it["V"], it["k"], it["dt_s"], it["C_out_ppb"]
    ser = res["series"]
    n = Cint.shape[0]
    emitted = float(g.sum() * dt)                    # ppb·m³
    outdoor_in = float((Qoi * Cout).sum() * dt)
    exfiltrated = float((Qoo * Cint).sum())
    decayed = float(k * (Cint @ V).sum())
    storage = float(((ser[n] - ser[0]) * V).sum())
    residual = emitted + outdoor_in - exfiltrated - decayed - storage
    denom = max(abs(emitted) + abs(outdoor_in), 1e-12)
    to_g = PPB_TO_KGM3 * 1000.0                      # ppb·m³ -> grams NO₂
    return {"emitted_g": emitted * to_g,
            "outdoor_in_g": outdoor_in * to_g,
            "exfiltrated_g": exfiltrated * to_g,
            "decayed_g": decayed * to_g,
            "storage_g": storage * to_g,
            "residual_g": residual * to_g,
            "residual_pct": 100.0 * residual / denom}


def decay_fit(res, roles, window_h=(20.0, 24.0)):
    """Fit the post-dinner decay tail per room; compare to the slowest mode.

    After sources stop, every zone relaxes toward the outdoor-driven floor at
    the overnight system's slowest eigenvalue. Fit ln(C − C_floor) over the
    window and compare the fitted rate to −λ_slow and to (whole-home ACH + k)."""
    it = res["internals"]
    A, V, k, Cout = it["A_overnight"], it["V"], it["k"], it["C_out_ppb"]
    lam = np.linalg.eigvals(A)
    slow_mode = -float(np.max(lam.real)) * 3600.0    # 1/h
    # overnight floor: steady state under the fans-off regime, no cooking
    b_night = (it["Qout_in"][-1] * Cout) / V         # last step is overnight
    floor = np.linalg.solve(A, -b_night)
    t = res["t"]
    ser = res["series"]
    zi = {z: i for i, z in enumerate(res["zone_ids"])}
    sel = (t >= window_h[0]) & (t <= window_h[1])
    rows = []
    for label, zid in (("Kitchen", roles["kitchen"]),
                       ("Bedroom", roles["bedroom"])):
        if zid is None or zid not in zi:
            continue
        y = ser[sel, zi[zid]] - floor[zi[zid]]
        if y.size < 4 or np.max(y) < 0.5:            # no usable tail signal
            rows.append({"room": label, "fitted_per_h": np.nan, "r2": np.nan})
            continue
        ok = y > max(1e-3, 1e-4 * np.max(y))
        x = t[sel][ok]
        ly = np.log(y[ok])
        coef = np.polyfit(x, ly, 1)
        pred = np.polyval(coef, x)
        ss_res = float(np.sum((ly - pred) ** 2))
        ss_tot = float(np.sum((ly - ly.mean()) ** 2))
        rows.append({"room": label, "fitted_per_h": -float(coef[0]),
                     "r2": 1.0 - ss_res / ss_tot if ss_tot > 0 else np.nan})
    refs = {"slowest_mode_per_h": slow_mode,
            "ach_plus_decay_per_h": res["living_ach"] + k * 3600.0}
    return pd.DataFrame(rows), refs


def _kitchen_component(model, kzid):
    """Zone ids reachable from the kitchen through interior mixing fans."""
    adj = {}
    for p in model.paths:
        el = model.elements.get(p.element)
        if (el is not None and el.type_code in af.FAN_TYPES
                and p.n_from != -1 and p.n_to != -1):
            adj.setdefault(p.n_from, set()).add(p.n_to)
            adj.setdefault(p.n_to, set()).add(p.n_from)
    comp, stack = {kzid}, [kzid]
    while stack:
        z = stack.pop()
        for nb in adj.get(z, ()):
            if nb not in comp:
                comp.add(nb)
                stack.append(nb)
    return comp


def box_model_check(model, roles, scenario, kitchen_zone=None, mixing=10.0):
    """Well-mixed limit: at high interzone mixing, the fan-connected zones
    around the kitchen behave as one box.

    Runs the engine with mixing_scale=`mixing` and integrates the equivalent
    single-zone model over the kitchen's fan-connected component, using the
    SAME per-step envelope flows and sources. Zones outside the component
    (unconditioned spaces linked only by leaks) are excluded — they never join
    the box no matter how hard the fans mix. Exchange with them through
    interior leaks is neglected, and outdoor NO₂ is forced to 0 (with C_out>0
    the engine routes outdoor NO₂ into the component indirectly via basement/
    attic makeup air, a pathway a single box cannot represent). Expect
    agreement to a few percent, not machine precision."""
    sc = dict(scenario)
    sc["mixing_scale"] = mixing
    sc["C_out_ppb"] = 0.0            # isolate stove NO₂ (see docstring)
    res = transport.simulate(model, kitchen_zone=kitchen_zone,
                             return_internals=True, **sc)
    it = res["internals"]
    V, k, dt, Cout = it["V"], it["k"], it["dt_s"], it["C_out_ppb"]
    comp = _kitchen_component(model, roles["kitchen"])
    cols = [i for i, z in enumerate(res["zone_ids"]) if z in comp]
    Vc = V[cols]
    Vtot = float(Vc.sum())
    Qin_t = it["Qout_in"][:, cols].sum(axis=1)
    Qout_t = it["Qout_out"][:, cols].sum(axis=1)
    E_t = it["g"][:, cols].sum(axis=1)
    C = float(Cout)
    box = [C]
    for i in range(len(E_t)):
        a = -(Qout_t[i] / Vtot + k)
        b = (Qin_t[i] * Cout + E_t[i]) / Vtot
        Css = -b / a
        C = Css + np.exp(a * dt) * (C - Css)
        box.append(C)
    box = np.array(box)
    eng = res["series"][:, cols] @ (Vc / Vtot)   # volume-weighted component avg
    n24 = int(round(24.0 / float(res["t"][1] - res["t"][0])))
    return {"t": res["t"], "engine": eng, "box": box, "mixing": mixing,
            "ratio_dayavg": float(np.mean(eng[:n24]) / np.mean(box[:n24])),
            "n_component": len(cols), "n_zones": len(res["zone_ids"]),
            "volume_fraction": Vtot / float(V.sum())}


CONVERGED = dict(max_iter=4000, tol=1e-10)   # settings for fully-converged solves


def scaling_laws(model, T_in_C=23.0):
    """Fitted log-log slopes of ACH vs ΔT (stack) and vs wind speed.

    Envelope flow follows Q ∝ ΔP^n: stack gives ΔP ∝ Δρ ∝ ΔT (slope ≈ n̄),
    wind gives ΔP ∝ v² (slope ≈ 2n̄), with n̄ in the range spanned by the
    ambient-connected leak exponents. Windows closed, mech fans off. Uses
    FULLY-CONVERGED solves (max_iter=4000) so this checks the physics, not the
    production iteration cap — see convergence_scan() for the latter."""
    ns, ws = [], []
    for p in model.paths:
        el = model.elements.get(p.element)
        if el is None or el.type_code not in (23, 25):
            continue
        if p.n_from != -1 and p.n_to != -1:
            continue
        ns.append(float(el.params[2]))
        ws.append(float(el.params[1]) * p.mult)
    n_mean = float(np.average(ns, weights=ws)) if ns else float("nan")
    n_min = float(np.min(ns)) if ns else float("nan")
    n_max = float(np.max(ns)) if ns else float("nan")

    dTs = np.array([1.0, 2.0, 4.0, 8.0, 16.0, 30.0])
    ach_T = np.array([max(1e-9, af.solve_airflow(
        model, T_out_C=T_in_C - dT, T_in_C=T_in_C, wind_ms=0.0,
        window_open=0.0, mech_extract={}, **CONVERGED)["living_ach"])
        for dT in dTs])
    slope_T = float(np.polyfit(np.log(dTs), np.log(ach_T), 1)[0])

    vs = np.array([0.5, 1.0, 2.0, 4.0, 8.0, 12.0])
    ach_v = np.array([max(1e-9, af.solve_airflow(
        model, T_out_C=T_in_C, T_in_C=T_in_C, wind_ms=v,
        window_open=0.0, mech_extract={}, **CONVERGED)["living_ach"])
        for v in vs])
    slope_v = float(np.polyfit(np.log(vs), np.log(ach_v), 1)[0])

    return {"n_mean": n_mean, "n_min": n_min, "n_max": n_max,
            "dT": dTs, "ach_dT": ach_T, "slope_dT": slope_T,
            "v": vs, "ach_v": ach_v, "slope_v": slope_v}


def convergence_scan(model):
    """Compare production-config ACH vs fully-converged ACH over the library's
    temp × wind × window grid. Rows with ratio far from 1 are scenarios where
    the production Newton iteration cap (max_iter=100, no damping) returns a
    non-converged airflow solution."""
    from .validate import TEMP_C, WIND_MS, WINDOW_OPEN
    rows = []
    for temp, T in TEMP_C.items():
        for wind, v in WIND_MS.items():
            for window, wo in WINDOW_OPEN.items():
                a1 = af.solve_airflow(model, T_out_C=T, wind_ms=v,
                                      window_open=wo, mech_extract={})
                a2 = af.solve_airflow(model, T_out_C=T, wind_ms=v,
                                      window_open=wo, mech_extract={},
                                      **CONVERGED)
                r1, r2 = a1["living_ach"], a2["living_ach"]
                rows.append({
                    "temp": temp, "wind": wind, "window": window,
                    "ach_production": r1, "ach_converged": r2,
                    "ratio": r1 / r2 if r2 > 1e-12 else float("nan"),
                    "iterations": a1["solver"]["iterations"],
                    "converged_flag": bool(a1["solver"]["converged"]),
                })
    return pd.DataFrame(rows)


# ------------------------------------------------- C. library ground truth

AXES = {
    "temp": ["COLD", "COOL", "RT", "WARM"],
    "wind": ["STILL", "BREEZE", "WINDY"],
    "window": ["closed", "moderate", "open"],
    "hood": ["NoHood", "25CE", "50CE", "75CE"],
    "use": ["low", "med", "medNoBk", "high"],
}
OCS = ["fifth_kitchen", "median", "ninetyfifth_kitchen",
       "ninetyfifth_outside", "fifth_outside"]
REF = dict(hood="NoHood", use="med", window="closed", temp="COOL",
           wind="BREEZE", oc="median")


def have_library_truth():
    """Occupancy-weighted library comparison needs the original data folders."""
    try:
        return (config.HOUSE_INPUTS.exists()
                and (config.SCALEUP / "Occupancy" / "median.csv").exists())
    except OSError:
        return False


def axis_response(house, axis, metric="dayavg", overrides=None, progress=None):
    """Engine vs library along ONE category axis, others at the reference."""
    from . import library as lib
    from . import validate as V
    model = persily.load_paper_home(house)
    no2, _ = lib.load_library()
    ref = {**REF, **(overrides or {})}
    rows = []
    levels = AXES[axis]
    for i, level in enumerate(levels):
        sc = dict(ref)
        sc[axis] = level
        p = V.predict(model, house, sc["hood"], sc["use"], sc["window"],
                      sc["temp"], sc["wind"], sc["oc"])
        r = no2[lib.scenario_key(house, sc["hood"], sc["use"], sc["window"],
                                 sc["temp"], sc["wind"], sc["oc"])]
        rows.append({"level": level, "engine": p[metric], "library": r[metric]})
        if progress:
            progress(i + 1, len(levels))
    return pd.DataFrame(rows)


def scatter_sample(house, n=100, seed=0, progress=None):
    """Engine vs library over a random scenario sample (all four metrics).

    One physical simulation per unique (hood,use,window,temp,wind); the five
    occupancy weightings are post-processing on the same run."""
    from . import library as lib
    from . import validate as V
    model = persily.load_paper_home(house)
    no2, _ = lib.load_library()
    allc = list(itertools.product(AXES["hood"], AXES["use"], AXES["window"],
                                  AXES["temp"], AXES["wind"], OCS))
    rng = random.Random(seed)
    sample = rng.sample(allc, min(n, len(allc)))
    phys_cache = {}

    def phys(hood, use, window, temp, wind):
        key = (hood, use, window, temp, wind)
        if key not in phys_cache:
            phys_cache[key] = transport.simulate(
                model, T_out_C=V.TEMP_C[temp], wind_ms=V.WIND_MS[wind],
                window_open=V.WINDOW_OPEN[window], hood=hood,
                cooking=V.USE_COOKING[use], emission_scale=V.USE_SCALE[use],
                C_out_ppb=0.0)
        return phys_cache[key]

    metrics = ("peak", "hravg", "eighthravg", "dayavg")
    recs = []
    for i, (hood, use, window, temp, wind, oc) in enumerate(sample):
        res = phys(hood, use, window, temp, wind)
        p = V.occupancy_metrics(res, house, oc)
        r = no2[lib.scenario_key(house, hood, use, window, temp, wind, oc)]
        recs.append({"hood": hood, "use": use, "window": window, "temp": temp,
                     "wind": wind, "oc": oc,
                     **{f"engine_{m}": p[m] for m in metrics},
                     **{f"library_{m}": r[m] for m in metrics}})
        if progress:
            progress(i + 1, len(sample))
    return pd.DataFrame(recs)


def gm_ratio(pred, ref, floor=0.05):
    """Geometric-mean engine/library ratio over pairs above a floor (ppb)."""
    pred = np.asarray(pred, float)
    ref = np.asarray(ref, float)
    ok = (pred > floor) & (ref > floor)
    if not ok.any():
        return float("nan")
    return float(np.exp(np.mean(np.log(pred[ok] / ref[ok]))))
