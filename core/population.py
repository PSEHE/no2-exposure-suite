"""Population-level exposure + health outcomes.

Reweights the exact 86,400-scenario CONTAM library by population distributions
(housing stock, range-hood adoption, cooking intensity, climate, wind,
occupancy) — exactly the scale-up the papers do — rather than re-running the
physics per home. Health outcomes scale the papers' published national burdens
(pediatric asthma, adult mortality) with the modeled exposure and the gas/
propane-stove prevalence.
"""
from __future__ import annotations

import functools
import pickle

from . import config, constants as C, library as lib

# --- default behavior distributions (papers' produceHouseDict central case) ---
DEF_HOOD = {"NoHood": 0.74, "25CE": 0.04, "50CE": 0.13, "75CE": 0.09}
DEF_USE = {"low": 0.014, "med": 0.228, "medNoBk": 0.457, "high": 0.10}  # cooking only
DEF_WINDOW = {"closed": 0.70, "moderate": 0.25, "open": 0.05}
DEF_WIND = {"STILL": 0.083, "BREEZE": 0.608, "WINDY": 0.308}
DEF_OC = {"fifth_kitchen": 0.05, "fifth_outside": 0.05, "median": 0.80,
          "ninetyfifth_kitchen": 0.05, "ninetyfifth_outside": 0.05}
CLIMATE_TEMP = {"Very Cold": ("COLD", "COOL"), "Cold": ("COLD", "RT"),
                "Mixed": ("COOL", "WARM"), "Marine": ("COOL", "WARM"),
                "Hot": ("RT", "WARM")}

# Published national anchors (Sci. Adv. 2024) at baseline gas/propane prevalence.
BASELINE_GAS_FRACTION = 0.38           # ~38% of US homes cook with gas/propane
BASELINE_ASTHMA_CASES = 50_000         # current pediatric asthma attributable
BASELINE_DEATHS = 19_000               # adult deaths attributable (upper-ish)


@functools.lru_cache(maxsize=1)
def house_weights():
    # Prefer the repo-vendored JSON (works on any host); fall back to the pickle.
    import json
    if config.HOUSE_WEIGHTS_JSON.exists():
        with open(config.HOUSE_WEIGHTS_JSON) as f:
            return json.load(f)
    with open(config.SCALEUP / "_DICTS" / "house_weights.pkl", "rb") as f:
        return pickle.load(f)


@functools.lru_cache(maxsize=1)
def _archetype_volumes():
    import json
    with open(config.WEB_DATA / "archetypes.json") as f:
        arch = json.load(f)
    return {h: (a.get("total_volume_m3") or 1.0) for h, a in arch.items()}


def _norm(d):
    s = sum(d.values())
    return {k: v / s for k, v in d.items()} if s > 0 else d


def population_mean_exposure(*, house_w=None, hood=None, use=None, window=None,
                             climate="Mixed", wind=None, oc=None):
    """Population-mean STOVE-attributable long-term NO2 (ppb), for homes with stoves."""
    no2, _ = lib.load_library()
    house_w = house_w or house_weights()
    hood = _norm(hood or DEF_HOOD)
    use = _norm(use or DEF_USE)
    window = _norm(window or DEF_WINDOW)
    wind = _norm(wind or DEF_WIND)
    oc = _norm(oc or DEF_OC)
    wt, st = CLIMATE_TEMP.get(climate, ("COOL", "WARM"))
    temps = {wt: 0.5} if wt == st else {wt: 0.5, st: 0.5}

    total = 0.0
    for house, hw in house_w.items():
        if hw <= 0:
            continue
        acc = 0.0
        for hd, ph in hood.items():
            for u, pu in use.items():
                for w, pw in window.items():
                    for t, pt in temps.items():
                        for wd, pwd in wind.items():
                            for o, po in oc.items():
                                k = lib.scenario_key(house, hd, u, w, t, wd, o)
                                acc += no2[k]["dayavg"] * ph * pu * pw * pt * pwd * po
        total += hw * acc
    return total


@functools.lru_cache(maxsize=1)
def baseline_exposure():
    return population_mean_exposure()


