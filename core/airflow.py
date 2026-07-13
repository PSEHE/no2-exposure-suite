"""Multizone airflow network solver (CONTAM-style).

Solves for steady zone pressures such that mass is conserved at every zone,
given stack effect (indoor/outdoor density differences) and wind pressure.
Flow elements use CONTAM's power-law form  w = C * sign(dP) * |dP|^n  (kg/s),
with C and n read from the parsed .prj element. Constant-volume-flow elements
(interzone mixing fans / AHS) are returned separately for the transport solver;
they carry little NET mass but mix contaminants.

Reference: NIST CONTAM theory (mass-balance airflow network, Newton-Raphson).
"""
from __future__ import annotations

import numpy as np

from . import transform as _tf     # is_living() for the living-space ACH metric

G = 9.8055
RHO_REF = 1.2041      # reference air density used by the PRJ flow coefficients
P_ATM = 101325.0
M_AIR = 0.0289647     # kg/mol
R_GAS = 8.31446

POWERLAW_TYPES = {23, 25, 27}   # plr_leak, plr_orfc, dor_door (net flow)
FAN_TYPES = {31, 29}            # fan_cvf / constant volume flow
DOOR_TYPES = {27}               # dor_door — also gets two-way density exchange
DOOR_CD = 0.35                  # discharge coef for two-way opening flow (calibrated)
WIND_MOD = 0.3                  # global wind-pressure scale (calibrated)
# Standard interzone doorway mixing (m³/s each way per doorway = 2000 m³/h).
# Every home gets THIS value on every doorway pair regardless of what its .prj
# encodes (per-home Sci-Adv variations are normalized away at solve time).
STD_DOORWAY_M3S = 2 * 0.277778


def air_density(T_kelvin, P=P_ATM):
    return P * M_AIR / (R_GAS * T_kelvin)


def _powerlaw(C, n, dP, dP_lam=1e-3):
    """Power-law mass flow and its derivative, linearized near dP=0."""
    a = abs(dP)
    if a < dP_lam:
        slope = C * dP_lam ** n / dP_lam      # match value/slope at dP_lam
        return slope * dP, slope
    w = C * (1.0 if dP > 0 else -1.0) * a ** n
    dwdP = C * n * a ** (n - 1)
    return w, dwdP


def _cp(profile, angle_deg):
    """Interpolate a wind-pressure coefficient from a WindProfile at an angle."""
    a = angle_deg % 360
    pts = profile.points
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        if x0 <= a <= x1:
            return y0 + (y1 - y0) * (a - x0) / (x1 - x0) if x1 != x0 else y0
    return pts[-1][1]


