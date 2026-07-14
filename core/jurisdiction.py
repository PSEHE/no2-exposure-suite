"""Jurisdiction (state) defaults for the population/health panel.

Aggregates the per-ZIP housing + climate + fuel table (the Explorer's
zips_abbr_updated.csv, 30,855 ZIPs) to a state — population-weighted — so a
policymaker can pick a jurisdiction and have its STRUCTURAL context populate:
the housing-stock mix (mapped onto the 24 CONTAM archetypes), the climate
(seasonal temperature mix), the wind distribution, the outdoor-NO2 level, the
gas/propane cooking prevalence, and the population.

Behavioral inputs (cooking, window, occupancy) are NOT set here — those stay at
the source paper's national distributions (see core.population). Only the
building/structural and location context comes from the jurisdiction.

The aggregated profiles are precomputed into web_data/jurisdiction_profiles.json
(run `python -m core.jurisdiction`) so the deployed app needs only that small
file, not the full ZIP table. If the raw table is present locally, profiles are
built from it on demand as a fallback.
"""
from __future__ import annotations

import functools
import json

from . import config
from .library import select_archetype

# --- ZIP-table column groups ---------------------------------------------
TYPE_COLS = {"Mobile": "MH", "SFD": "DH", "SFA": "AH", "MF24": "APT", "MF5+": "APT"}
SQFT_COLS = ["floor_area_0_1499", "floor_area_1500_2499",
             "floor_area_2500_3999", "floor_area_4000+"]
VINTAGE_COLS = ["vintage_prior_1940", "vintage_1940_1959", "vintage_1960_1979",
                "vintage_1980_1999", "vintage_2000_2009", "vintage_2010s"]
TEMP_CATS = ["COLD", "COOL", "RT", "WARM"]
# ZIP table has VCOLD (winter) and HOT (summer) beyond the library's 4-category
# temp axis — clamp them to the nearest available library category.
TEMP_REMAP = {"VCOLD": "COLD", "HOT": "WARM"}
WIND_COLS = {"STILL": "still", "BREEZE": "breeze", "WINDY": "windy_grouped"}

PROFILES_JSON = config.WEB_DATA / "jurisdiction_profiles.json"

STATE_NAMES = {
    "US": "United States", "AL": "Alabama", "AR": "Arkansas", "AZ": "Arizona",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DC": "District of Columbia",
    "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "IA": "Iowa", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "MA": "Massachusetts", "MD": "Maryland", "ME": "Maine", "MI": "Michigan",
    "MN": "Minnesota", "MO": "Missouri", "MS": "Mississippi", "MT": "Montana",
    "NC": "North Carolina", "ND": "North Dakota", "NE": "Nebraska", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NV": "Nevada", "NY": "New York",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VA": "Virginia", "VT": "Vermont", "WA": "Washington",
    "WI": "Wisconsin", "WV": "West Virginia", "WY": "Wyoming",
}


def display_name(code):
    return STATE_NAMES.get(code, code)

# US Census average household size (persons/household, ACS ~2020) — turns a
# jurisdiction population into a household count for absolute burden scaling.
PERSONS_PER_HOUSEHOLD = 2.51


# --- housing-stock -> 24-archetype weights -------------------------------
def house_weights_from_marginals(types, sqft, vintage, p_single, p_ac):
    """Cross the (independent) housing marginals into the 24 archetype weights.

    types/sqft/vintage: {label: probability} (each summing to 1); p_single =
    P(single-story), p_ac = P(central AC). Mirrors the papers' select_floorplan
    scale-up (via library.select_archetype) under a marginal-independence
    assumption — the only joint info the ZIP table carries."""
    w = {}
    for tcol, typehuq in TYPE_COLS.items():
        pt = types.get(tcol, 0.0)
        if pt <= 0:
            continue
        for scol in SQFT_COLS:
            ps = sqft.get(scol, 0.0)
            if ps <= 0:
                continue
            for vcol in VINTAGE_COLS:
                pv = vintage.get(vcol, 0.0)
                if pv <= 0:
                    continue
                for story, pstory in (("single_story", p_single), ("multi", 1 - p_single)):
                    if pstory <= 0:
                        continue
                    for ac, pac in (("yesAHS", p_ac), ("no", 1 - p_ac)):
                        if pac <= 0:
                            continue
                        arch = select_archetype(typehuq, scol, vcol, story, ac)
                        w[arch] = w.get(arch, 0.0) + pt * ps * pv * pstory * pac
    s = sum(w.values())
    return {k: v / s for k, v in w.items()} if s > 0 else w


# --- build profiles from the raw ZIP table -------------------------------
def _norm_cols(row_or_series, cols):
    vals = {c: max(0.0, float(row_or_series[c])) for c in cols}
    s = sum(vals.values())
    return {c: (v / s if s > 0 else 0.0) for c, v in vals.items()}


