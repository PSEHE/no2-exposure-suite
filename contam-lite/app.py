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

from core import config, constants, prj, transport, population

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


arch = load_archetypes()
st.sidebar.title("CONTAM-Lite")
st.sidebar.caption("First-principles multizone NO₂ engine — Kashtan et al. 2024/2025.")
panel = st.sidebar.radio("Panel", ["Single home", "Population & health"])
st.sidebar.divider()


# ============================ SINGLE HOME ============================
if panel == "Single home":
    house_opts = sorted(constants.HOUSES,
                        key=lambda h: (h.split("-")[0], arch[h]["total_volume_m3"] or 0))
    def house_label(h):
        a = arch[h]
        return f"{TYPE_NAMES.get(a['type'], a['type'])} · {round(a['total_volume_m3'])} m³ ({h})"

    house = st.sidebar.selectbox("Floorplan", house_opts, index=house_opts.index("DH-1"),
                                 format_func=house_label)
    up = st.sidebar.file_uploader("…or upload a CONTAM .prj", type=["prj"])
    custom_kitchen = None
    if up is not None:
        model = parse_uploaded(up.name, up.getvalue().decode("latin-1", "ignore"))
        house_title = f"Custom: {up.name} · {len(model.zones)} zones"
        if not has_no2_source(model):
            zopts = {f"{z.name} (#{z.id}, {z.volume:.0f} m³)": z.id
                     for z in sorted(model.zones.values(), key=lambda z: -z.volume)}
            pick = st.sidebar.selectbox("Kitchen zone (no stove source in file)", list(zopts))
            custom_kitchen = zopts[pick]
    else:
        model = load_model(house)
        house_title = house_label(house)

    st.sidebar.subheader("Environment")
    T_out = st.sidebar.slider("Outdoor temperature (°C)", -15, 38, 5)
    wind = st.sidebar.slider("Wind speed (m/s)", 0.0, 12.0, 3.0, 0.5)
    outdoor_no2 = st.sidebar.slider("Outdoor NO₂ (ppb)", 0.0, 40.0, 7.0, 0.5)
    st.sidebar.subheader("Ventilation")
    window_open = st.sidebar.slider("Window opening (0 = closed, 1 = wide)", 0.0, 1.0, 0.0, 0.05)
    hood = st.sidebar.selectbox("Range hood", ["NoHood", "25CE", "50CE", "75CE"],
                                format_func=lambda h: {"NoHood": "None / recirculating",
                                                       "25CE": "Standard (25%)", "50CE": "Good (50%)",
                                                       "75CE": "High-efficiency (75%)"}[h])
    st.sidebar.subheader("Cooking")
    pattern = st.sidebar.selectbox("Meal pattern", list(COOKING_PATTERNS), index=2)
    intensity = st.sidebar.slider("Burner intensity (× one burner)", 0.5, 4.0, 1.0, 0.25)

    res = transport.simulate(model, T_out_C=T_out, wind_ms=wind, window_open=window_open,
                             hood=hood, cooking=COOKING_PATTERNS[pattern], C_out_ppb=outdoor_no2,
                             emission_scale=intensity, kitchen_zone=custom_kitchen)
    t = res["t"]
    kname = model.zones[custom_kitchen].name if custom_kitchen is not None else kitchen_zone_name(model)
    kitchen = res["by_zone"].get(kname, np.zeros_like(t))

    st.title("Single-home NO₂")
    st.caption(f"{house_title} · {len(model.paths)} flow paths · "
               "first-principles airflow + transport (not a lookup).")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Air exchange", f"{res['whole_home_ach']:.2f} /h")
    c2.metric("Kitchen peak NO₂", f"{np.max(kitchen):.0f} ppb")
    c3.metric("Kitchen max 1-hr", f"{rolling_max_1h(kitchen):.0f} ppb",
              delta=f"{rolling_max_1h(kitchen) - EPA_1HR:.0f} vs EPA/WHO 1-hr", delta_color="inverse")
    c4.metric("Kitchen daily avg", f"{res['summary'][kname]['dayavg']:.1f} ppb")

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
    st.caption("NO₂ decay −2.4×10⁻⁴/s · interior doors mixed at 1000 m³/h · "
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
