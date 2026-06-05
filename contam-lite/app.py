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
import streamlit as st

from core import config, constants, prj, transport, population, persily, transform, apartments

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
    return prj.parse_prj(config.prj_path(house))


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


def run_persily(entry, scenario):
    """Simulate a Persily home under the scenario; return (res, kitchen_name, kitchen_curve)."""
    m = load_persily_model(entry["rel_path"])
    kid = transform.kitchen_zone_id(m)
    res = transport.simulate(m, kitchen_zone=kid, **scenario)
    kname = m.zones[kid].name if kid is not None else None
    return res, kname, res["by_zone"].get(kname, np.zeros_like(res["t"]))


def metrics_row(ach, peak, mx1, davg):
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Air exchange", f"{ach:.2f} /h")
    c2.metric("Kitchen peak NO₂", f"{peak:.0f} ppb")
    c3.metric("Kitchen max 1-hr", f"{mx1:.0f} ppb",
              delta=f"{mx1 - EPA_1HR:.0f} vs EPA/WHO 1-hr", delta_color="inverse")
    c4.metric("Kitchen daily avg", f"{davg:.1f} ppb")


arch = load_archetypes()
st.sidebar.title("CONTAM-Lite")
st.sidebar.caption("First-principles multizone NO₂ engine — Kashtan et al. 2024/2025.")
panel = st.sidebar.radio("Panel", ["Single home", "Population & health"])
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

    # --- shared scenario knobs ---
    st.sidebar.subheader("Environment")
    T_out = st.sidebar.slider("Outdoor temperature (°C)", -15, 38, 5)
    wind = st.sidebar.slider("Wind speed (m/s)", 0.0, 12.0, 3.0, 0.5)
    outdoor_no2 = st.sidebar.slider("Outdoor NO₂ (ppb)", 0.0, 40.0, 7.0, 0.5)
    st.sidebar.subheader("Ventilation")
    window_open = st.sidebar.slider("Window opening (0 = closed, 1 = wide)", 0.0, 1.0, 0.0, 0.05)
    hood = st.sidebar.selectbox(
        "Range hood", ["NoHood", "25CE", "50CE", "75CE"],
        format_func=lambda h: {"NoHood": "None / recirculating", "25CE": "Standard (25%)",
                               "50CE": "Good (50%)", "75CE": "High-efficiency (75%)"}[h])
    st.sidebar.subheader("Cooking")
    pattern = st.sidebar.selectbox("Meal pattern", list(COOKING_PATTERNS), index=2)
    intensity = st.sidebar.slider("Burner intensity (× one burner)", 0.5, 4.0, 1.0, 0.25)
    scenario = dict(T_out_C=T_out, wind_ms=wind, window_open=window_open, hood=hood,
                    cooking=COOKING_PATTERNS[pattern], C_out_ppb=outdoor_no2,
                    emission_scale=intensity)

    st.title("Single-home NO₂")

    # ----------- Describe your home: select the right floorplan + interpolate -----------
    if mode == "Describe your home":
        below, above, w = bracket
        res_b, _, kit_b = run_persily(below, scenario)
        if above["id"] == below["id"]:
            t, kitchen, havg = res_b["t"], kit_b, home_avg_curve(res_b)
            ach = res_b["whole_home_ach"]
            st.caption(
                f"Closest available {TYPE_NAMES[below['type']]} home: **{below['floor_area_ft2']:,} ft²** "
                f"({below['id']}); target is at the edge of the range, so no interpolation was needed. "
                "Fidelity for non-paper homes is physically faithful but unvalidated.")
        else:
            res_a, _, kit_a = run_persily(above, scenario)
            t = res_b["t"]
            kitchen = (1 - w) * kit_b + w * kit_a
            havg = (1 - w) * home_avg_curve(res_b) + w * home_avg_curve(res_a)
            ach = (1 - w) * res_b["whole_home_ach"] + w * res_a["whole_home_ach"]
            st.caption(
                f"Interpolated between **{below['id']}** ({below['floor_area_ft2']:,} ft²) and "
                f"**{above['id']}** ({above['floor_area_ft2']:,} ft²) — {w:.0%} of the way to the larger. "
                "Fidelity for non-paper homes is physically faithful but unvalidated.")
        peak = float(np.max(kitchen[:144]))
        metrics_row(ach, peak, rolling_max_1h(kitchen), float(np.mean(kitchen[:144])))

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
            res = transport.simulate(apt_model, kitchen_zone=kid, **scenario)
        zone_ids = res["zone_ids"]
        t = res["t"]
        kitchen = res["series"][:, zone_ids.index(kid)]
        st.caption(
            f"{apt_meta['id']} · full {apt_meta['n_floors']}-storey building "
            f"({len(apt_model.zones)} zones) · you're on floor {apt_floor_no}"
            + (f", unit {apt_tag}" if apt_tag else "")
            + ". Whole-building stack effect modeled through the stairwell; your unit is shown. "
            "Fidelity for non-paper homes is physically faithful but unvalidated.")
        metrics_row(res["whole_home_ach"], float(np.max(kitchen[:144])),
                    rolling_max_1h(kitchen), float(np.mean(kitchen[:144])))

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
            res = transport.simulate(model, kitchen_zone=upload_kitchen, **scenario)
            kname = (model.zones[upload_kitchen].name if upload_kitchen is not None
                     else kitchen_zone_name(model))
            kitchen = res["by_zone"].get(kname, np.zeros_like(res["t"]))
            home_title = f"Custom: {len(model.zones)} zones"
        t = res["t"]
        st.caption(f"{home_title} · {len(model.paths)} flow paths · "
                   "first-principles airflow + transport (not a lookup).")
        davg = res["summary"][kname]["dayavg"] if kname in res["summary"] else float(np.mean(kitchen))
        metrics_row(res["whole_home_ach"], float(np.max(kitchen)), rolling_max_1h(kitchen), davg)

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
else:
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
