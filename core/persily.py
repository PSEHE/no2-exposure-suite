"""Persily home library for CONTAM-Lite's single-home panel.

The 227 raw NIST dwellings — 209 from the CS-11 collection (pre-2000 stock,
NISTIR 7330 / 1997 RECS) plus 18 "2000-and-later" new-construction homes
(NIST TN 2329) — vendored under floorplans/persily/ and transformed at load by
core.transform (adds NO2 decay, interzone mixing, operable windows; the kitchen
source is injected at simulate time).

This library is SEPARATE from the 24-house scenario library (constants.HOUSES),
which the Explorer and the population panel depend on and must not change. Here
the live physics engine runs each home's actual geometry.

`build_manifest()` profiles every home (type, vintage, floor area, stories,
zones, single-dwelling vs multi-unit building) into web_data/persily_manifest.json;
run `python -m core.persily` to (re)build it.
"""
from __future__ import annotations

import glob
import json
import os

from . import config, prj, transform

TYPE_NAMES = {"AH": "Attached", "DH": "Detached",
              "MH": "Manufactured", "APT": "Apartment"}
SIM_ZONE_CAP = 250          # above this, the live solver is too slow (Phase D / buildings)


def _stem(path):
    return os.path.splitext(os.path.basename(path))[0]


def _home_type(name):
    return name.split("-")[0].split("_")[0].upper()


def load_persily(rel_path):
    """Parse + Sci.-Adv.-transform a vendored raw home.

    rel_path is relative to floorplans/persily/ (the manifest's `rel_path`)."""
    full = config.PERSILY_DIR / rel_path
    return transform.apply_modifications(prj.parse_prj(str(full)))


def load_manifest():
    with open(config.PERSILY_MANIFEST) as f:
        return json.load(f)["homes"]


def list_homes(simulatable_only=True, single_dwelling_only=True):
    homes = load_manifest()
    if single_dwelling_only:
        homes = [h for h in homes if h["category"] == "single_dwelling"]
    if simulatable_only:
        homes = [h for h in homes if h["simulatable"]]
    return homes


def bracket_by_area(homes, target_area):
    """Find the two homes bracketing `target_area` (ft²) for interpolation.

    `homes` should already be filtered to a comparable set (e.g. one type).
    Returns (below, above, weight) where weight in [0,1] is the fraction toward
    `above`. Clamps at the ends (below == above, weight 0 or 1) — no
    extrapolation beyond the available floor-area range."""
    pool = sorted(homes, key=lambda h: h["floor_area_ft2"])
    if not pool:
        return None, None, 0.0
    if target_area <= pool[0]["floor_area_ft2"]:
        return pool[0], pool[0], 0.0
    if target_area >= pool[-1]["floor_area_ft2"]:
        return pool[-1], pool[-1], 1.0
    below = [h for h in pool if h["floor_area_ft2"] <= target_area][-1]
    above = [h for h in pool if h["floor_area_ft2"] >= target_area][0]
    span = above["floor_area_ft2"] - below["floor_area_ft2"]
    w = (target_area - below["floor_area_ft2"]) / span if span > 0 else 0.0
    return below, above, w


def _stories(model):
    """Number of occupiable storeys (levels that hold a living zone)."""
    occ = {z.level for z in model.zones.values() if transform.is_living(z.name)}
    return max(1, len(occ))


def _categorize(typ, n_zones, n_kitchens, area_ft2):
    """single_dwelling (a home a person lives in) vs building (multi-unit APT)."""
    if typ != "APT":
        return "single_dwelling"
    # An apartment *unit* has one kitchen and is small; a whole building has
    # many units (several kitchens) or a large footprint / zone count.
    if n_kitchens > 1 or n_zones > 30 or area_ft2 > 2500:
        return "building"
    return "single_dwelling"