def build_profiles():
    """Build per-state (+ 'US') aggregated profiles from the raw ZIP table."""
    import numpy as np
    import pandas as pd

    df = pd.read_csv(config.ZIP_TABLE, low_memory=False)
    df = df[df["population"].fillna(0) > 0].copy()

    def aggregate(sub, name, code):
        pop = sub["population"].to_numpy(float)
        W = pop / pop.sum()

        def wm(col):                          # population-weighted mean of a column
            return float(np.average(sub[col].fillna(0).to_numpy(float), weights=pop))

        types = _norm_cols(pd.Series({c: wm(c) for c in TYPE_COLS}), list(TYPE_COLS))
        sqft = _norm_cols(pd.Series({c: wm(c) for c in SQFT_COLS}), SQFT_COLS)
        vintage = _norm_cols(pd.Series({c: wm(c) for c in VINTAGE_COLS}), VINTAGE_COLS)
        p_single = wm("story_eq_1") / 100.0
        p_ac = wm("centralAC") / 100.0
        hw = house_weights_from_marginals(types, sqft, vintage, p_single, p_ac)

        # seasonal temperature mix: 50% winter + 50% summer category shares
        temps = {t: 0.0 for t in TEMP_CATS}
        for season in ("winter_temp", "summer_temp"):
            share = sub.groupby(season)["population"].sum() / pop.sum()
            for cat, p in share.items():
                cat = TEMP_REMAP.get(cat, cat)
                if cat in temps:
                    temps[cat] += 0.5 * float(p)
        wind = {k: wm(c) for k, c in WIND_COLS.items()}
        wsum = sum(wind.values()) or 1.0
        wind = {k: v / wsum for k, v in wind.items()}

        return {
            "name": name, "code": code,
            "population": int(pop.sum()),
            "gas_pct": round(wm("cooking_NG") + wm("cooking_Propane"), 2),
            "outdoor_no2_ppb": round(wm("no2_ppb"), 2),
            "house_weights": {k: round(v, 6) for k, v in hw.items()},
            "temps": {k: round(v, 4) for k, v in temps.items() if v > 0},
            "wind": {k: round(v, 4) for k, v in wind.items()},
            "housing_pct": {  # for a context readout
                "Detached": round(types.get("SFD", 0) * 100, 1),
                "Attached": round(types.get("SFA", 0) * 100, 1),
                "Manufactured": round(types.get("Mobile", 0) * 100, 1),
                "Multifamily": round((types.get("MF24", 0) + types.get("MF5+", 0)) * 100, 1),
            },
            "central_ac_pct": round(p_ac * 100, 1),
        }

    profiles = {"US": aggregate(df, "United States", "US")}
    # Pin the national housing weights to the exact shipped scale-up (built from
    # RECS joint microdata) so the paper's national burden anchor stays exact;
    # states use the marginal-reconstruction (type split exact, within-type ~0.07
    # total-variation off — the only joint info the ZIP table carries).
    if config.HOUSE_WEIGHTS_JSON.exists():
        with open(config.HOUSE_WEIGHTS_JSON) as f:
            profiles["US"]["house_weights"] = {k: round(v, 6)
                                               for k, v in json.load(f).items()}
    for code, sub in df.groupby("STATE"):
        if isinstance(code, str) and code.strip():
            profiles[code] = aggregate(sub, code, code)
    return profiles


# --- load / access --------------------------------------------------------
@functools.lru_cache(maxsize=1)
def load_profiles():
    """Vendored profiles if present, else build from the raw ZIP table."""
    if PROFILES_JSON.exists():
        with open(PROFILES_JSON) as f:
            return json.load(f)
    return build_profiles()


def list_jurisdictions():
    """'US' first, then states alphabetically."""
    p = load_profiles()
    states = sorted(k for k in p if k != "US")
    return ["US"] + states


def profile(code):
    return load_profiles().get(code, load_profiles()["US"])


def national_gas_households():
    us = profile("US")
    return us["population"] / PERSONS_PER_HOUSEHOLD * (us["gas_pct"] / 100.0)


def gas_households(code, gas_fraction):
    return profile(code)["population"] / PERSONS_PER_HOUSEHOLD * gas_fraction


if __name__ == "__main__":
    profs = build_profiles()
    with open(PROFILES_JSON, "w") as f:
        json.dump(profs, f, indent=1, sort_keys=True)
    print(f"wrote {len(profs)} jurisdiction profiles -> {PROFILES_JSON}")
    for c in ("US", "CA", "NY", "TX"):
        p = profs[c]
        print(f"  {c}: gas {p['gas_pct']}% · outdoor {p['outdoor_no2_ppb']} ppb · "
              f"pop {p['population']:,} · {p['housing_pct']}")
