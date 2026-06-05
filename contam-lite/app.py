"""CONTAM-Lite — interactive multizone NO2 engine (research-grade).

A from-first-principles port of the CONTAM physics behind Kashtan et al.
2024/2025: parses a floorplan, solves the airflow network, and integrates
contaminant transport — live, for any set of physical conditions. This is the
single-home panel; a population panel will be added alongside.

Run from the repo root:  streamlit run contam-lite/app.py
"""
import sys
from pathlib import Path

# Make the repo root importable (so `core` resolves) regardless of launch dir.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import json
import numpy as np
import plotly.graph_objects as go
import streamlit as st

from core import config, constants, prj, transport

st.set_page_config(page_title="CONTAM-Lite — NO₂ engine", layout="wide")

WHO_1HR = constants.WHO_1HR_PPB     # ~106 ppb
EPA_1HR = constants.EPA_1HR_PPB     # 100 ppb
WHO_ANNUAL = constants.WHO_ANNUAL_PPB  # ~5.3 ppb

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


@st.cache_resource
def load_archetypes():
    with open(ROOT / "web_data" / "archetypes.json") as f:
        return json.load(f)


@st.cache_resource
def load_model(house):
    return prj.parse_prj(config.DATABASE_HOUSES / house / f"{house}.prj")


def kitchen_zone_name(model):
    """Zone holding a cooktop NO2 source (else a zone literally named 'kitchen')."""
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
    means = np.convolve(arr, np.ones(w) / w, mode="valid")
    return float(np.max(means))


# ----------------------------- Sidebar -----------------------------
arch = load_archetypes()
st.sidebar.title("CONTAM-Lite")
st.sidebar.caption("Full multizone physics — airflow network + contaminant transport.")

type_names = {"DH": "Detached", "AH": "Attached", "MH": "Manufactured", "APT": "Apartment"}
house_opts = sorted(
    constants.HOUSES,
    key=lambda h: (h.split("-")[0], arch[h]["total_volume_m3"] or 0),
)
def house_label(h):
    a = arch[h]
    return f"{type_names.get(a['type'], a['type'])} · {round(a['total_volume_m3'])} m³ ({h})"

house = st.sidebar.selectbox("Floorplan", house_opts, index=house_opts.index("DH-1"),
                             format_func=house_label)

st.sidebar.subheader("Environment")
T_out = st.sidebar.slider("Outdoor temperature (°C)", -15, 38, 5)
wind = st.sidebar.slider("Wind speed (m/s)", 0.0, 12.0, 3.0, 0.5)
outdoor_no2 = st.sidebar.slider("Outdoor NO₂ (ppb)", 0.0, 40.0, 7.0, 0.5)

st.sidebar.subheader("Ventilation")
window_open = st.sidebar.slider("Window opening (0 = closed, 1 = wide)", 0.0, 1.0, 0.0, 0.05)
hood = st.sidebar.selectbox("Range hood", ["NoHood", "25CE", "50CE", "75CE"],
                            format_func=lambda h: {"NoHood": "None / recirculating",
                                                   "25CE": "Standard (25% capture)",
                                                   "50CE": "Good (50%)",
                                                   "75CE": "High-efficiency (75%)"}[h])

st.sidebar.subheader("Cooking")
pattern = st.sidebar.selectbox("Meal pattern", list(COOKING_PATTERNS), index=2)
intensity = st.sidebar.slider("Burner intensity (× one burner)", 0.5, 4.0, 1.0, 0.25)

# ----------------------------- Run engine -----------------------------
model = load_model(house)
res = transport.simulate(
    model, T_out_C=T_out, wind_ms=wind, window_open=window_open, hood=hood,
    cooking=COOKING_PATTERNS[pattern], C_out_ppb=outdoor_no2, emission_scale=intensity,
)
t = res["t"]
kname = kitchen_zone_name(model)
kitchen = res["by_zone"].get(kname, np.zeros_like(t))

# ----------------------------- Header + metrics -----------------------------
st.title("CONTAM-Lite — multizone NO₂ engine")
st.caption(
    f"{house_label(house)} · {len(model.zones)} zones · {len(model.paths)} flow paths · "
    "first-principles airflow + transport (not a lookup)."
)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Air exchange", f"{res['whole_home_ach']:.2f} /h")
c2.metric("Kitchen peak NO₂", f"{np.max(kitchen):.0f} ppb",
          help="Instantaneous maximum in the kitchen.")
c3.metric("Kitchen max 1-hr", f"{rolling_max_1h(kitchen):.0f} ppb",
          delta=f"{rolling_max_1h(kitchen) - EPA_1HR:.0f} vs EPA/WHO 1-hr",
          delta_color="inverse")
c4.metric("Kitchen daily avg", f"{res['summary'][kname]['dayavg']:.1f} ppb")

# ----------------------------- Time-series plot -----------------------------
st.subheader("NO₂ concentration over 24 hours")
# show the most relevant rooms (kitchen + highest-peak others), skip buffer zones
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
fig.update_layout(
    xaxis_title="hour of day", yaxis_title="NO₂ (ppb)",
    height=430, margin=dict(l=10, r=10, t=10, b=10),
    legend=dict(orientation="h", yanchor="bottom", y=1.02),
    xaxis=dict(tickvals=[0, 6, 12, 18, 24]),
)
st.plotly_chart(fig, use_container_width=True)

# ----------------------------- Room table -----------------------------
st.subheader("By room")
rows = []
for n, s in ranked:
    rows.append({"Room": n, "Peak (ppb)": round(s["peak"], 1),
                 "Max 1-hr (ppb)": round(rolling_max_1h(res["by_zone"][n]), 1),
                 "Daily avg (ppb)": round(s["dayavg"], 2)})
st.dataframe(rows, use_container_width=True, hide_index=True)

st.caption(
    "NO₂ decay −2.4×10⁻⁴/s · interior doors mixed at 1000 m³/h · benchmarks: WHO/EPA 1-hr ≈ 100 ppb, "
    "WHO annual ≈ 5.3 ppb. Built on Kashtan et al. 2024/2025. — "
    "Drafted by Claude with prompts engineered by Yannai Kashtan"
)
