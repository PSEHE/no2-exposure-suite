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

from core import (config, constants, diagnostics as dg, prj, transport,
                  population, persily, transform, apartments)

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


def scenario_sidebar(prefix="", no2_default=7.0, window_style="fraction"):
    """Shared environment/ventilation/cooking knobs -> a simulate() kwargs dict.

    window_style="fraction": the research-grade 0..1 opening slider (diagnostics).
    window_style="hours": hours-per-day budget + which-windows menu + an
    open-during-cooking toggle; the concrete schedule is resolved per model by
    apply_window_spec() (the kitchen zone id differs per home)."""
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
    pattern = st.sidebar.selectbox("Meal pattern", list(COOKING_PATTERNS), index=2,
                                   key=f"{prefix}p")
    intensity = st.sidebar.slider("Burner intensity (× one burner)", 0.5, 4.0, 1.0, 0.25,
                                  key=f"{prefix}i")
    scenario = dict(T_out_C=T_out, wind_ms=wind, window_open=window_open, hood=hood,
                    cooking=COOKING_PATTERNS[pattern], C_out_ppb=outdoor_no2,
                    emission_scale=intensity)
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

    # --- shared scenario knobs (hours-based window schedule) ---
    scenario = scenario_sidebar(prefix="sh_", window_style="hours")

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


# ======================= POPULATION & HEALTH =======================
elif panel == "Population & health":
    st.sidebar.subheader("Population")
    gas_pct = st.sidebar.slider("Homes cooking with gas/propane (%)", 0, 100, 38)
    hood_adopt = st.sidebar.slider("Homes with an effective vented hood (%)", 0, 100, 22)
    cook_int = st.sidebar.slider("Cooking intensity (light → heavy)", 0.0, 1.0, 0.35, 0.05)
    size_shift = st.sidebar.slider("Home size (smaller ← → larger)", -1.0, 1.0, 0.0, 0.1)
    climate = st.sidebar.selectbox("Climate", list(population.CLIMATE_TEMP), index=2)

    mean = population.population_mean_exposure(
        house_w=population.home_size_weights(size_shift),
        hood=population.hood_dist(hood_adopt / 100),
        use=population.use_dist(cook_int), climate=climate)
    gas_frac = gas_pct / 100
    pop_mean = mean * gas_frac      # averaged over all homes (electric add 0 stove NO2)
    out = population.health_outcomes(mean, gas_frac)

    st.title("Population NO₂ & health")
    st.caption("Reweights the exact 86,400-scenario CONTAM library by population distributions; "
               "health burdens scale the published national estimates with exposure × prevalence.")
    c1, c2, c3 = st.columns(3)
    c1.metric("Stove NO₂ (gas-stove homes)", f"{mean:.1f} ppb",
              delta=f"{mean - WHO_ANNUAL:.1f} vs WHO annual", delta_color="inverse")
    c2.metric("Stove NO₂ (whole population)", f"{pop_mean:.1f} ppb")
    c3.metric("Gas/propane prevalence", f"{gas_pct}%")
    st.divider()
    h1, h2, h3 = st.columns(3)
    h1.metric("Pediatric asthma cases", f"{out['asthma_cases']:,.0f}/yr")
    h2.metric("Adult deaths", f"{out['deaths']:,.0f}/yr")
    h3.metric("Societal cost", f"${out['cost_usd']/1e9:,.0f}B/yr")

    fig = go.Figure()
    fig.add_trace(go.Bar(x=["Stove NO₂ (gas-stove homes)"], y=[mean],
                         marker_color="#e8743b", name="stove NO₂"))
    fig.add_hline(y=WHO_ANNUAL, line_dash="dash", line_color="#2f9e57",
                  annotation_text="WHO annual (5.3 ppb)")
    fig.update_layout(yaxis_title="long-term NO₂ (ppb)", height=320,
                      margin=dict(l=10, r=10, t=10, b=10), showlegend=False)
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "Asthma uses the gas-cooking odds ratio 1.32 (Lin, Brunekreef & Gehring 2013); mortality uses "
        "RR 1.02 per 10 µg/m³ (Atkinson et al.); VSL $13.1M, asthma $5,300/case. Burdens are anchored to "
        "the papers' central national estimates (≈50k asthma cases, ≈19k deaths at 38% prevalence) and "
        "scaled linearly by modeled exposure × prevalence. — "
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