def health_outcomes(mean_stove_no2, gas_fraction):
    """Scale the papers' national burdens by exposure x prevalence (transparent
    linear anchoring to the published central estimates)."""
    base = baseline_exposure()
    scale = (gas_fraction / BASELINE_GAS_FRACTION) * (mean_stove_no2 / base if base > 0 else 0.0)
    asthma = BASELINE_ASTHMA_CASES * scale
    deaths = BASELINE_DEATHS * scale
    cost = deaths * C.VSL_USD + asthma * C.ASTHMA_COST_USD_PER_CASE_YR
    return {"asthma_cases": asthma, "deaths": deaths, "cost_usd": cost, "scale": scale}


# --- slider helpers: map intuitive controls to distributions ---
def hood_dist(adoption):
    """adoption = fraction of homes with an effective outside-venting hood."""
    a = max(0.0, min(1.0, adoption))
    return {"NoHood": 1 - a, "50CE": 0.4 * a, "75CE": 0.6 * a}


def use_dist(intensity):
    """intensity 0..1 blends light -> heavy cooking."""
    x = max(0.0, min(1.0, intensity))
    return {"low": 0.6 * (1 - x), "med": 0.3 + 0.2 * x, "medNoBk": 0.1, "high": 0.6 * x}


# The panel's historical central case: use_dist(0.35) anchors the published
# burdens, so the "x typical" cooking slider must reproduce it at the middle
# (typical-household) tick.
DEFAULT_COOK_INTENSITY = 0.35
# Mean open-hours of the papers' central window mix (0.70/0.25/0.05 over
# closed/moderate 4h/open 24h): 0.25*4 + 0.05*24.
DEF_WINDOW_MEAN_HOURS = 2.2


def use_from_amount(amount, ticks):
    """Map the single-home-style cooking slider (x typical household) to a
    population (use_dist, sub_light_scale).

    `ticks` = the (light, typical, heavy) anchor positions from the single-home
    slider. Piecewise linear so the anchors land exactly: light tick -> all
    light-cooking households, typical tick -> the papers' central case, heavy
    tick and above -> all heavy. Below the light tick, the library can't cook
    less, so the returned scale (amount/light_tick -> 0) linearly damps the
    stove-NO2 mean toward zero (0 = nobody cooks)."""
    tl, tm, th = ticks
    if amount <= 0:
        return use_dist(0.0), 0.0
    if amount < tl:
        return use_dist(0.0), amount / tl
    if amount <= tm:
        x = DEFAULT_COOK_INTENSITY * (amount - tl) / (tm - tl)
    else:
        x = (DEFAULT_COOK_INTENSITY
             + (1.0 - DEFAULT_COOK_INTENSITY) * min(1.0, (amount - tm) / (th - tm)))
    return use_dist(x), 1.0


def window_hours_dist(hours):
    """Population window mix from a mean open-hours-per-day slider.

    Piecewise-linear blend through three anchor distributions: 0 h -> all
    closed, DEF_WINDOW_MEAN_HOURS -> the papers' central mix (exactly, so the
    default anchors the published burdens), 24 h -> all open. The library's
    window axis is an all-day behavior category, so per-room or cooking-timed
    opening (single-home features) is NOT representable here."""
    h = max(0.0, min(24.0, float(hours)))
    closed = {"closed": 1.0, "moderate": 0.0, "open": 0.0}
    opend = {"closed": 0.0, "moderate": 0.0, "open": 1.0}
    if h <= DEF_WINDOW_MEAN_HOURS:
        w = h / DEF_WINDOW_MEAN_HOURS
        lo, hi = closed, DEF_WINDOW
    else:
        w = (h - DEF_WINDOW_MEAN_HOURS) / (24.0 - DEF_WINDOW_MEAN_HOURS)
        lo, hi = DEF_WINDOW, opend
    return {k: (1 - w) * lo[k] + w * hi[k] for k in ("closed", "moderate", "open")}


def home_size_weights(shift):
    """shift -1 (smaller homes) .. +1 (larger). Reweights house_weights by total
    home volume (smaller homes -> higher exposure)."""
    hw = dict(house_weights())
    if abs(shift) < 1e-6:
        return hw
    import numpy as np
    vols = _archetype_volumes()
    v = np.array([vols.get(h, 1.0) for h in hw])
    vnorm = (v - v.mean()) / (v.std() + 1e-9)
    factor = np.exp(shift * vnorm)            # shift>0 favors larger homes
    return _norm({h: hw[h] * f for h, f in zip(hw, factor)})
