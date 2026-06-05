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

G = 9.8055
RHO_REF = 1.2041      # reference air density used by the PRJ flow coefficients
P_ATM = 101325.0
M_AIR = 0.0289647     # kg/mol
R_GAS = 8.31446

POWERLAW_TYPES = {23, 25, 27}   # plr_leak, plr_orfc, dor_door (net flow)
FAN_TYPES = {31, 29}            # fan_cvf / constant volume flow
DOOR_TYPES = {27}               # dor_door — also gets two-way density exchange


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
                  window_open=0.0, T_in_C=23.0, max_iter=100, tol=1e-7):
    """Solve the airflow network for one set of ambient conditions.

    model:        parsed PrjModel
    T_out_C:      outdoor temperature (C)
    wind_ms:      wind speed (m/s)
    wind_dir:     wind direction (deg)
    window_open:  window opening fraction 0..1 (scales the window/door element mult)

    Returns dict with zone pressures (Pa), the zone->zone+ambient mass-flow
    matrix (kg/s), and the air-exchange rate per zone (1/h).
    """
    zone_ids = sorted(model.zones)
    idx = {z: i for i, z in enumerate(zone_ids)}
    nz = len(zone_ids)
    T_in = T_in_C + 273.15
    T_out = T_out_C + 273.15
    rho_in = air_density(T_in)
    rho_out = air_density(T_out)
    rho_zone = {z: rho_in for z in zone_ids}          # all zones at indoor temp
    Pdyn = 0.5 * rho_out * wind_ms ** 2

    # Pre-build the list of power-law paths we actually solve.
    paths = []
    for p in model.paths:
        el = model.elements.get(p.element)
        if el is None or el.type_code not in POWERLAW_TYPES:
            continue
        C = float(el.params[1]); n = float(el.params[2])
        mult = p.mult
        # window/door opening scales with the window_open fraction
        if el.type_code == 27:
            mult = mult * window_open
            if mult <= 0:
                continue
        # wind pressure on ambient-connected paths
        wind_P = 0.0
        if (p.n_from == -1 or p.n_to == -1) and p.wind_profile in model.wind_profiles and wind_ms > 0:
            cp = _cp(model.wind_profiles[p.wind_profile], wind_dir - p.wazm)
            wind_P = (p.wPmod if p.wPmod else 1.0) * cp * Pdyn
        paths.append((p.n_from, p.n_to, C * mult, n, p.relHt, wind_P))

    P = np.zeros(nz)   # zone gauge pressures (Pa), ambient = 0

    def node_P_at(node, z):
        """Pressure of a node at height z (hydrostatic), ambient incl. wind sep."""
        if node == -1:
            return -rho_out * G * z          # ambient hydrostatic (wind added per-path)
        return P[idx[node]] - rho_zone[node] * G * z

    for _ in range(max_iter):
        F = np.zeros(nz)
        J = np.zeros((nz, nz))
        for (a, b, C, n, z, wind_P) in paths:
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
        # Newton step: J dP = -F
        try:
            dPv = np.linalg.solve(J, -F)
        except np.linalg.LinAlgError:
            dPv = np.linalg.lstsq(J, -F, rcond=None)[0]
        P += dPv
        if np.max(np.abs(dPv)) < tol:
            break

    # Recover flows and per-zone outdoor air exchange.
    inflow_from_out = {z: 0.0 for z in zone_ids}     # kg/s of outdoor air into each zone
    path_flows = []   # (a, b, w_mass) directed mass flow a->b (kg/s) for each power-law path
    for (a, b, C, n, z, wind_P) in paths:
        Pa = node_P_at(a, z) + (wind_P if a == -1 else 0.0)
        Pb = node_P_at(b, z) + (wind_P if b == -1 else 0.0)
        w, _ = _powerlaw(C, n, Pa - Pb)
        path_flows.append((a, b, w))
        if a == -1 and b != -1 and w > 0:
            inflow_from_out[b] += w
        elif b == -1 and a != -1 and w < 0:
            inflow_from_out[a] += -w

    # Constant-volume-flow elements (interzone mixing fans / AHS) — excluded from
    # the pressure solve (≈zero net mass) but needed for contaminant transport.
    fans = []  # (a, b, Q_vol m3/s) bidirectional volumetric mixing
    for p in model.paths:
        el = model.elements.get(p.element)
        if el is not None and el.type_code in FAN_TYPES:
            Q = float(el.params[0]) * p.mult
            fans.append((p.n_from, p.n_to, Q))

    # Two-way density-driven exchange through OPEN large openings (windows/doors).
    # A one-way power law gives ~zero flow at small net dP, but a real open
    # window exchanges air bidirectionally driven by the in/out density gradient
    # over its height:  Q ≈ (1/3) Cd W H sqrt(g H |dρ| / ρ).  This is the main
    # ventilation when windows are open. Scaled by the opening fraction.
    Cd_door = 0.6
    for p in model.paths:
        el = model.elements.get(p.element)
        if el is None or el.type_code not in DOOR_TYPES or window_open <= 0:
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
        Qexch = (1.0 / 3.0) * Cd_door * W * H * np.sqrt(G * H * drho / (0.5 * (rho_a + rho_b)))
        fans.append((p.n_from, p.n_to, Qexch * window_open * p.mult))

    ach = {}
    for z in zone_ids:
        vol = model.zones[z].volume
        # ACH = (volumetric outdoor inflow) / volume, per hour
        ach[z] = (inflow_from_out[z] / rho_out) / vol * 3600 if vol > 0 else 0.0

    total_out_inflow = sum(inflow_from_out.values())  # kg/s
    total_vol = sum(model.zones[z].volume for z in zone_ids)
    whole_home_ach = (total_out_inflow / rho_out) / total_vol * 3600 if total_vol else 0.0

    return {
        "P": {z: float(P[idx[z]]) for z in zone_ids},
        "ach": ach,
        "whole_home_ach": whole_home_ach,
        "rho_in": rho_in, "rho_out": rho_out,
        "path_flows": path_flows,   # [(a, b, w_mass kg/s)] power-law paths
        "fans": fans,               # [(a, b, Q_vol m3/s)] constant-flow mixing
        "zone_ids": zone_ids,
    }
