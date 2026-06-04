"""Physical and epidemiological constants from the two papers.

Sources:
  [SciAdv] Kashtan et al. 2024, Sci. Adv. 10, eadm8680
  [PNAS]   Kashtan et al. 2025, PNAS Nexus 4, pgaf341
"""

# --- Unit conversion -------------------------------------------------------
# The papers convert between molar mixing ratio and mass concentration at
# 25 C, 1 atm using 1 ppbv NO2 = 1.88 ug/m^3 [SciAdv, Definitions].
# (The original post-processing pipeline used 1.89 when converting raw CONTAM
# mass output to ppb; the values stored in the scenario library are already ppb.)
UGM3_PER_PPB = 1.88

def ugm3_to_ppb(x):
    return x / UGM3_PER_PPB

def ppb_to_ugm3(x):
    return x * UGM3_PER_PPB

# --- Health benchmarks -----------------------------------------------------
# Long-term (annual-average) NO2 guidelines/standards
WHO_ANNUAL_UGM3 = 10.0          # WHO global air quality guideline (2021)
WHO_ANNUAL_PPB = ugm3_to_ppb(WHO_ANNUAL_UGM3)      # ~5.32 ppb
CANADA_ANNUAL_UGM3 = 20.0       # Health Canada residential long-term guideline
CANADA_ANNUAL_PPB = ugm3_to_ppb(CANADA_ANNUAL_UGM3)  # ~10.6 ppb
EPA_ANNUAL_PPB = 53.0           # US EPA NAAQS annual (applies outdoors)

# Short-term (1-hour-average) guidelines/standards
WHO_1HR_UGM3 = 200.0            # WHO 1-hour exposure guideline
WHO_1HR_PPB = ugm3_to_ppb(WHO_1HR_UGM3)            # ~106 ppb (paper rounds to ~100)
EPA_1HR_PPB = 100.0             # US EPA NAAQS 1-hour (applies outdoors)
CANADA_1HR_UGM3 = 170.0         # Health Canada 1-hour residential standard
CANADA_1HR_PPB = ugm3_to_ppb(CANADA_1HR_UGM3)      # ~90 ppb

# --- Indoor air physics ----------------------------------------------------
# First-order NO2 decay/deposition rate indoors [PNAS Methods; SciAdv].
NO2_DECAY_PER_S = -2.4e-4               # central estimate
NO2_DECAY_PER_H = NO2_DECAY_PER_S * 3600  # ~ -0.86 / h
NO2_DECAY_PER_S_RANGE = (-4.7e-5, -5.7e-4)  # ~ (-0.17, -2.07) / h

# Air-exchange rate (whole-home), modeled across scenarios [PNAS].
AIR_EXCHANGE_MEAN_PER_H = 0.74          # mean across scenarios
AIR_EXCHANGE_RANGE_PER_H = (0.0, 6.0)   # closed/no-gradient  ->  open/cold/windy

# Bidirectional flow used to represent open interior doorways [SciAdv/PNAS].
INTERIOR_DOOR_FLOW_M3_H = 1000.0        # ~590 cfm

# --- Stove NO2 emissions ---------------------------------------------------
# Source rate of a single burner on HIGH, taken directly from the CONTAM .prj
# source/sink element "burn_NO2" (kg/s). 3.1878e-8 kg/s ~ 115 mg/h.
BURNER_NO2_HIGH_KG_S = 3.1878e-8
OVEN_PREHEAT_NO2_KG_S = 3.66597e-8      # "ov_pr_NO2", ~132 mg/h during cycling
def kg_s_to_mg_h(x):
    return x * 1e6 * 3600.0
BURNER_NO2_HIGH_MG_H = kg_s_to_mg_h(BURNER_NO2_HIGH_KG_S)  # ~114.8 mg/h

# Median NO2 emission factors (per joule of fuel burned) [SciAdv].
EF_GAS_NG_PER_J = 8.2          # gas burners on low ~8.2; on high ~8.7 ng/J
EF_PROPANE_NG_PER_J = 8.2      # statistically indistinguishable from gas
# A burner that is "on" averages ~6.5 MJ/h energy -> ~48 mg NO2/h (half of high) [PNAS].
BURNER_ON_ENERGY_MJ_H = 6.5
BURNER_ON_NO2_MG_H = 48.0
# Daily stove NO2 (mg/day) at 5th / 50th / 95th percentile cooking [PNAS].
DAILY_NO2_MG_PERCENTILES = {"5th": 0.64, "50th": 31.0, "95th": 199.0}

# --- Epidemiology (population panel, CONTAM-Lite) --------------------------
# Childhood asthma / wheeze effect estimates ALL taken from the indoor-NO2 /
# gas-cooking meta-analysis (per user direction, in place of Puzzolo et al.):
#   Lin W., Brunekreef B., Gehring U. (2013). Meta-analysis of the effects of
#   indoor nitrogen dioxide and gas cooking on asthma and wheeze in children.
#   Int. J. Epidemiol. 42(6), 1724-1737.  (41 studies, random-effects)
# Childhood asthma vs. presence/absence of gas cooking:
ASTHMA_GAS_COOKING_OR = 1.32
ASTHMA_GAS_COOKING_OR_CI = (1.18, 1.48)
# Childhood asthma per 15-ppb increase in indoor NO2 (CI crosses 1; not sig.):
ASTHMA_NO2_OR_PER_15PPB = 1.09
ASTHMA_NO2_OR_PER_15PPB_CI = (0.91, 1.31)
# Current wheeze vs. indoor NO2 (random-effects summary OR):
WHEEZE_NO2_OR = 1.15
WHEEZE_NO2_OR_CI = (1.06, 1.25)

# All-cause adult mortality RR per 10 ug/m^3 long-term NO2 (Atkinson et al.) [SciAdv].
MORTALITY_RR_PER_10UGM3 = 1.02
MORTALITY_RR_PER_10UGM3_CI = (1.01, 1.03)
# Valuation
VSL_USD = 13.1e6                # EPA value of a statistical life (BenMAP), 2023 $
ASTHMA_COST_USD_PER_CASE_YR = 5300.0

# Headline national results (for context / sanity checks) [SciAdv, PNAS]
NATIONAL_STOVE_LONGTERM_PPB = 2.4      # population-avg stove-attributable long-term
NATIONAL_TOTAL_LONGTERM_PPB = 10.1     # total (stove + outdoor) for gas/propane homes
NATIONAL_PEDIATRIC_ASTHMA_CASES = 50_000
NATIONAL_ADULT_DEATHS = 19_000

# --- Scenario library dimensions (canonical 86,400-entry grid) -------------
HOODS = ["NoHood", "25CE", "50CE", "75CE"]
USES = ["zero", "low", "med", "medNoBk", "high"]
WINDOWS = ["closed", "moderate", "open"]
TEMPS = ["COLD", "COOL", "RT", "WARM"]
WINDS = ["STILL", "BREEZE", "WINDY"]
OCCUPANCIES = [
    "fifth_kitchen", "fifth_outside", "median",
    "ninetyfifth_kitchen", "ninetyfifth_outside",
]
METRICS = ["peak", "hravg", "eighthravg", "dayavg"]

# The 24 representative floorplans
HOUSES = [
    "MH-1", "MH-2", "MH-3", "MH-4",
    "DH-1", "DH-2", "DH-7", "DH-17", "DH-29", "DH-42", "DH-81",
    "AH-1", "AH-3", "AH-8", "AH-21", "AH-34", "AH-39",
    "APT-1", "APT-3", "APT-4", "APT-5", "APT-28", "APT-35", "APT-62",
]
