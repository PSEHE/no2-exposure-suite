// Health benchmarks and UI option mappings for the Explorer.
// Mirrors core/constants.py (the Python source of truth).

const UGM3_PER_PPB = 1.88

// Long- and short-term NO2 health benchmarks, in ppb.
export const BENCHMARKS = {
  whoAnnual: 10.0 / UGM3_PER_PPB,   // ~5.32 ppb  (WHO 2021 annual guideline)
  canadaAnnual: 20.0 / UGM3_PER_PPB, // ~10.6 ppb (Health Canada long-term)
  epaAnnual: 53.0,                   // US EPA NAAQS annual (outdoors)
  who1hr: 200.0 / UGM3_PER_PPB,      // ~106 ppb (WHO 1-hour guideline)
  epa1hr: 100.0,                     // US EPA NAAQS 1-hour (outdoors)
}

// National-average outdoor NO2 default (used until an address/ZIP is provided).
export const DEFAULT_OUTDOOR_PPB = 7.0

// --- Behavioral controls: friendly label -> scenario token + helper text ---
export const HOOD_OPTIONS = [
  { token: 'NoHood', label: 'No hood / rarely used', hint: 'No range hood, or one that recirculates instead of venting outside.' },
  { token: '25CE', label: 'Standard hood', hint: 'A typical over-the-range hood, regularly used (~25% capture).' },
  { token: '50CE', label: 'Good hood', hint: 'A well-fitted outside-venting hood (~50% capture).' },
  { token: '75CE', label: 'High-efficiency hood', hint: 'A strong outside-venting hood used every time (~75% capture).' },
]

export const USE_OPTIONS = [
  { token: 'zero', label: 'None', hint: 'No cooking with the stove today.' },
  { token: 'low', label: 'Light', hint: 'Reheating or light cooking for 1–2 people.' },
  { token: 'med', label: 'Average', hint: 'Typical cooking for 2–4 people, including some oven use.' },
  { token: 'medNoBk', label: 'Average, no baking', hint: 'Typical cooking but cooktop only — no oven/baking.' },
  { token: 'high', label: 'Heavy', hint: 'Cooking most meals for 5+ people.' },
]

export const WINDOW_OPTIONS = [
  { token: 'closed', label: 'Closed', hint: 'Windows kept closed.' },
  { token: 'moderate', label: 'Sometimes open', hint: 'A window open ~4 hours a day.' },
  { token: 'open', label: 'Often open', hint: 'Windows open most of the time.' },
]

export const OCCUPANCY_OPTIONS = [
  { token: 'fifth_kitchen', label: '5 min', hint: 'About 5 minutes a day in the kitchen.' },
  { token: 'median', label: '35 min', hint: 'About 35 minutes a day in the kitchen (typical).' },
  { token: 'ninetyfifth_kitchen', label: '2.5 h', hint: 'About 2.5 hours a day in the kitchen.' },
  { token: 'ninetyfifth_outside', label: 'Often out', hint: 'Out of the home ~8 hours a day.' },
  { token: 'fifth_outside', label: 'Home all day', hint: 'Rarely leaves the home.' },
]

// --- Environment controls (tucked away; sensible defaults) ---
export const TEMP_OPTIONS = [
  { token: 'COLD', label: 'Cold (<5 °C)' },
  { token: 'COOL', label: 'Cool (5–15 °C)' },
  { token: 'RT', label: 'Mild (15–25 °C)' },
  { token: 'WARM', label: 'Warm (>25 °C)' },
]
export const WIND_OPTIONS = [
  { token: 'STILL', label: 'Still' },
  { token: 'BREEZE', label: 'Breezy' },
  { token: 'WINDY', label: 'Windy' },
]

// National wind distribution when windows can be open (paper, NOAA normals).
export const NATIONAL_WIND_WEIGHTS = { STILL: 0.083, BREEZE: 0.608, WINDY: 0.308 }

// Fraction of at-home time spent OUTDOORS per occupancy schedule (= outdoor
// hours / non-away hours, from the Occupancy CSVs). During this time the
// person breathes full outdoor NO2; the rest is penetrated outdoor + stove.
export const OUTDOOR_FRACTION = {
  fifth_kitchen: 0.0617, // 1.08 / 17.5 h
  median: 0.0617,
  ninetyfifth_kitchen: 0.0617,
  fifth_outside: 0.0046, // 0.08 / 17.5 h
  ninetyfifth_outside: 0.3542, // 8.5 / 24 h (no away time)
}

// Human-readable archetype groups for the home picker.
export const TYPE_GROUPS = [
  { type: 'DH', label: 'Single-family detached' },
  { type: 'AH', label: 'Single-family attached' },
  { type: 'APT', label: 'Apartment / multifamily' },
  { type: 'MH', label: 'Mobile / manufactured' },
]
