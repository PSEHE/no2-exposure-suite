// Lightweight 24-hour kitchen NO2 box-model for the time-series visual.
//
// This is the "live solver" half of the hybrid engine. It produces the SHAPE of
// the concentration curve from simple, transparent physics (a well-mixed kitchen
// with cooking-event emissions, air exchange, and first-order decay). The HEIGHT
// is anchored to the exact CONTAM peak from the lookup library, so the curve is
// faithful at the anchor while still responding physically to the controls.

const DECAY_PER_H = 0.86 // NO2 first-order decay/deposition (paper central value)
const PPB_PER_MG_M3 = 1 / 0.00188 // 1 ppb NO2 = 1.88 ug/m^3 = 0.00188 mg/m^3
const STEPS = 144 // 10-minute steps over 24 h
const DT = 24 / STEPS // hours per step

// Whole-home-ish air exchange (1/h) as a function of behavior + environment.
// Only shapes the curve's decay rate; headline numbers come from the library.
export function airExchange(win, temp, wind) {
  const base = { closed: 0.2, moderate: 0.5, open: 1.4 }[win] ?? 0.4
  const tempAdd = { COLD: 0.6, COOL: 0.35, RT: 0.1, WARM: 0.0 }[temp] ?? 0.2
  const windAdd = { STILL: 0.0, BREEZE: 0.3, WINDY: 0.8 }[wind] ?? 0.3
  return Math.max(0.1, base + tempAdd + windAdd)
}

// Hood capture efficiency -> fraction of emissions that still reach the room.
const HOOD_FACTOR = { NoHood: 1.0, '25CE': 0.75, '50CE': 0.5, '75CE': 0.25 }

// Representative cooking events by stove-use level: {start hour, minutes, burners, oven}.
const EVENTS = {
  zero: [],
  low: [{ h: 7.7, min: 10, burners: 1, oven: false }],
  med: [
    { h: 7.5, min: 15, burners: 1, oven: false },
    { h: 18.0, min: 30, burners: 2, oven: true },
  ],
  medNoBk: [
    { h: 7.5, min: 15, burners: 1, oven: false },
    { h: 18.0, min: 35, burners: 2, oven: false },
  ],
  high: [
    { h: 7.0, min: 20, burners: 2, oven: true },
    { h: 12.0, min: 15, burners: 2, oven: false },
    { h: 18.0, min: 40, burners: 3, oven: true },
  ],
}

const BURNER_MG_H = 48 // a burner that is "on" averages ~48 mg NO2/h (paper)
const OVEN_MG_H = 132 // oven preheat/cycling (~from .prj)

// Emission (mg/h) at each 10-min step for a given use level + hood.
function emissionSchedule(use, hood) {
  const e = new Array(STEPS).fill(0)
  const hoodFactor = HOOD_FACTOR[hood] ?? 1
  for (const ev of EVENTS[use] ?? []) {
    const start = Math.round((ev.h / 24) * STEPS)
    const nSteps = Math.max(1, Math.round(ev.min / (DT * 60)))
    const mgh = (ev.burners * BURNER_MG_H + (ev.oven ? OVEN_MG_H : 0)) * hoodFactor
    for (let s = start; s < start + nSteps && s < STEPS; s++) e[s] = mgh
  }
  return e
}

// Simulate a day. Returns hours[], kitchen[] (ppb), bedroom[] (ppb), cooking[] (0/1).
// peakAnchor: the exact CONTAM peak (ppb) to scale the stove component to.
export function simulateDay({ use, hood, win, temp, wind, kitchenVol = 30, outdoorNO2 = 0, peakAnchor = null }) {
  const lambda = airExchange(win, temp, wind)
  const r = lambda + DECAY_PER_H // total removal rate (1/h)
  const emit = emissionSchedule(use, hood)

  // Stove component (outdoor = 0), analytic update toward step steady-state.
  const stove = new Array(STEPS).fill(0)
  let c = 0
  for (let s = 0; s < STEPS; s++) {
    const q = (emit[s] / kitchenVol) * PPB_PER_MG_M3 // ppb/h source
    const css = q / r
    c = css + (c - css) * Math.exp(-r * DT)
    stove[s] = c
  }
  // Carry over past midnight for a smoother cyclic look (one extra pass seed).
  for (let s = 0; s < STEPS; s++) {
    const q = (emit[s] / kitchenVol) * PPB_PER_MG_M3
    const css = q / r
    c = css + (c - css) * Math.exp(-r * DT)
    stove[s] = c
  }

  // Anchor the stove peak to the exact CONTAM peak (shape from physics, height exact).
  const modelPeak = Math.max(...stove, 1e-9)
  const scale = peakAnchor != null && modelPeak > 0 ? peakAnchor / modelPeak : 1

  // Outdoor-infiltrated baseline (roughly steady): outdoor * lambda/r.
  const outdoorBase = outdoorNO2 * (lambda / r)

  const hours = [], kitchen = [], bedroom = [], cooking = []
  // Bedroom: a lagged, damped echo of the kitchen (first-order transport).
  let bedC = 0
  const bedRate = 0.9 // 1/h coupling
  for (let s = 0; s < STEPS; s++) {
    const kStove = stove[s] * scale
    const kTotal = kStove + outdoorBase
    bedC = bedC + (kTotal * 0.45 - bedC) * (1 - Math.exp(-bedRate * DT))
    hours.push(s * DT)
    kitchen.push(kTotal)
    bedroom.push(bedC)
    cooking.push(emit[s] > 0 ? 1 : 0)
  }
  return { hours, kitchen, bedroom, cooking, lambda }
}