def build_manifest(with_baseline=True, verbose=True):
    """Profile every vendored Persily home into the manifest JSON."""
    import numpy as np
    from . import transport

    base = config.PERSILY_DIR
    homes = []
    for full in sorted(glob.glob(str(base / "**" / "*.prj"), recursive=True)):
        rel = os.path.relpath(full, base)
        name = _stem(full)
        typ = _home_type(name)
        source = "tn2329" if "tn2329" in rel else "cs11"
        model = transform.apply_modifications(prj.parse_prj(full))
        n_zones = len(model.zones)
        n_living = sum(1 for z in model.zones.values() if transform.is_living(z.name))
        n_kit = sum(1 for z in model.zones.values() if "kitchen" in z.name.lower())
        area = round(model.floor_area_m2() * 10.7639)
        category = _categorize(typ, n_zones, n_kit, area)
        kid = transform.kitchen_zone_id(model)
        runnable = (category == "single_dwelling" and n_zones <= SIM_ZONE_CAP
                    and kid is not None)

        # Baseline solve (single dwellings) -> modeled ACH + kitchen peak.
        ach = peak = None
        sim_err = None
        if runnable and with_baseline:
            try:
                res = transport.simulate(model, kitchen_zone=kid,
                                         T_out_C=5.0, wind_ms=3.0, hood="NoHood")
                arr = res["by_zone"].get(model.zones[kid].name)
                peak = float(np.max(arr[:144])) if arr is not None else None
                ach = float(res["whole_home_ach"])
            except Exception as e:
                sim_err = type(e).__name__

        # Mechanical (AHS) ventilation is modeled (Phase 4d): exhaust-only systems
        # (kitchen/bath fans) add realistic outdoor air to tight new-construction
        # homes, so they no longer solve airtight.
        has_mech_vent = sum(model.mech_extract.values()) > 1e-6
        # Exclude only true pathologies: a failed solve or a runaway baseline
        # peak (>2000 ppb signals a degenerate model, e.g. a stuck multi-unit).
        pathological = bool(sim_err) or (peak is not None and peak > 2000)
        simulatable = bool(runnable and peak is not None and not pathological)

        rec = {
            "id": name,
            "rel_path": rel,
            "type": typ,
            "type_name": TYPE_NAMES.get(typ, typ),
            "source": source,
            "vintage": "2000plus" if source == "tn2329" else "pre2000",
            "floor_area_ft2": area,
            "stories": _stories(model),
            "n_zones": n_zones,
            "n_living": n_living,
            "n_kitchens": n_kit,
            "category": category,
            "simulatable": simulatable,
            "has_mech_vent": has_mech_vent,
            "cooking_zone": model.zones[kid].name if kid is not None else None,
        }
        if ach is not None:
            rec["ref_whole_home_ach"] = round(ach, 3)
        if peak is not None:
            rec["ref_kitchen_peak_ppb"] = round(peak, 1)
        if sim_err:
            rec["sim_error"] = sim_err
        homes.append(rec)
        if verbose and len(homes) % 25 == 0:
            print(f"  ...{len(homes)} homes")

    homes.sort(key=lambda h: (h["type"], h["floor_area_ft2"]))
    payload = {
        "description": "Persily NIST dwelling library for CONTAM-Lite "
                       "(209 CS-11 pre-2000 + 18 TN 2329 2000-and-later).",
        "sim_zone_cap": SIM_ZONE_CAP,
        "n_homes": len(homes),
        "homes": homes,
    }
    config.PERSILY_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with open(config.PERSILY_MANIFEST, "w") as f:
        json.dump(payload, f, indent=1)
    return payload


if __name__ == "__main__":
    p = build_manifest()
    homes = p["homes"]
    from collections import Counter
    print(f"\nwrote {config.PERSILY_MANIFEST}  ({p['n_homes']} homes)")
    for cat in ("single_dwelling", "building"):
        sub = [h for h in homes if h["category"] == cat]
        bt = Counter(h["type"] for h in sub)
        sim = sum(1 for h in sub if h["simulatable"])
        print(f"  {cat}: {len(sub)}  ({dict(bt)})  simulatable={sim}")
    sd = [h for h in homes if h["category"] == "single_dwelling"]
    for t in ("AH", "DH", "MH", "APT"):
        areas = [h["floor_area_ft2"] for h in sd if h["type"] == t]
        if areas:
            print(f"  {t}: n={len(areas)}  area {min(areas)}-{max(areas)} ft2")