def solve_airflow(model, T_out_C, wind_ms=0.0, wind_dir=0.0,
                  window_open=0.0, T_in_C=23.0, max_iter=100, tol=1e-7,
                  mech_extract=None, leakage_scale=1.0, mixing_scale=1.0):
    """Solve the airflow network for one set of ambient conditions.

    model:        parsed PrjModel
    T_out_C:      outdoor temperature (C)
    wind_ms:      wind speed (m/s)
    wind_dir:     wind direction (deg)
    window_open:  window opening fraction 0..1 (scales the window/door element
                  mult), applied to every window; OR a {zone_id: fraction} dict
                  for per-room control (zones not listed are closed; interior
                  doors take the more-open side)
    leakage_scale: diagnostics knob — multiplies the flow coefficient C of
                  envelope leaks/orifices (ambient-connected, types 23/25 only;
                  windows/doors excluded). 1.0 = as-parsed (no-op).
    mixing_scale: diagnostics knob — multiplies the volumetric flow of interior
                  constant-flow mixing fans (the 1000 m³/h doorway/stair fans).
                  1.0 = as-parsed (no-op).

    Returns dict with zone pressures (Pa), the zone->zone+ambient mass-flow
    matrix (kg/s), and the air-exchange rate per zone (1/h).
    """
    zone_ids = sorted(model.zones)
    idx = {z: i for i, z in enumerate(zone_ids)}
    nz = len(zone_ids)
    # Mechanical exhaust (zone -> kg/s). Callers can override (e.g. to toggle a
    # scheduled fan on/off); otherwise use the model's full extract.
    mech = mech_extract if mech_extract is not None else (getattr(model, "mech_extract", {}) or {})
    T_in = T_in_C + 273.15
    T_out = T_out_C + 273.15
    rho_in = air_density(T_in)
    rho_out = air_density(T_out)
    rho_zone = {z: rho_in for z in zone_ids}          # all zones at indoor temp
    Pdyn = 0.5 * rho_out * wind_ms ** 2

    def _wo(path):
        """Opening fraction for a window/door path (scalar or per-room dict)."""
        if not isinstance(window_open, dict):
            return float(window_open)
        if path.n_from == -1:
            return float(window_open.get(path.n_to, 0.0))
        if path.n_to == -1:
            return float(window_open.get(path.n_from, 0.0))
        return max(float(window_open.get(path.n_from, 0.0)),
                   float(window_open.get(path.n_to, 0.0)))

    # Pre-build the list of power-law paths we actually solve.
    paths = []
    for p in model.paths:
        el = model.elements.get(p.element)
        if el is None or el.type_code not in POWERLAW_TYPES:
            continue
        C = float(el.params[1]); n = float(el.params[2])
        mult = p.mult
        # diagnostics: scale envelope leakage (ambient-connected leaks/orifices only)
        if el.type_code in (23, 25) and (p.n_from == -1 or p.n_to == -1):
            C = C * leakage_scale
        # window/door opening scales with the window_open fraction
        if el.type_code == 27:
            mult = mult * _wo(p)
            if mult <= 0:
                continue
        # wind pressure on ambient-connected paths
        wind_P = 0.0
        if (p.n_from == -1 or p.n_to == -1) and p.wind_profile in model.wind_profiles and wind_ms > 0:
            cp = _cp(model.wind_profiles[p.wind_profile], wind_dir - p.wazm)
            wind_P = WIND_MOD * (p.wPmod if p.wPmod else 1.0) * cp * Pdyn
        paths.append((p.n_from, p.n_to, C * mult, n, p.relHt, wind_P, el.type_code))

    P = np.zeros(nz)   # zone gauge pressures (Pa), ambient = 0

    def node_P_at(node, z):
        """Pressure of a node at height z (hydrostatic), ambient incl. wind sep."""
        if node == -1:
            return -rho_out * G * z          # ambient hydrostatic (wind added per-path)
        return P[idx[node]] - rho_zone[node] * G * z

    n_iter = 0
    last_step = float("inf")
    for _ in range(max_iter):
        F = np.zeros(nz)
        J = np.zeros((nz, nz))
        for (a, b, C, n, z, wind_P, _tc) in paths:
            Pa = node_P_at(a, z) + (wind_P if a == -1 else 0.0)
            Pb = node_P_at(b, z) + (wind_P if b == -1 else 0.0)
            dP = Pa - Pb
            w, dwdP = _powerlaw(C, n, dP)      # mass flow a -> b
            if a != -1:
                ia = idx[a]; F[ia] -= w; J[ia, ia] -= dwdP
                if b != -1:
                    J[ia, idx[b]] += dwdP
            if b != -1:
                ib = idx[b]; F[ib] += w; J[ib, ib] -= dwdP
                if a != -1:
                    J[ib, idx[a]] += dwdP
        # Mechanical exhaust (kg/s) to outdoor: a constant sink that depressurizes
        # the zone, so the solver draws makeup air in through the envelope.
        for z_id, q in mech.items():
            if z_id in idx:
                F[idx[z_id]] -= q
        # Newton step: J dP = -F
        try:
            dPv = np.linalg.solve(J, -F)
        except np.linalg.LinAlgError:
            dPv = np.linalg.lstsq(J, -F, rcond=None)[0]
        P += dPv
        n_iter += 1
        last_step = float(np.max(np.abs(dPv)))
        if last_step < tol:
            break

    # Recover flows and per-zone outdoor air exchange.
    inflow_from_out = {z: 0.0 for z in zone_ids}     # kg/s of outdoor air into each zone
    path_flows = []   # (a, b, w_mass) directed mass flow a->b (kg/s) for each power-law path
    path_types = []   # element type_code per path_flows entry (23 leak / 25 orfc / 27 door)
    for (a, b, C, n, z, wind_P, tc) in paths:
        Pa = node_P_at(a, z) + (wind_P if a == -1 else 0.0)
        Pb = node_P_at(b, z) + (wind_P if b == -1 else 0.0)
        w, _ = _powerlaw(C, n, Pa - Pb)
        path_flows.append((a, b, w))
        path_types.append(tc)
        if a == -1 and b != -1 and w > 0:
            inflow_from_out[b] += w
        elif b == -1 and a != -1 and w < 0:
            inflow_from_out[a] += -w

    # Constant-volume-flow elements (interzone mixing fans / AHS) — excluded from
    # the pressure solve (≈zero net mass) but needed for contaminant transport.
    # Interior fan paths are NORMALIZED: one entry per zone pair at the standard
    # doorway exchange, however the .prj encodes it (pairs of 1000s, single
    # 2000s, or per-home oddities all become STD_DOORWAY_M3S each way).
    fans = []  # (a, b, Q_vol m3/s) bidirectional volumetric mixing
    seen_pairs = set()
    for p in model.paths:
        el = model.elements.get(p.element)
        if el is None or el.type_code not in FAN_TYPES:
            continue
        if p.n_from != -1 and p.n_to != -1:
            pair = tuple(sorted((p.n_from, p.n_to)))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            fans.append((pair[0], pair[1], STD_DOORWAY_M3S * mixing_scale))
        else:
            fans.append((p.n_from, p.n_to, float(el.params[0]) * p.mult))

    # Two-way density-driven exchange through OPEN large openings (windows/doors).
    # A one-way power law gives ~zero flow at small net dP, but a real open
    # window exchanges air bidirectionally driven by the in/out density gradient
    # over its height:  Q ≈ (1/3) Cd W H sqrt(g H |dρ| / ρ).  This is the main
    # ventilation when windows are open. Scaled by the opening fraction.
    for p in model.paths:
        el = model.elements.get(p.element)
        if el is None or el.type_code not in DOOR_TYPES:
            continue
        wo = _wo(p)
        if wo <= 0:
            continue
        try:
            H = float(el.params[4]); W = float(el.params[5])
        except (IndexError, ValueError):
            continue
        rho_a = rho_out if p.n_from == -1 else rho_in
        rho_b = rho_out if p.n_to == -1 else rho_in
        drho = abs(rho_a - rho_b)
        if drho < 1e-4 or H <= 0 or W <= 0:
            continue
        Qexch = (1.0 / 3.0) * DOOR_CD * W * H * np.sqrt(G * H * drho / (0.5 * (rho_a + rho_b)))
        fans.append((p.n_from, p.n_to, Qexch * wo * p.mult))

    ach = {}
    for z in zone_ids:
        vol = model.zones[z].volume
        # ACH = (volumetric outdoor inflow) / volume, per hour
        ach[z] = (inflow_from_out[z] / rho_out) / vol * 3600 if vol > 0 else 0.0

    total_out_inflow = sum(inflow_from_out.values())  # kg/s
    total_vol = sum(model.zones[z].volume for z in zone_ids)
    whole_home_ach = (total_out_inflow / rho_out) / total_vol * 3600 if total_vol else 0.0

    # Living-space ACH: direct outdoor air into conditioned/occupiable zones
    # only. Whole-home ACH is dominated by attic/crawlspace cross-flow (wind
    # blows in one vent and out the other without touching the living space),
    # so it badly overstates the ventilation people actually experience.
    liv = [z for z in zone_ids if _tf.is_living(model.zones[z].name)]
    liv_vol = sum(model.zones[z].volume for z in liv)
    liv_inflow = sum(inflow_from_out[z] for z in liv)
    living_ach = (liv_inflow / rho_out) / liv_vol * 3600 if liv_vol else whole_home_ach

    # Solver health (instrumentation only — does not alter the solution):
    # per-zone net mass imbalance of the recovered power-law flows + mech sinks.
    net = {z: 0.0 for z in zone_ids}
    for (a, b, w) in path_flows:
        if b != -1:
            net[b] += w
        if a != -1:
            net[a] -= w
    for z_id, q in mech.items():
        if z_id in idx:
            net[z_id] -= q
    mass_residual = max((abs(v) for v in net.values()), default=0.0)

    return {
        "P": {z: float(P[idx[z]]) for z in zone_ids},
        "ach": ach,
        "whole_home_ach": whole_home_ach,
        "living_ach": living_ach,
        "rho_in": rho_in, "rho_out": rho_out,
        "path_flows": path_flows,   # [(a, b, w_mass kg/s)] power-law paths
        "path_types": path_types,   # element type_code aligned with path_flows
        "fans": fans,               # [(a, b, Q_vol m3/s)] constant-flow mixing
        "mech_exhaust": [(z, mech[z]) for z in mech if z in idx],  # [(zone, kg/s)]
        "zone_ids": zone_ids,
        "solver": {
            "iterations": n_iter, "max_iter": max_iter,
            "converged": last_step < tol, "last_step_Pa": last_step,
            "mass_residual_kgps": mass_residual,
            "mass_residual_rel": mass_residual / max(total_out_inflow, 1e-12),
        },
    }
