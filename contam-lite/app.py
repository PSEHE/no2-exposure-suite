"""CONTAM-Lite — interactive multizone NO2 engine (research-grade).

A from-first-principles port of the CONTAM physics behind Kashtan et al.
2024/2025: parses a floorplan, solves the airflow network, and integrates
contaminant transport — live, for any physical conditions (single-home panel).
A population panel reweights the exact CONTAM library by population
distributions and reports exposure + health outcomes.

Run from the repo root:  streamlit run contam-lite/app.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import json
import numpy as np
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

# stdlib JSON engine: plotly's lazy orjson import can race under Streamlit's
# rerun threads and poison the session ("partially initialized module");
# figures here are small, so the faster engine buys nothing.
pio.json.config.default_engine = "json"

from core import (config, constants, diagnostics as dg, jurisdiction as juris,
                  prj, transport, population, persily, transform, apartments)

st.set_page_config(page_title="CONTAM-Lite — NO₂ engine", layout="wide")

WHO_1HR = constants.WHO_1HR_PPB
EPA_1HR = constants.EPA_1HR_PPB
WHO_ANNUAL = constants.WHO_ANNUAL_PPB

COOKING_PATTERNS = {
    "None": [],
    "Light (one short meal)": [
        {"start": 18.0, "min": 10, "cooktop": True, "oven": False}],
    "Average (breakfast + dinner)": transport.DEFAULT_COOKING,
    "Heavy (3 meals, oven)": [
        {"start": 7.0, "min": 20, "cooktop": True, "oven": True},
        {"start": 12.0, "min": 15, "cooktop": True, "oven": False},
        {"start": 18.0, "min": 40, "cooktop": True, "oven": True}],
}
TYPE_NAMES = {"DH": "Detached", "AH": "Attached", "MH": "Manufactured", "APT": "Apartment"}


def _pattern_no2_total(pattern):
    """Total daily stove NO₂ of a cooking pattern (kg, at standard rates)."""
    tot = 0.0
    for e in pattern:
        dur = e["min"] * 60.0
        if e.get("cooktop", True):
            tot += dur * transport.STD_COOKTOP_KG_S
        if e.get("oven", False):
            tot += dur * transport.STD_OVEN_KG_S
    return tot


# Cooking-amount slider anchors: the three patterns on a "× typical household"
# scale (their total emissions relative to Average). Light/Heavy sit near the
# 5th/95th percentiles of household cooking in the papers' behavioral axis.
_T_AVG = _pattern_no2_total(COOKING_PATTERNS["Average (breakfast + dinner)"])
COOKING_ANCHORS = [
    (name, COOKING_PATTERNS[name], _pattern_no2_total(COOKING_PATTERNS[name]) / _T_AVG)
    for name in ("Light (one short meal)", "Average (breakfast + dinner)",
                 "Heavy (3 meals, oven)")]
COOKING_AMOUNT_MAX = 3.0


def cooking_from_amount(amount):
    """Map the cooking-amount slider to (pattern, emission_scale).

    Uses the nearest anchor's meal SCHEDULE (boundaries at tick midpoints) and
    scales its emissions so total stove NO₂ varies continuously with the
    slider; at each tick the anchor pattern is reproduced exactly (scale 1).
    amount<=0 means no cooking."""
    if amount <= 0:
        return [], 0.0
    ticks = [t for _, _, t in COOKING_ANCHORS]
    bounds = [(ticks[0] + ticks[1]) / 2, (ticks[1] + ticks[2]) / 2]
    i = 0 if amount < bounds[0] else (1 if amount < bounds[1] else 2)
    _, pattern, tick = COOKING_ANCHORS[i]
    return pattern, amount / tick


def _cooking_marks_html(vmax=COOKING_AMOUNT_MAX):
    """Unobtrusive tick marks under the cooking slider at the anchor positions."""
    marks = "".join(
        f'<span style="position:absolute;left:{t / vmax * 100:.1f}%;'
        f'transform:translateX(-50%);color:#9aa0a6;font-size:9px;'
        f'line-height:1;">▲</span>'
        for _, _, t in COOKING_ANCHORS)
    return (f'<div style="position:relative;height:10px;margin-top:-10px;">'
            f'{marks}</div>')


COOKING_LEGEND = ("▲ middle: most households cook like this · "
                  "▲ lower and upper: about 5% of households cook like this")


def _usd(x):
    """Compact USD string, unit chosen by magnitude ($B / $M / $)."""
    ax = abs(x)
    if ax >= 1e9:
        return f"${x/1e9:,.2f}B"
    if ax >= 1e6:
        return f"${x/1e6:,.0f}M"
    return f"${x:,.0f}"


@st.cache_resource
def load_archetypes():
    with open(ROOT / "web_data" / "archetypes.json") as f:
        return json.load(f)


@st.cache_resource
def load_model(house):
    """Paper home under the uniform-mixing policy (doorway top-up where the
    shipped file lacks fans; flows normalized in the solver)."""
    return persily.load_paper_home(house)


@st.cache_resource
def parse_uploaded(name, text):
    return prj.parse_prj_text(text, label=name)


def has_no2_source(model):
    for s in model.sources:
        el = model.source_elements.get(s.element)
        if el and el.species == "NO2" and "ov" not in el.name:
            return True
    return False


def kitchen_zone_name(model):
    for s in model.sources:
        el = model.source_elements.get(s.element)
        if el and el.species == "NO2" and "ov" not in el.name:
            return model.zones[s.zone].name
    for z in model.zones.values():
        if z.name.lower() == "kitchen":
            return z.name
    return None


def rolling_max_1h(arr, dt_min=10):
    w = max(1, int(round(60 / dt_min)))
    if len(arr) < w:
        return float(np.mean(arr))
    return float(np.max(np.convolve(arr, np.ones(w) / w, mode="valid")))


@st.cache_data
def persily_homes():
    """The simulatable single dwellings from the NIST Persily library (152)."""
    return persily.list_homes(simulatable_only=True, single_dwelling_only=True)


@st.cache_resource
def load_persily_model(rel_path):
    """Parse + Sci.-Adv.-transform a vendored Persily home (cached)."""
    return persily.load_persily(rel_path)


@st.cache_data
def apartment_buildings():
    """Tractable apartment buildings for the full-building stack panel."""
    return apartments.list_buildings()


@st.cache_resource
def load_apartment_model(rel_path):
    return apartments.load_building(rel_path)


def persily_label(h):
    return (f"{TYPE_NAMES.get(h['type'], h['type'])} · {h['floor_area_ft2']:,} ft² · "
            f"{h['stories']}-story ({h['id']})")


def home_avg_curve(res):
    """Mean NO₂ over living zones at each timestep (a whole-home average)."""
    arrs = [a for n, a in res["by_zone"].items() if transform.is_living(n)]
    return np.mean(arrs, axis=0) if arrs else np.zeros_like(res["t"])


def rest_avg_curve(res, kitchen_name):
    """Mean NO₂ over living zones EXCLUDING the kitchen (rest of the house)."""
    arrs = [a for n, a in res["by_zone"].items()
            if transform.is_living(n) and n != kitchen_name]
    return np.mean(arrs, axis=0) if arrs else np.zeros_like(res["t"])


def run_persily(entry, scenario):
    """Simulate a Persily home under the scenario; return (res, kitchen_name, kitchen_curve)."""
    m = load_persily_model(entry["rel_path"])
    kid = transform.kitchen_zone_id(m)
    res = transport.simulate(m, kitchen_zone=kid, **apply_window_spec(scenario, kid))
    kname = m.zones[kid].name if kid is not None else None
    return res, kname, res["by_zone"].get(kname, np.zeros_like(res["t"]))


def metrics_row(k_avg, mx1, rest_avg):
    c1, c2, c3 = st.columns(3)
    c1.metric("Average NO₂ in kitchen", f"{k_avg:.1f} ppb",
              help="24-hour average kitchen concentration.")
    c2.metric("Kitchen max 1-hr average", f"{mx1:.0f} ppb",
              delta=f"{mx1 - EPA_1HR:.0f} vs EPA/WHO 1-hr", delta_color="inverse")
    c3.metric("Average NO₂ in rest of the house", f"{rest_avg:.1f} ppb",
              help="24-hour average over the other living rooms "
                   "(kitchen excluded; unconditioned spaces excluded).")


def scenario_sidebar(prefix="", no2_default=7.0, window_style="fraction",
                     cooking_style="menu"):
    """Shared environment/ventilation/cooking knobs -> a simulate() kwargs dict.

    window_style="fraction": the research-grade 0..1 opening slider (diagnostics).
    window_style="hours": hours-per-day budget + which-windows menu + an
    open-during-cooking toggle; the concrete schedule is resolved per model by
    apply_window_spec() (the kitchen zone id differs per home).
    cooking_style="menu": meal-pattern menu + burner-intensity slider
    (diagnostics). "slider": one cooking-amount slider with tick marks at the
    Light/Average/Heavy anchors (see cooking_from_amount)."""
    st.sidebar.subheader("Environment")
    T_out = st.sidebar.slider("Outdoor temperature (°C)", -15, 38, 5, key=f"{prefix}T")
    wind = st.sidebar.slider("Wind speed (m/s)", 0.0, 12.0, 3.0, 0.5, key=f"{prefix}w")
    outdoor_no2 = st.sidebar.slider("Outdoor NO₂ (ppb)", 0.0, 40.0, no2_default, 0.5,
                                    key=f"{prefix}o")
    st.sidebar.subheader("Ventilation")
    spec = None
    if window_style == "hours":
        hrs = st.sidebar.slider("Window open time (hours/day)", 0.0, 24.0, 0.0, 0.5,
                                key=f"{prefix}wh")
        which = st.sidebar.selectbox("Which windows",
                                     ["All windows", "Kitchen window only"],
                                     key=f"{prefix}ww")
        during = st.sidebar.toggle("Open during cooking", value=True,
                                   key=f"{prefix}wc")
        start = 12.0
        if not during:
            start = st.sidebar.slider("Opens at (hour of day)", 0.0, 23.5, 12.0, 0.5,
                                      key=f"{prefix}ws")
        if hrs > 0:
            st.sidebar.caption(
                ("Windows open when the stove turns on and linger after — your open "
                 "time is split across meals by meal length."
                 if during else
                 f"One block starting at {start:g}:00.")
                + " While open, windows are at the calibrated open state (0.7).")
        window_open = 0.0
        spec = {"hours": hrs, "kitchen_only": which == "Kitchen window only",
                "during_cooking": during, "start_hour": start}
    else:
        window_open = st.sidebar.slider("Window opening (0 = closed, 1 = wide)",
                                        0.0, 1.0, 0.0, 0.05, key=f"{prefix}win")
    hood = st.sidebar.selectbox(
        "Range hood", ["NoHood", "25CE", "50CE", "75CE"],
        format_func=lambda h: {"NoHood": "None / recirculating", "25CE": "Standard (25%)",
                               "50CE": "Good (50%)", "75CE": "High-efficiency (75%)"}[h],
        key=f"{prefix}h")
    st.sidebar.subheader("Cooking")
    if cooking_style == "slider":
        amount = st.sidebar.slider("Cooking amount (× typical household)",
                                   0.0, COOKING_AMOUNT_MAX, 1.0, 0.05,
                                   key=f"{prefix}ca")
        st.sidebar.markdown(_cooking_marks_html(), unsafe_allow_html=True)
        st.sidebar.caption(COOKING_LEGEND)
        cooking, emission_scale = cooking_from_amount(amount)
    else:
        pattern = st.sidebar.selectbox("Meal pattern", list(COOKING_PATTERNS),
                                       index=2, key=f"{prefix}p")
        emission_scale = st.sidebar.slider("Burner intensity (× one burner)",
                                           0.5, 4.0, 1.0, 0.25, key=f"{prefix}i")
        cooking = COOKING_PATTERNS[pattern]
    scenario = dict(T_out_C=T_out, wind_ms=wind, window_open=window_open, hood=hood,
                    cooking=cooking, C_out_ppb=outdoor_no2,
                    emission_scale=emission_scale)
    if spec is not None:
        scenario["_window_spec"] = spec
    return scenario


def apply_window_spec(scenario, kitchen_zid):
    """Resolve the sidebar's window spec into a concrete window_schedule for
    ONE model (kitchen-only needs that model's kitchen zone id). Returns a new
    kwargs dict safe to pass to transport.simulate."""
    sc = dict(scenario)
    spec = sc.pop("_window_spec", None)
    if not spec or spec["hours"] <= 0:
        return sc
    open_value = ({kitchen_zid: transport.OPEN_FRACTION}
                  if spec["kitchen_only"] and kitchen_zid is not None
                  else transport.OPEN_FRACTION)
    sc["window_schedule"] = transport.window_schedule_from_hours(
        spec["hours"], cooking=sc.get("cooking"),
        during_cooking=spec["during_cooking"],
        start_hour=spec["start_hour"], open_value=open_value)
    return sc


arch = load_archetypes()
st.sidebar.title("CONTAM-Lite")
st.sidebar.caption("First-principles multizone NO₂ engine — Kashtan et al. 2024/2025.")
panel = st.sidebar.radio("Panel", ["Single home", "Population & health", "Diagnostics"])
st.sidebar.divider()


# ============================ SINGLE HOME ============================
if panel == "Single home":
    manifest = persily_homes()        # 152 single dwellings (NIST Persily set)
    mode = st.sidebar.radio("Choose your home by",
                            ["Describe your home", "Browse homes",
                             "Apartment building", "Upload a .prj"])

    bracket = sel_entry = upload_model = upload_kitchen = None
    apt_model = apt_meta = apt_level = apt_floor_no = apt_tag = None

    if mode == "Describe your home":
        c_type = st.sidebar.selectbox("Home type", ["DH", "AH", "MH"],
                                      format_func=lambda t: TYPE_NAMES[t])
        type_pool = [h for h in manifest if h["type"] == c_type]
        vints = sorted({h["vintage"] for h in type_pool})
        if len(vints) > 1:                       # only show when 2000+ homes are live
            vintage = st.sidebar.selectbox(
                "Vintage", ["Any"] + vints,
                format_func=lambda v: {"Any": "Any vintage", "pre2000": "Built before 2000",
                                       "2000plus": "New construction (2000+)"}.get(v, v))
        else:
            vintage = "Any"
        pool = [h for h in type_pool if vintage == "Any" or h["vintage"] == vintage]
        s_opts = sorted({h["stories"] for h in pool})
        if len(s_opts) > 1:
            s_sel = st.sidebar.selectbox("Stories", ["Any"] + [str(s) for s in s_opts])
            if s_sel != "Any":
                pool = [h for h in pool if h["stories"] == int(s_sel)]
        if not pool:
            st.title("Single-home NO₂")
            st.warning("No NIST homes match those filters — loosen the vintage or stories.")
            st.stop()
        amin = min(h["floor_area_ft2"] for h in pool)
        amax = max(h["floor_area_ft2"] for h in pool)
        area = st.sidebar.number_input(
            f"Total floor area (ft²) — {amin:,}–{amax:,} available",
            min_value=300, max_value=9000, value=int(np.clip(1500, amin, amax)), step=50)
        bracket = persily.bracket_by_area(pool, area)

    elif mode == "Browse homes":
        opts = sorted(manifest, key=lambda h: (h["type"], h["floor_area_ft2"]))
        di = next((i for i, h in enumerate(opts) if h["type"] == "DH"), 0)
        sel_entry = st.sidebar.selectbox("Home", opts, index=di, format_func=persily_label)

    elif mode == "Apartment building":
        blds = apartment_buildings()
        def _blabel(b):
            u = (f"{b['units_per_floor']} units/floor" if b["units_per_floor"] > 1
                 else "1 unit/floor")
            return f"{b['n_floors']}-storey · {u} ({b['id']})"
        apt_meta = st.sidebar.selectbox("Building", blds, format_func=_blabel)
        apt_model = load_apartment_model(apt_meta["rel_path"])
        fl = apartments.building_floors(apt_model)
        apt_floor_no = st.sidebar.selectbox(
            "Which floor do you live on?", [f for _, f in fl],
            index=min(len(fl) - 1, len(fl) // 2))
        apt_level = next(lid for lid, f in fl if f == apt_floor_no)
        _units = apartments.units_on_floor(apt_model, apt_level)
        apt_tag = (st.sidebar.selectbox("Which unit?", sorted(_units))
                   if len(_units) > 1 else (sorted(_units)[0] if _units else ""))

    else:  # Upload a .prj
        up = st.sidebar.file_uploader("Upload a CONTAM .prj", type=["prj"])
        if up is None:
            st.title("Single-home NO₂")
            st.info("Upload a CONTAM `.prj` to simulate a custom floorplan, or switch to "
                    "**Describe your home** / **Browse homes** in the sidebar.")
            st.stop()
        upload_model = parse_uploaded(up.name, up.getvalue().decode("latin-1", "ignore"))
        if not has_no2_source(upload_model):
            zopts = {f"{z.name} (#{z.id}, {z.volume:.0f} m³)": z.id
                     for z in sorted(upload_model.zones.values(), key=lambda z: -z.volume)}
            pick = st.sidebar.selectbox("Kitchen zone (no stove source in file)", list(zopts))
            upload_kitchen = zopts[pick]

    # --- shared scenario knobs (hours-based windows, cooking-amount slider) ---
    scenario = scenario_sidebar(prefix="sh_", window_style="hours",
                                cooking_style="slider")

    st.title("Single-home NO₂")

    # ----------- Describe your home: select the right floorplan + interpolate -----------
    if mode == "Describe your home":
        below, above, w = bracket
        res_b, kn_b, kit_b = run_persily(below, scenario)
        if above["id"] == below["id"]:
            t, kitchen, havg = res_b["t"], kit_b, home_avg_curve(res_b)
            rest = rest_avg_curve(res_b, kn_b)
            st.caption(
                f"Closest available {TYPE_NAMES[below['type']]} home: **{below['floor_area_ft2']:,} ft²** "
                f"({below['id']}); target is at the edge of the range, so no interpolation was needed. "
                "Fidelity for non-paper homes is physically faithful but unvalidated.")
        else:
            res_a, kn_a, kit_a = run_persily(above, scenario)
            t = res_b["t"]
            kitchen = (1 - w) * kit_b + w * kit_a
            havg = (1 - w) * home_avg_curve(res_b) + w * home_avg_curve(res_a)
            rest = ((1 - w) * rest_avg_curve(res_b, kn_b)
                    + w * rest_avg_curve(res_a, kn_a))
            st.caption(
                f"Interpolated between **{below['id']}** ({below['floor_area_ft2']:,} ft²) and "
                f"**{above['id']}** ({above['floor_area_ft2']:,} ft²) — {w:.0%} of the way to the larger. "
                "Fidelity for non-paper homes is physically faithful but unvalidated.")
        metrics_row(float(np.mean(kitchen[:144])), rolling_max_1h(kitchen),
                    float(np.mean(rest[:144])))

        st.subheader("NO₂ over 24 hours")
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=t, y=kitchen, mode="lines", name="kitchen (your home)",
                                 line=dict(width=3, color="#e8743b")))
        fig.add_trace(go.Scatter(x=t, y=havg, mode="lines", name="home average",
                                 line=dict(width=2, color="#4063d8")))
        fig.add_hline(y=EPA_1HR, line_dash="dash", line_color="#d6453d",
                      annotation_text="EPA/WHO 1-hr (100 ppb)", annotation_position="top right")
        fig.update_layout(xaxis_title="hour of day", yaxis_title="NO₂ (ppb)", height=430,
                          margin=dict(l=10, r=10, t=10, b=10),
                          legend=dict(orientation="h", yanchor="bottom", y=1.02),
                          xaxis=dict(tickvals=[0, 6, 12, 18, 24]))
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Engine runs the two NIST homes bracketing your floor area and blends the result. "
                   "NO₂ decay −2.4×10⁻⁴/s · interior doors + stairs mixed at 1000 m³/h · windows + "
                   "envelope leakage from the NIST geometry. — "
                   "Drafted by Claude with prompts engineered by Yannai Kashtan")

    # ----------- Apartment building: full-building stack, occupant's unit -----------
    elif mode == "Apartment building":
        kid, unit_zids = apartments.occupant_unit(apt_model, apt_level, apt_tag)
        if kid is None:
            st.warning("Couldn't identify a unit on that floor — try another floor/unit.")
            st.stop()
        with st.spinner(f"Solving the full {apt_meta['n_floors']}-storey building "
                        f"({len(apt_model.zones)} zones)…"):
            res = transport.simulate(apt_model, kitchen_zone=kid,
                                     **apply_window_spec(scenario, kid))
        zone_ids = res["zone_ids"]
        t = res["t"]
        kitchen = res["series"][:, zone_ids.index(kid)]
        st.caption(
            f"{apt_meta['id']} · full {apt_meta['n_floors']}-storey building "
            f"({len(apt_model.zones)} zones) · you're on floor {apt_floor_no}"
            + (f", unit {apt_tag}" if apt_tag else "")
            + ". Whole-building stack effect modeled through the stairwell; your unit is shown. "
            "Fidelity for non-paper homes is physically faithful but unvalidated.")
        rest_cols = [zone_ids.index(z) for z in unit_zids if z != kid]
        rest = (np.mean(res["series"][:, rest_cols], axis=1) if rest_cols
                else np.zeros_like(t))
        metrics_row(float(np.mean(kitchen[:144])), rolling_max_1h(kitchen),
                    float(np.mean(rest[:144])))

        st.subheader("NO₂ over 24 hours — your unit")
        fig = go.Figure()
        for zid in unit_zids:
            fig.add_trace(go.Scatter(x=t, y=res["series"][:, zone_ids.index(zid)],
                                     mode="lines", name=apt_model.zones[zid].name,
                                     line=dict(width=3 if zid == kid else 1.5)))
        fig.add_hline(y=EPA_1HR, line_dash="dash", line_color="#d6453d",
                      annotation_text="EPA/WHO 1-hr (100 ppb)", annotation_position="top right")
        fig.update_layout(xaxis_title="hour of day", yaxis_title="NO₂ (ppb)", height=430,
                          margin=dict(l=10, r=10, t=10, b=10),
                          legend=dict(orientation="h", yanchor="bottom", y=1.02),
                          xaxis=dict(tickvals=[0, 6, 12, 18, 24]))
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Whole-building multizone solve — the stack effect acts through the stairwell over "
                   "the building height, so upper and lower floors ventilate differently. "
                   "'Air exchange' is building-wide. Tall high-rises (>200 zones) aren't available yet. — "
                   "Drafted by Claude with prompts engineered by Yannai Kashtan")

    # ----------- Browse / Upload: one home, full per-room detail -----------
    else:
        if mode == "Browse homes":
            model = load_persily_model(sel_entry["rel_path"])
            res, kname, kitchen = run_persily(sel_entry, scenario)
            home_title = persily_label(sel_entry)
        else:
            model = upload_model
            kzid = upload_kitchen
            if kzid is None:            # file has its own NO2 source: that zone
                for s in model.sources:
                    el = model.source_elements.get(s.element)
                    if el and el.species == "NO2" and "ov" not in el.name:
                        kzid = s.zone
                        break
            res = transport.simulate(model, kitchen_zone=upload_kitchen,
                                     **apply_window_spec(scenario, kzid))
            kname = (model.zones[upload_kitchen].name if upload_kitchen is not None
                     else kitchen_zone_name(model))
            kitchen = res["by_zone"].get(kname, np.zeros_like(res["t"]))
            home_title = f"Custom: {len(model.zones)} zones"
        t = res["t"]
        st.caption(f"{home_title} · {len(model.paths)} flow paths · "
                   "first-principles airflow + transport (not a lookup).")
        davg = res["summary"][kname]["dayavg"] if kname in res["summary"] else float(np.mean(kitchen))
        rest = rest_avg_curve(res, kname)
        metrics_row(davg, rolling_max_1h(kitchen), float(np.mean(rest[:144])))

        st.subheader("NO₂ concentration over 24 hours")
        ranked = sorted(res["summary"].items(), key=lambda kv: -kv[1]["peak"])
        show = [n for n, s in ranked if s["peak"] > 0.5][:6]
        if kname and kname not in show:
            show = [kname] + show
        fig = go.Figure()
        for n in show:
            fig.add_trace(go.Scatter(x=t, y=res["by_zone"][n], mode="lines", name=n,
                                     line=dict(width=3 if n == kname else 1.6)))
        fig.add_hline(y=EPA_1HR, line_dash="dash", line_color="#d6453d",
                      annotation_text="EPA/WHO 1-hr (100 ppb)", annotation_position="top right")
        fig.update_layout(xaxis_title="hour of day", yaxis_title="NO₂ (ppb)", height=430,
                          margin=dict(l=10, r=10, t=10, b=10),
                          legend=dict(orientation="h", yanchor="bottom", y=1.02),
                          xaxis=dict(tickvals=[0, 6, 12, 18, 24]))
        st.plotly_chart(fig, use_container_width=True)

        st.subheader("By room")
        st.dataframe([{"Room": n, "Peak (ppb)": round(s["peak"], 1),
                       "Max 1-hr (ppb)": round(rolling_max_1h(res["by_zone"][n]), 1),
                       "Daily avg (ppb)": round(s["dayavg"], 2)} for n, s in ranked],
                     use_container_width=True, hide_index=True)
        st.caption("NO₂ decay −2.4×10⁻⁴/s · interior doors + stairs mixed at 1000 m³/h · "
                   "benchmarks WHO/EPA 1-hr ≈ 100 ppb, WHO annual ≈ 5.3 ppb.")


# ================= POPULATION HEALTH & POLICY (jurisdiction CBA) =================
elif panel == "Population & health":
    # ---- jurisdiction sets the STRUCTURAL + location context ----
    codes = juris.list_jurisdictions()
    jcode = st.sidebar.selectbox("Jurisdiction", codes,
                                 format_func=juris.display_name, key="j_code")
    prof = juris.profile(jcode)
    base_gas = prof["gas_pct"]
    gas_default = int(round(base_gas))   # slider start; baseline aligns to it
    base_no2 = prof["outdoor_no2_ppb"]
    # Jurisdiction's effective-range-hood baseline (vintage proxy; US = paper's 22%)
    BASE_HOOD_PCT = int(round(prof.get("hood_pct_default", 22)))

    # Per-jurisdiction widget keys: Streamlit keeps a keyed slider's value across
    # reruns (frontend included), so a shared key would keep DISPLAYING the prior
    # jurisdiction's number even after `value=` changes. A jcode-scoped key mounts
    # a fresh slider per jurisdiction → it initializes to that jurisdiction's
    # baseline on first visit (and remembers any policy you set there on return).
    kg, kh, kn = f"j_gas_{jcode}", f"j_hood_{jcode}", f"j_no2_{jcode}"

    st.sidebar.subheader("Policy levers")
    st.sidebar.caption(f"Defaults are **{juris.display_name(jcode)}**'s current conditions. "
                       "Move a lever to model a policy; the headline shows the change from "
                       "that baseline.")
    gas_pct = st.sidebar.slider("Gas/propane cooking prevalence (%)", 0, 100,
                                gas_default, key=kg)
    hood_pct = st.sidebar.slider(
        "Homes with an effective range hood (vented + regularly used) (%)",
        0, 100, BASE_HOOD_PCT, key=kh,
        help="Share of homes whose range hood is vented outdoors AND actually used "
             "during cooking (so it removes NO₂). A ventilation standard raises this. "
             "Baseline is a housing-vintage proxy — new homes are far likelier to have "
             "a vented hood; no survey measures this at state level.")
    out_no2 = st.sidebar.slider("Outdoor NO₂ (ppb)", 0.0, 40.0, float(round(base_no2, 1)),
                                0.5, key=kn,
                                help="Ambient NO₂ from traffic/outdoor sources. Affects "
                                     "total exposure shown; the stove-attributable health "
                                     "burden below is driven by the prevalence and "
                                     "ventilation levers.")
    with st.sidebar.expander("Cost & valuation assumptions"):
        vsl_m = st.slider("Value of statistical life ($M)", 1.0, 20.0,
                          constants.VSL_USD / 1e6, 0.1, key="j_vsl")
        asthma_cost = st.number_input("Asthma cost ($/case·yr)", 500, 50000,
                                      int(constants.ASTHMA_COST_USD_PER_CASE_YR), 100,
                                      key="j_ac")
    st.sidebar.caption("Cooking, window, and occupancy behavior are held at the source "
                       "paper's national distributions; housing stock, climate, and wind "
                       "come from the jurisdiction.")

    # ---- exposure + burden: baseline (jurisdiction) vs policy (levers) ----
    hw, temps, wind = prof["house_weights"], prof["temps"], prof["wind"]

    @st.cache_data(show_spinner=False)
    def _pop_mean(code, hood_pct):
        return population.population_mean_exposure(
            house_w=hw, temps=temps, wind=wind,
            hood=population.hood_dist(hood_pct / 100))

    @st.cache_data(show_spinner=False)
    def _pop_dist(code, hood_pct):
        return population.exposure_distribution(
            house_w=hw, temps=temps, wind=wind,
            hood=population.hood_dist(hood_pct / 100))

    @st.cache_data(show_spinner=False)
    def _pen(code):
        return population.population_mean_penetration(house_w=hw, temps=temps, wind=wind)

    mean_base = _pop_mean(jcode, BASE_HOOD_PCT)
    mean_pol = _pop_mean(jcode, hood_pct)
    pen = _pen(jcode)

    hh_base = juris.gas_households(jcode, gas_default / 100)
    hh_pol = juris.gas_households(jcode, gas_pct / 100)
    cost_kw = dict(vsl_usd=vsl_m * 1e6, asthma_cost=asthma_cost)
    b_base = population.jurisdiction_burden(mean_base, hh_base, **cost_kw)
    b_pol = population.jurisdiction_burden(mean_pol, hh_pol, **cost_kw)

    d_deaths = b_base["deaths"] - b_pol["deaths"]
    d_asthma = b_base["asthma_cases"] - b_pol["asthma_cases"]
    d_cost = b_base["cost_usd"] - b_pol["cost_usd"]
    changed = (gas_pct != gas_default) or (hood_pct != BASE_HOOD_PCT)

    st.title(f"Population health & policy — {juris.display_name(jcode)}")
    hp = prof["housing_pct"]
    st.caption(
        f"**{prof['population']:,}** people · **{hh_base:,.0f}** gas-cooking households · "
        f"gas/propane **{base_gas:.0f}%** · effective range hood **{BASE_HOOD_PCT}%** · "
        f"outdoor NO₂ **{base_no2:.1f} ppb** · housing "
        f"{hp['Detached']:.0f}% detached / {hp['Multifamily']:.0f}% multifamily / "
        f"{hp['Manufactured']:.0f}% manufactured. Structural + location context from the "
        "ZIP-level housing/climate data; behavior from the source paper.")

    # ---- headline: what the policy buys (asthma leads) ----
    st.subheader("What this policy avoids, per year")
    if not changed:
        st.info("Move the **gas prevalence** or **ventilation** lever in the sidebar to "
                "model a policy. The figures below then show the annual burden it avoids "
                "versus the jurisdiction's current conditions.")
    # Baseline→policy shown as neutral gray captions, not st.metric deltas:
    # a descriptive delta string always renders green ("up=good") regardless of
    # direction, which misleads. The avoided value (and its sign) carries the
    # direction; a negative "avoided" means the policy adds burden.
    a1, a2 = st.columns(2)
    a1.metric("Pediatric asthma cases avoided", f"{d_asthma:,.0f}/yr")
    a1.caption(f"baseline {b_base['asthma_cases']:,.0f} → {b_pol['asthma_cases']:,.0f} under policy")
    a2.metric("Adult deaths avoided", f"{d_deaths:,.0f}/yr")
    a2.caption(f"baseline {b_base['deaths']:,.0f} → {b_pol['deaths']:,.0f} under policy")

    st.subheader("Health cost avoided, per year")
    d_acost = b_base["asthma_cost_usd"] - b_pol["asthma_cost_usd"]
    d_mcost = b_base["mortality_cost_usd"] - b_pol["mortality_cost_usd"]
    c1, c2 = st.columns(2)
    c1.metric("Asthma (morbidity)", f"{_usd(d_acost)}/yr")
    c1.caption(f"{_usd(b_base['asthma_cost_usd'])} → {_usd(b_pol['asthma_cost_usd'])}")
    c2.metric("Mortality (VSL)", f"{_usd(d_mcost)}/yr")
    c2.caption(f"{_usd(b_base['mortality_cost_usd'])} → {_usd(b_pol['mortality_cost_usd'])}")
    st.caption("Mortality valuation (deaths × VSL) dwarfs asthma morbidity cost by ~1000× "
               "and carries the larger uncertainty — the split keeps the more-defensible "
               "asthma outcome legible.")

    st.subheader("Per gas-cooking household, per year")
    p1, p2, p3 = st.columns(3)
    p1.metric("Asthma (morbidity)", f"${b_pol['asthma_cost_per_home']:,.0f}/home")
    p1.caption(f"baseline ${b_base['asthma_cost_per_home']:,.0f} → "
               f"${b_pol['asthma_cost_per_home']:,.0f}")
    p2.metric("Mortality (VSL)", f"${b_pol['mortality_cost_per_home']:,.0f}/home")
    p2.caption(f"baseline ${b_base['mortality_cost_per_home']:,.0f} → "
               f"${b_pol['mortality_cost_per_home']:,.0f}")
    total_ltn2_base = mean_base + base_no2 * pen
    total_ltn2_pol = mean_pol + out_no2 * pen
    dtot = total_ltn2_pol - total_ltn2_base
    # Directional delta (inverse-colored, green = lower), shown only when moved.
    p3.metric("Total long-term NO₂ (gas homes)", f"{total_ltn2_pol:.1f} ppb",
              delta=(f"{dtot:+.1f} ppb vs baseline" if abs(dtot) >= 0.05 else None),
              delta_color="inverse",
              help="Stove-attributable + outdoor penetration. Compared to WHO annual "
                   f"{WHO_ANNUAL:.1f} ppb.")

    # ---- exposure spread across the housing stock (baseline vs policy) ----
    st.subheader("Exposure spread across homes")
    vb, wb = _pop_dist(jcode, BASE_HOOD_PCT)
    vp, wp = _pop_dist(jcode, hood_pct)
    edges = np.linspace(0, float(max(vb.max(), vp.max())) * 1.02, 45)
    hb, _ = np.histogram(vb, bins=edges, weights=wb)
    hp_, _ = np.histogram(vp, bins=edges, weights=wp)
    ctr = (edges[:-1] + edges[1:]) / 2
    vent_changed = hood_pct != BASE_HOOD_PCT     # only ventilation shifts the spread
    figd = go.Figure()
    figd.add_trace(go.Bar(x=ctr, y=hb / hb.sum() * 100, name="baseline",
                          marker_color="#9aa0a6", opacity=0.75))
    if vent_changed:
        figd.add_trace(go.Bar(x=ctr, y=hp_ / hp_.sum() * 100, name="under policy",
                              marker_color="#4063d8", opacity=0.6))
    figd.add_vline(x=mean_base, line_dash="dot", line_color="#5f6368",
                   annotation_text=f"baseline mean {mean_base:.1f}",
                   annotation_position="top left", annotation_yshift=10)
    if changed and abs(mean_pol - mean_base) > 0.05:   # ventilation shifted the spread
        figd.add_vline(x=mean_pol, line_dash="dash", line_color="#4063d8",
                       annotation_text=f"policy mean {mean_pol:.1f}",
                       annotation_position="bottom right")
    figd.add_vline(x=WHO_ANNUAL, line_color="#2f9e57",
                   annotation_text=f"WHO annual {WHO_ANNUAL:.1f}",
                   annotation_position="top right", annotation_yshift=10)
    figd.update_layout(barmode="overlay", xaxis_title="long-term stove NO₂ (ppb)",
                       yaxis_title="% of gas-cooking homes", height=340,
                       margin=dict(l=10, r=10, t=10, b=10),
                       legend=dict(orientation="h", yanchor="bottom", y=1.02))
    st.plotly_chart(figd, use_container_width=True)
    st.caption(
        "Distribution of long-term stove NO₂ across the jurisdiction's gas-cooking homes "
        "(reweighting the exact 86,400-scenario CONTAM library by the local housing mix, "
        "climate, and wind). The spread — not just the mean — shows the fraction of homes "
        "well above guideline. Burden = the papers' national estimates (gas-cooking asthma "
        "OR 1.32, Lin et al. 2013; mortality RR 1.02/10 µg·m⁻³, Atkinson et al.) scaled by "
        f"gas-household count × mean exposure; VSL ${vsl_m:.1f}M, asthma ${asthma_cost:,}/case. "
        "Stove-attributable; outdoor NO₂ sets total-exposure context only. State housing "
        "sub-types are reconstructed from ZIP marginals (type split exact). Effective-hood "
        "baseline is a housing-vintage proxy (LBNL: ~76–80% of post-2003 homes have a "
        "vented hood, ~30% used effectively; no state-level survey exists), scaled so the "
        "US average matches the paper's 22%. — "
        "Drafted by Claude with prompts engineered by Yannai Kashtan")


# ============================ DIAGNOSTICS ============================
else:
    # ---- home picker: 24 paper archetypes first, then the NIST library ----
    papers = dg.paper_homes()
    paper_opts = sorted(papers, key=lambda h: (papers[h]["type"],
                                               papers[h]["floor_area_ft2"]))
    nist = sorted(persily_homes(), key=lambda h: (h["type"], h["floor_area_ft2"]))
    options = [("paper", h) for h in paper_opts] + [("nist", h["id"]) for h in nist]
    nist_by_id = {h["id"]: h for h in nist}

    def _dg_label(opt):
        kind, hid = opt
        if kind == "paper":
            a = papers[hid]
            return f"{hid} · paper · {a['floor_area_ft2']:,} ft²"
        h = nist_by_id[hid]
        return f"{hid} · NIST · {h['floor_area_ft2']:,} ft²"

    default_i = options.index(("paper", "DH-29"))
    kind, home_id = st.sidebar.selectbox("Home", options, index=default_i,
                                         format_func=_dg_label, key="dg_home")
    if kind == "paper":
        model = load_model(home_id)
        arch = papers[home_id]
        kz_sim = None                       # paper homes carry their own sources
    else:
        model = load_persily_model(nist_by_id[home_id]["rel_path"])
        arch = None
        kz_sim = transform.kitchen_zone_id(model)
    roles = dg.zone_roles(model, arch)

    scenario = scenario_sidebar(prefix="dg_", no2_default=0.0)
    st.sidebar.caption("Outdoor NO₂ defaults to 0 here to isolate stove physics "
                       "(the library convention).")

    # --- window detail: all-windows scalar / per-room / scheduled ---
    wmode = st.sidebar.radio("Window mode",
                             ["All windows (slider above)", "Per room", "Scheduled"],
                             key="dg_wmode")
    window_rooms = sorted({
        (p.n_to if p.n_from == -1 else p.n_from)
        for p in model.paths
        if (el := model.elements.get(p.element)) is not None
        and el.type_code == 27 and (p.n_from == -1 or p.n_to == -1)})
    if wmode == "Per room":
        per = {}
        for zid in window_rooms:
            per[zid] = st.sidebar.slider(
                f"{model.zones[zid].name} window", 0.0, 1.0, 0.0, 0.05,
                key=f"dg_wroom_{home_id}_{zid}")
        scenario["window_open"] = per
    elif wmode == "Scheduled":
        base = scenario["window_open"]
        s_open = st.sidebar.slider("Opening while scheduled", 0.0, 1.0, 0.7, 0.05,
                                   key="dg_ws_open")
        s_start = st.sidebar.slider("Opens at (hour)", 0.0, 23.0, 18.0, 0.5,
                                    key="dg_ws_start")
        s_hours = st.sidebar.slider("Stays open (hours)", 0.5, 12.0, 4.0, 0.5,
                                    key="dg_ws_hours")
        scenario["window_schedule"] = [
            {"start": s_start, "hours": s_hours, "open": s_open}]
        st.sidebar.caption(f"Base opening {base:g} outside the scheduled interval. "
                           "Flow metrics (kitchen exchange, boundary tab) use the "
                           "base state.")
    scen_key = json.dumps(scenario, sort_keys=True)   # hashable cache key

    # ---- cached computation wrappers (model objects aren't hashable) ----
    @st.cache_data(show_spinner=False)
    def c_sweep(home, skey, knob, _m, _r, _sc, _kz):
        return dg.sweep(_m, _r, _sc, knob, kitchen_zone=_kz)

    @st.cache_data(show_spinner=False)
    def c_tornado(home, skey, _m, _r, _sc, _kz):
        df, base = dg.tornado(_m, _r, _sc, kitchen_zone=_kz)
        return df, base

    @st.cache_data(show_spinner=False)
    def c_case(home, skey, _m, _r, _sc, _kz):
        return dg.run_case(_m, _r, _sc, kitchen_zone=_kz,
                           internals=True, include_res=True)

    @st.cache_data(show_spinner=False)
    def c_box(home, skey, _m, _r, _sc, _kz):
        return dg.box_model_check(_m, _r, _sc, kitchen_zone=_kz)

    @st.cache_data(show_spinner=False)
    def c_scaling(home, _m):
        return dg.scaling_laws(_m)

    @st.cache_data(show_spinner=False)
    def c_convergence(home, _m):
        return dg.convergence_scan(_m)

    @st.cache_data(show_spinner=False)
    def c_axis(house, axis, metric, window_state):
        return dg.axis_response(house, axis, metric,
                                overrides={"window": window_state}
                                if axis != "window" else None)

    @st.cache_data(show_spinner=True)
    def c_scatter(house, n, seed):
        return dg.scatter_sample(house, n=n, seed=seed)

    st.title("Engine diagnostics")
    st.caption(f"{home_id} · {len(model.zones)} zones · kitchen = "
               f"{model.zones[roles['kitchen']].name}"
               + (f" · bedroom = {model.zones[roles['bedroom']].name}"
                  if roles["bedroom"] else "")
               + " · all sweeps/checks run the live engine at the sidebar conditions.")
    if getattr(model, "mixing_added", 0):
        st.info(f"Uniform-mixing policy: this home's shipped .prj was missing fan "
                f"mixing on {model.mixing_added} interior doorway(s) — the standard "
                "2,000 m³/h exchange was added at load. Library ground truth was "
                "computed WITHOUT it, so engine-vs-library comparisons for this home "
                "will diverge by design.")

    tab_sw, tab_ck, tab_lib, tab_af = st.tabs(
        ["Sensitivity sweeps", "Physics self-checks", "vs. CONTAM library",
         "Airflow internals"])

    # ----------------------------- A. sweeps -----------------------------
    with tab_sw:
        knob = st.selectbox("Sweep knob", list(dg.SWEEPS),
                            format_func=lambda k: dg.SWEEPS[k][0])
        with st.spinner("Sweeping…"):
            df = c_sweep(home_id, scen_key, knob, model, roles, scenario, kz_sim)
        bad = df[~df["solver_converged"]]

        CONC = [("kitchen_peak", "kitchen peak"), ("kitchen_max1h", "kitchen max 1-hr"),
                ("kitchen_dayavg", "kitchen day-avg"), ("bedroom_dayavg", "bedroom day-avg"),
                ("homeavg_dayavg", "home-avg day-avg")]
        logy = st.checkbox("log concentration axis", value=False)
        fig = go.Figure()
        for col, name in CONC:
            fig.add_trace(go.Scatter(x=df[knob], y=df[col], mode="lines+markers", name=name))
        fig.update_layout(xaxis_title=dg.SWEEPS[knob][0], yaxis_title="NO₂ (ppb)",
                          height=380, margin=dict(l=10, r=10, t=30, b=10),
                          yaxis_type="log" if logy else "linear",
                          legend=dict(orientation="h", yanchor="bottom", y=1.02))
        st.plotly_chart(fig, use_container_width=True)

        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(x=df[knob], y=df["living_ach"],
                                  mode="lines+markers", name="living-space ACH (/h)"))
        fig2.add_trace(go.Scatter(x=df[knob], y=df["kitchen_outdoor_ach"],
                                  mode="lines+markers", name="kitchen outdoor ACH (/h)"))
        fig2.add_trace(go.Scatter(x=df[knob], y=df["kitchen_exchange_m3h"],
                                  mode="lines+markers", name="kitchen↔rest exchange (m³/h)",
                                  yaxis="y2", line=dict(dash="dot")))
        if len(bad):
            fig2.add_trace(go.Scatter(x=bad[knob], y=bad["living_ach"], mode="markers",
                                      name="airflow NOT converged",
                                      marker=dict(symbol="x", size=11, color="#d6453d")))
        fig2.update_layout(xaxis_title=dg.SWEEPS[knob][0],
                           yaxis=dict(title="living-space air exchange (/h)"),
                           yaxis2=dict(title="exchange (m³/h)", overlaying="y", side="right"),
                           height=340, margin=dict(l=10, r=10, t=30, b=10),
                           legend=dict(orientation="h", yanchor="bottom", y=1.02))
        st.plotly_chart(fig2, use_container_width=True)
        if len(bad):
            st.warning(f"{len(bad)}/{len(df)} sweep points: the airflow Newton solve hit "
                       "the iteration cap without converging (see Self-checks → solver "
                       "health). Concentrations at those points inherit the bias.")

        st.subheader("Concentration vs. air exchange (parametric)")
        fig3 = go.Figure()
        fig3.add_trace(go.Scatter(
            x=df["living_ach"], y=df["kitchen_dayavg"], mode="markers+lines",
            name="kitchen day-avg", text=[f"{knob}={v:g}" for v in df[knob]],
            marker=dict(size=8, color=df[knob], colorscale="Viridis", showscale=True,
                        colorbar=dict(title=knob))))
        fig3.add_trace(go.Scatter(x=df["living_ach"], y=df["bedroom_dayavg"],
                                  mode="markers+lines", name="bedroom day-avg",
                                  line=dict(dash="dot")))
        fig3.update_layout(xaxis_title="living-space ACH (/h)", yaxis_title="NO₂ (ppb)",
                           height=340, margin=dict(l=10, r=10, t=30, b=10),
                           legend=dict(orientation="h", yanchor="bottom", y=1.02))
        st.plotly_chart(fig3, use_container_width=True)
        st.caption("Air exchange is EMERGENT (leakage + stack + wind + windows), so this "
                   "traces C(ACH) as the sweep knob moves it — different knobs trace "
                   "different curves. Living-space ACH counts outdoor air delivered "
                   "directly to conditioned rooms (attic/crawl cross-flow excluded). "
                   "Kitchen↔rest exchange = volumetric inflow to the kitchen from other "
                   "rooms (doorway fans + interior leaks), fans-off base-window regime.")

        st.subheader("Tornado — one step on every knob")
        with st.spinner("Perturbing each knob…"):
            tdf, base_m = c_tornado(home_id, scen_key, model, roles, scenario, kz_sim)
        figt = go.Figure()
        for col, color in (("kitchen_dayavg", "#e8743b"), ("bedroom_dayavg", "#4063d8"),
                           ("living_ach", "#2f9e57")):
            figt.add_trace(go.Bar(y=tdf["perturbation"], x=tdf[col], orientation="h",
                                  name=col.replace("_", " "), marker_color=color))
        figt.update_layout(xaxis_title="% change from base", barmode="group", height=380,
                           margin=dict(l=10, r=10, t=30, b=10),
                           legend=dict(orientation="h", yanchor="bottom", y=1.02))
        st.plotly_chart(figt, use_container_width=True)
        st.caption(f"Base point: kitchen day-avg {base_m['kitchen_dayavg']:.1f} ppb · "
                   f"bedroom {base_m['bedroom_dayavg']:.1f} ppb · "
                   f"living-space ACH {base_m['living_ach']:.2f}/h · "
                   f"kitchen↔rest {base_m['kitchen_exchange_m3h']:,.0f} m³/h.")

    # -------------------------- B. self-checks --------------------------
    with tab_ck:
        with st.spinner("Running base case with internals…"):
            case = c_case(home_id, scen_key, model, roles, scenario, kz_sim)
        res = case["res"]

        st.subheader("Mass-balance closure (24 h)")
        mb = dg.mass_balance(res)
        ok = abs(mb["residual_pct"]) < 1e-6
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Emitted", f"{mb['emitted_g']:.3f} g")
        c2.metric("Decayed", f"{mb['decayed_g']:.3f} g")
        c3.metric("Exfiltrated", f"{mb['exfiltrated_g']:.3f} g")
        c4.metric("Δ storage", f"{mb['storage_g']:.4f} g")
        c5.metric("Residual", f"{mb['residual_pct']:.1e} %",
                  delta="closes" if ok else "FAILS", delta_color="normal" if ok else "inverse")
        st.caption("Exact bookkeeping via the solver's own matrix-exponential step "
                   "integrals — residual at float precision means the transport "
                   "integration is internally exact (this is not a discretized estimate).")

        st.subheader("Post-dinner decay tail vs. system modes")
        ddf, refs = dg.decay_fit(res, roles)
        cA, cB = st.columns([1, 1])
        with cA:
            st.dataframe(ddf.round(4), use_container_width=True, hide_index=True)
        with cB:
            st.metric("Slowest system mode", f"{refs['slowest_mode_per_h']:.3f} /h")
            st.metric("Naive living-space ACH + decay", f"{refs['ach_plus_decay_per_h']:.3f} /h")
        st.caption("Fitted on ln(C − overnight floor), hours 20–24. All rooms should relax "
                   "at the slowest eigenvalue of the fans-off transport matrix — the same "
                   "analysis as a field tracer-decay experiment. The naive ACH+k rate "
                   "differs legitimately: the home is not one well-mixed zone.")

        st.subheader("Well-mixed (box-model) limit")
        bm = c_box(home_id, scen_key, model, roles, scenario, kz_sim)
        okb = abs(bm["ratio_dayavg"] - 1) < 0.05
        cb1, cb2 = st.columns([2, 1])
        with cb1:
            figb = go.Figure()
            figb.add_trace(go.Scatter(x=bm["t"], y=bm["engine"], mode="lines",
                                      name=f"engine @ mixing×{bm['mixing']:g}"))
            figb.add_trace(go.Scatter(x=bm["t"], y=bm["box"], mode="lines",
                                      name="analytic single box", line=dict(dash="dash")))
            figb.update_layout(xaxis_title="hour", yaxis_title="NO₂ (ppb)", height=300,
                               margin=dict(l=10, r=10, t=30, b=10),
                               legend=dict(orientation="h", yanchor="bottom", y=1.02))
            st.plotly_chart(figb, use_container_width=True)
        with cb2:
            st.metric("engine / box (day-avg)", f"{bm['ratio_dayavg']:.4f}",
                      delta="within 5%" if okb else "outside 5%",
                      delta_color="normal" if okb else "inverse")
            st.caption(f"{bm['n_component']}/{bm['n_zones']} zones fan-connected "
                       f"({bm['volume_fraction']:.0%} of volume). Outdoor NO₂ forced to 0 "
                       "(a box can't represent the indirect basement/attic pathway).")

        st.subheader("Power-law scaling")
        sl = c_scaling(home_id, model)
        cs1, cs2 = st.columns(2)
        with cs1:
            figs = go.Figure()
            figs.add_trace(go.Scatter(x=sl["dT"], y=sl["ach_dT"], mode="markers", name="ACH"))
            figs.add_trace(go.Scatter(
                x=sl["dT"], y=np.exp(np.polyval(np.polyfit(np.log(sl["dT"]),
                                                           np.log(sl["ach_dT"]), 1),
                                                np.log(sl["dT"]))),
                mode="lines", name=f"fit: slope {sl['slope_dT']:.2f}"))
            figs.update_layout(xaxis_type="log", yaxis_type="log", height=300,
                               xaxis_title="ΔT (K), wind 0", yaxis_title="ACH (/h)",
                               margin=dict(l=10, r=10, t=30, b=10),
                               legend=dict(orientation="h", yanchor="bottom", y=1.02))
            st.plotly_chart(figs, use_container_width=True)
        with cs2:
            figw = go.Figure()
            figw.add_trace(go.Scatter(x=sl["v"], y=sl["ach_v"], mode="markers", name="ACH"))
            figw.add_trace(go.Scatter(
                x=sl["v"], y=np.exp(np.polyval(np.polyfit(np.log(sl["v"]),
                                                          np.log(sl["ach_v"]), 1),
                                               np.log(sl["v"]))),
                mode="lines", name=f"fit: slope {sl['slope_v']:.2f}"))
            figw.update_layout(xaxis_type="log", yaxis_type="log", height=300,
                               xaxis_title="wind (m/s), ΔT 0", yaxis_title="ACH (/h)",
                               margin=dict(l=10, r=10, t=30, b=10),
                               legend=dict(orientation="h", yanchor="bottom", y=1.02))
            st.plotly_chart(figw, use_container_width=True)
        st.caption(f"Leak exponents n ∈ [{sl['n_min']:.2f}, {sl['n_max']:.2f}] "
                   f"(C-weighted mean {sl['n_mean']:.2f}). Stack slope should fall in that "
                   f"range (got {sl['slope_dT']:.2f}); wind slope ≈ 2n (got {sl['slope_v']:.2f}). "
                   "Computed with fully-converged solves — physics check, not solver check.")

        st.subheader("Airflow solver health")
        sv = case["afr"]["solver"]
        ch1, ch2, ch3 = st.columns(3)
        ch1.metric("Iterations (base case)", f"{sv['iterations']}/{sv['max_iter']}",
                   delta="converged" if sv["converged"] else "NOT converged",
                   delta_color="normal" if sv["converged"] else "inverse")
        ch2.metric("Last Newton step", f"{sv['last_step_Pa']:.1e} Pa")
        ch3.metric("Mass residual (rel.)", f"{sv['mass_residual_rel']:.1e}")
        with st.spinner("Scanning the temp × wind × window grid…"):
            cs = c_convergence(home_id, model)
        flagged = cs[cs["ratio"] < 0.98]
        if len(flagged):
            st.error(f"{len(flagged)}/{len(cs)} grid cells return a NON-converged airflow "
                     "solution at the production iteration cap (undamped Newton, "
                     "max_iter=100). ACH there is biased low → concentrations biased high.")
            st.dataframe(flagged.round(3), use_container_width=True, hide_index=True)
        else:
            st.success(f"All {len(cs)} temp × wind × window grid cells converge at the "
                       "production solver settings for this home.")
        with st.expander("Full convergence grid"):
            st.dataframe(cs.round(3), use_container_width=True, hide_index=True)

    # ------------------------ C. vs. CONTAM library ------------------------
    with tab_lib:
        if not dg.have_library_truth():
            st.warning("Library ground truth needs the original data folders "
                       "(Occupancy schedules + house inputs) — available on this machine "
                       "only if the CONTAM_SCALEUP project is present.")
        elif kind != "paper":
            st.info("Pick one of the 24 paper homes in the sidebar — the 86,400-scenario "
                    "library only covers those.")
        else:
            cl1, cl2, cl3 = st.columns(3)
            axis = cl1.selectbox("Vary axis", list(dg.AXES))
            metric = cl2.selectbox("Metric", ["dayavg", "hravg", "eighthravg", "peak"])
            wstate = cl3.selectbox("Windows held at", ["closed", "moderate", "open"],
                                   disabled=(axis == "window"))
            with st.spinner("Engine runs along the axis…"):
                ar = c_axis(home_id, axis, metric, wstate)
            gm = dg.gm_ratio(ar["engine"], ar["library"])
            figl = go.Figure()
            figl.add_trace(go.Scatter(x=ar["level"], y=ar["engine"],
                                      mode="lines+markers", name="engine"))
            figl.add_trace(go.Scatter(x=ar["level"], y=ar["library"],
                                      mode="lines+markers", name="library (exact CONTAM)"))
            figl.update_layout(xaxis_title=axis, yaxis_title=f"{metric} (ppb, occupancy-weighted)",
                               height=360, margin=dict(l=10, r=10, t=30, b=10),
                               legend=dict(orientation="h", yanchor="bottom", y=1.02))
            st.plotly_chart(figl, use_container_width=True)
            st.caption(f"Other axes at reference (NoHood · med use · {wstate} windows · "
                       f"COOL · BREEZE · median occupancy). GM engine/library ratio on this "
                       f"axis: {gm:.2f}. Absolute offsets reflect the documented per-type "
                       "bias; the SHAPE mismatch along an axis is the physics signal.")

            with st.expander("Scatter over a random scenario sample"):
                n = st.slider("Sample size", 30, 300, 100, 10)
                if st.button("Run sample"):
                    sc_df = c_scatter(home_id, n, 0)
                    x = sc_df[f"library_{metric}"]; y = sc_df[f"engine_{metric}"]
                    gm2 = dg.gm_ratio(y, x)
                    figsc = go.Figure()
                    figsc.add_trace(go.Scatter(x=x, y=y, mode="markers",
                                               marker=dict(size=6, opacity=0.65,
                                                           color=sc_df["window"].map(
                                                               {"closed": "#4063d8",
                                                                "moderate": "#2f9e57",
                                                                "open": "#e8743b"})),
                                               text=[f"{r.hood}/{r.use}/{r.window}/{r.temp}/"
                                                     f"{r.wind}/{r.oc}"
                                                     for r in sc_df.itertuples()],
                                               name="scenarios"))
                    lim = [max(0.05, min(x.min(), y.min()) * 0.8),
                           max(x.max(), y.max()) * 1.2]
                    figsc.add_trace(go.Scatter(x=lim, y=lim, mode="lines", name="1:1",
                                               line=dict(dash="dash", color="#888")))
                    figsc.update_layout(xaxis_type="log", yaxis_type="log", height=430,
                                        xaxis_title=f"library {metric} (ppb)",
                                        yaxis_title=f"engine {metric} (ppb)",
                                        margin=dict(l=10, r=10, t=30, b=10))
                    st.plotly_chart(figsc, use_container_width=True)
                    st.caption(f"n={len(sc_df)} · GM engine/library = {gm2:.2f} · colors = "
                               "window state (blue closed, green moderate, orange open).")

    # ------------------------- D. airflow internals -------------------------
    with tab_af:
        case = c_case(home_id, scen_key, model, roles, scenario, kz_sim)
        afr, res = case["afr"], case["res"]
        kb = case["boundary"]
        cm1, cm2, cm3 = st.columns(3)
        cm1.metric("Kitchen ↔ rest of home", f"{kb['interzone_m3h']:,.0f} m³/h")
        cm2.metric("Kitchen ↔ outdoors", f"{kb['outdoor_m3h']:,.1f} m³/h")
        cm3.metric("Kitchen turnover", f"{kb['turnover_ach']:.1f} /h")

        st.subheader("Kitchen boundary flows")
        rows = sorted(kb["rows"], key=lambda r: -(r["q_in"] + r["q_out"]))
        figk = go.Figure()
        labels = [f"{r['counterpart']} ({r['kind']})" for r in rows]
        figk.add_trace(go.Bar(y=labels, x=[r["q_in"] for r in rows], orientation="h",
                              name="into kitchen", marker_color="#4063d8"))
        figk.add_trace(go.Bar(y=labels, x=[-r["q_out"] for r in rows], orientation="h",
                              name="out of kitchen", marker_color="#e8743b"))
        figk.update_layout(barmode="relative", xaxis_title="m³/h",
                           height=max(260, 34 * len(rows)),
                           margin=dict(l=10, r=10, t=30, b=10),
                           legend=dict(orientation="h", yanchor="bottom", y=1.02))
        st.plotly_chart(figk, use_container_width=True)
        st.caption("Fans-off (overnight) regime at the sidebar conditions. Doorway mixing "
                   "fans exchange equal volumes both ways; power-law paths are directional.")

        st.subheader("Per-zone state")
        zi = {z: i for i, z in enumerate(res["zone_ids"])}
        ztab = [{"Zone": model.zones[z].name, "Volume (m³)": round(model.zones[z].volume, 1),
                 "Pressure (Pa)": round(afr["P"][z], 3),
                 "Outdoor ACH (/h)": round(afr["ach"][z], 3),
                 "Day-avg NO₂ (ppb)": round(float(np.mean(res["series"][:, zi[z]])), 2)}
                for z in res["zone_ids"]]
        st.dataframe(ztab, use_container_width=True, hide_index=True)
        st.caption("Sanity anchors: zone pressures within a few Pa of ambient; stack makes "
                   "lower zones slightly negative and upper zones positive on cold days. — "
                   "Drafted by Claude with prompts engineered by Yannai Kashtan")
