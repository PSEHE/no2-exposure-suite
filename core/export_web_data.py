"""Emit trimmed, widget-ready JSON from the source CONTAM data into web_data/.

Outputs:
  scenario_library.json  flat arrays of exact CONTAM results (the lookup engine)
  zip_data.json          per-ZIP outdoor NO2, climate, wind, and a default archetype
  archetypes.json        metadata for the 24 floorplans (rooms, volumes, labels)

Run with:  python -m core.export_web_data
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from . import config, constants, library


def _round_sig(x, sig=4):
    if x is None or (isinstance(x, float) and (np.isnan(x) or x == 0)):
        return 0
    return float(f"{x:.{sig}g}")


# --- 1. Scenario library (the lookup engine) -------------------------------
def build_scenario_library():
    no2, conta = library.load_library()
    H, HD, U, W, T, WD, OC = (
        constants.HOUSES, constants.HOODS, constants.USES, constants.WINDOWS,
        constants.TEMPS, constants.WINDS, constants.OCCUPANCIES,
    )
    n = len(H) * len(HD) * len(U) * len(W) * len(T) * len(WD) * len(OC)
    out = {
        "no2": {m: [0.0] * n for m in ["peak", "hravg", "eighthravg", "dayavg"]},
        "conta": {m: [0.0] * n for m in ["hravg", "dayavg"]},
    }
    i = 0
    for h in H:
        for hd in HD:
            for u in U:
                for w in W:
                    for t in T:
                        for wd in WD:
                            for oc in OC:
                                k = library.scenario_key(h, hd, u, w, t, wd, oc)
                                a, c = no2[k], conta[k]
                                out["no2"]["peak"][i] = _round_sig(a["peak"])
                                out["no2"]["hravg"][i] = _round_sig(a["hravg"])
                                out["no2"]["eighthravg"][i] = _round_sig(a["eighthravg"])
                                out["no2"]["dayavg"][i] = _round_sig(a["dayavg"])
                                out["conta"]["hravg"][i] = _round_sig(c["hravg"])
                                out["conta"]["dayavg"][i] = _round_sig(c["dayavg"])
                                i += 1
    # Schema documents the flat-array index ordering for the JS engine:
    #   idx = ((((((hi*nHood+hdi)*nUse+ui)*nWin+wi)*nTemp+ti)*nWind+wdi)*nOc+oci)
    out["schema"] = {
        "houses": H, "hood": HD, "use": U, "window": W,
        "temp": T, "wind": WD, "oc": OC,
        "order": ["house", "hood", "use", "window", "temp", "wind", "oc"],
        "count": n,
    }
    assert i == n, (i, n)
    return out


# --- 2. Per-ZIP data + default archetype -----------------------------------
_TYPE_COLS = {"Mobile": "MH", "SFD": "DH", "SFA": "AH", "MF24": "APT", "MF5+": "APT"}
_SQFT_COLS = ["floor_area_0_1499", "floor_area_1500_2499",
              "floor_area_2500_3999", "floor_area_4000+"]
_VINTAGE_COLS = ["vintage_prior_1940", "vintage_1940_1959", "vintage_1960_1979",
                 "vintage_1980_1999", "vintage_2000_2009", "vintage_2010s"]


def _default_archetype(row):
    """Pick a representative archetype for a ZIP from its housing-stock mix."""
    try:
        typehuq = _TYPE_COLS[max(_TYPE_COLS, key=lambda c: _num(row.get(c)))]
        sqft = max(_SQFT_COLS, key=lambda c: _num(row.get(c)))
        vintage = max(_VINTAGE_COLS, key=lambda c: _num(row.get(c)))
        stories = "single_story" if _num(row.get("story_eq_1")) >= 50 else "multi"
        ac = "yesAHS" if _num(row.get("centralAC")) >= 50 else "no"
        return library.select_archetype(typehuq, sqft, vintage, stories, ac)
    except Exception:
        return "DH-1"


def _num(v):
    try:
        f = float(v)
        return 0.0 if np.isnan(f) else f
    except (TypeError, ValueError):
        return 0.0


def build_zip_data():
    df = library.load_zip_table()
    out = {}
    for _, r in df.iterrows():
        z = int(r["ZIP"])
        # Trimmed to exactly what the Explorer needs (keeps the single-file
        # build small). lat/lon, climate label, and gas-prevalence are dropped;
        # Product 2 reads those from the full source tables instead.
        out[str(z)] = {
            "o": _round_sig(_num(r["no2_ppb"]), 4),          # outdoor NO2 (ppb)
            "wt": r["winter_temp"], "st": r["summer_temp"],   # seasonal temp categories
            "w": [_round_sig(_num(r["still"]), 3),            # wind [still, breeze, windy]
                  _round_sig(_num(r["breeze"]), 3),
                  _round_sig(_num(r["windy_grouped"]), 3)],
            "c": r["city"], "s": r["STATE"],
            "arch": _default_archetype(r),                    # default floorplan for this ZIP
        }
    return out


# --- 3. Archetype metadata (rooms + volumes + labels) ----------------------
_TYPE_NAMES = {"MH": "Mobile / manufactured home", "DH": "Single-family detached",
               "AH": "Single-family attached", "APT": "Apartment / multifamily"}


def _zone_volumes(prj_path):
    """Light parse of a CONTAM .prj zones block -> {zone_id: (volume_m3, name)}."""
    vols = {}
    try:
        lines = prj_path.read_text(errors="ignore").splitlines()
        in_zones = False
        for ln in lines:
            s = ln.strip()
            if s.endswith("! zones:"):
                in_zones = True
                continue
            if in_zones:
                if s.startswith("-999"):
                    break
                if s.startswith("!"):
                    continue
                f = s.split()
                if len(f) >= 11:
                    try:
                        vols[int(f[0])] = (float(f[7]), f[10])
                    except (ValueError, IndexError):
                        pass
    except Exception:
        pass
    return vols


def build_archetypes():
    inputs = pd.read_csv(config.HOUSE_INPUTS)
    out = {}
    for _, r in inputs.iterrows():
        house = r["House"]
        if house not in constants.HOUSES:
            continue
        prj = config.DATABASE_HOUSES / house / f"{house}.prj"
        vols = _zone_volumes(prj) if prj.exists() else {}
        kitchen_id = int(r["Kitchen"]) if not pd.isna(r["Kitchen"]) else None
        total_vol = round(sum(v for v, _ in vols.values()), 1) if vols else None
        kvol = round(vols[kitchen_id][0], 1) if kitchen_id in vols else None
        out[house] = {
            "type": house.split("-")[0],
            "type_name": _TYPE_NAMES.get(house.split("-")[0], house),
            "rooms": {
                "kitchen": _intornone(r.get("Kitchen")),
                "livingroom": _intornone(r.get("Livingroom")),
                "bedroom1": _intornone(r.get("Bedroom1")),
                "bedroom2": _intornone(r.get("Bedroom2")),
            },
            "n_zones": len(vols) if vols else None,
            "total_volume_m3": total_vol,
            "kitchen_volume_m3": kvol,
        }
    return out


def _intornone(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# --- Main ------------------------------------------------------------------
def main():
    config.WEB_DATA.mkdir(exist_ok=True)

    print("Building scenario_library.json …")
    lib = build_scenario_library()
    _write("scenario_library.json", lib)

    print("Building zip_data.json …")
    _write("zip_data.json", build_zip_data())

    print("Building archetypes.json …")
    _write("archetypes.json", build_archetypes())

    # --- sanity / parity check ---
    print("\nSanity check (DH-2, no hood, average cooking, windows ~4h, typical kitchen time, ZIP 94112):")
    res = library.weighted_annual_exposure("DH-2", "NoHood", "med", "moderate", "median", 94112)
    for k, v in res.items():
        print(f"  {k:24s} {v:.3f}" if isinstance(v, float) else f"  {k:24s} {v}")


def _write(name, obj):
    path = config.WEB_DATA / name
    with open(path, "w") as f:
        json.dump(obj, f, separators=(",", ":"))
    mb = path.stat().st_size / 1e6
    print(f"  wrote {path}  ({mb:.1f} MB)")


if __name__ == "__main__":
    main()
