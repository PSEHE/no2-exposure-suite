// Exact-lookup engine over the 86,400-scenario CONTAM library.
// Port of core/library.py: scenario lookup, ZIP-weighted annual exposure, and
// archetype selection. Every value returned for an in-grid scenario IS the
// stored CONTAM result.

import library from '@data/scenario_library.json'
import archetypes from '@data/archetypes.json'
import { OUTDOOR_FRACTION } from './constants.js'

const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x))
// Fixed cooking reference used to measure the window's ventilation response
// for outdoor penetration (so penetration is independent of the selected use).
const PEN_REF_USE = 'high'

const S = library.schema

// Precompute token -> index maps for each dimension (each is tiny).
const idxMap = {}
for (const dim of ['houses', 'hood', 'use', 'window', 'temp', 'wind', 'oc']) {
  idxMap[dim] = new Map(S[dim].map((t, i) => [t, i]))
}

const N = { hood: S.hood.length, use: S.use.length, window: S.window.length,
  temp: S.temp.length, wind: S.wind.length, oc: S.oc.length }

// Flat-array index, matching the nesting used by the exporter:
// house > hood > use > window > temp > wind > oc
export function scenarioIndex(house, hood, use, win, temp, wind, oc) {
  const hi = idxMap.houses.get(house)
  const hdi = idxMap.hood.get(hood)
  const ui = idxMap.use.get(use)
  const wi = idxMap.window.get(win)
  const ti = idxMap.temp.get(temp)
  const wdi = idxMap.wind.get(wind)
  const oci = idxMap.oc.get(oc)
  return (((((hi * N.hood + hdi) * N.use + ui) * N.window + wi) * N.temp + ti) * N.wind + wdi) * N.oc + oci
}

// Exact CONTAM result for one fully-specified scenario.
export function lookup(house, hood, use, win, temp, wind, oc) {
  const i = scenarioIndex(house, hood, use, win, temp, wind, oc)
  return {
    no2: {
      peak: library.no2.peak[i],
      hravg: library.no2.hravg[i],
      eighthravg: library.no2.eighthravg[i],
      dayavg: library.no2.dayavg[i],
    },
    conta: {
      hravg: library.conta.hravg[i],
      dayavg: library.conta.dayavg[i],
    },
  }
}

// Annual-representative exposure: average the exact scenarios over a set of
// temperature and wind weights, then add the outdoor contribution.
//
// Outdoor handling (per the physics): while OUTDOORS the person breathes the
// full outdoor concentration; while INDOORS they breathe a penetrated fraction.
// To keep ventilation monotonic (opening windows lowers exposure under clean
// outdoor), the indoor penetration is recomputed from the SAME air exchange
// that dilutes the stove:
//     pen(win) = 1 - (1 - pen_closed) * [stove(win) / stove(closed)]
// where pen_closed is calibrated from the closed-window CONTA tracer. So
// penetration only rises when the stove actually dilutes. Then:
//     outdoor_attributable = outdoor * [ (1 - f_out) * pen + f_out ]
// with f_out the fraction of at-home time spent outdoors (full outdoor).
//   temps: [[token, weight], ...]   winds: [[token, weight], ...]
export function weightedExposure({ house, hood, use, win, oc, temps, winds, outdoorNO2 = 0, penUse = null }) {
  const fout = OUTDOOR_FRACTION[oc] ?? 0
  let stoveLong = 0, outdoorLong = 0, stoveHrW = 0
  let stoveHrMax = 0, stove8Max = 0, peakMax = 0, wsum = 0
  for (const [t, tw] of temps) {
    for (const [wd, ww] of winds) {
      const c = tw * ww
      if (!c) continue
      const r = lookup(house, hood, use, win, t, wd, oc)
      // Outdoor penetration's window-response is tied to the cooking level being
      // shown, so it tracks the actual stove dilution and stays monotonic under
      // clean outdoor. For no-cook/electric, fall back to a cooking reference so
      // penetration remains a building property (and the electric comparison can
      // pass penUse = the gas use to keep it identical). CONTA is use-independent.
      const pu0 = penUse || use
      const puHasStove = lookup(house, hood, pu0, 'closed', t, wd, oc).no2.dayavg > 1e-9
      const pu = puHasStove ? pu0 : PEN_REF_USE
      const refWin = lookup(house, hood, pu, win, t, wd, oc)
      const refClosed = lookup(house, hood, pu, 'closed', t, wd, oc)
      const penClosed = clamp((refClosed.conta.dayavg / 100 - fout) / (1 - fout), 0, 1)
      const R = refClosed.no2.dayavg > 1e-9
        ? clamp(refWin.no2.dayavg / refClosed.no2.dayavg, 0, 1) // stove dilution ratio
        : 1
      const pen = clamp(1 - (1 - penClosed) * R, 0, 1)
      const outdoorAttr = outdoorNO2 * ((1 - fout) * pen + fout)
      stoveLong += r.no2.dayavg * c
      outdoorLong += outdoorAttr * c
      stoveHrW += r.no2.hravg * c
      stoveHrMax = Math.max(stoveHrMax, r.no2.hravg)
      stove8Max = Math.max(stove8Max, r.no2.eighthravg)
      peakMax = Math.max(peakMax, r.no2.peak)
      wsum += c
    }
  }
  return {
    stoveLong, outdoorLong, totalLong: stoveLong + outdoorLong,
    stoveHr: stoveHrW, stoveHrMax, stove8Max, peakMax,
    outdoorNO2, weightSum: wsum,
  }
}

// Convenience: build temp/wind weights from a ZIP's seasonal climate + wind
// distribution (50/50 winter/summer). Falls back to a single temp + national
// wind distribution when no ZIP is provided.
export function weightsFromClimate({ winterTemp, summerTemp, windWeights }) {
  const temps = winterTemp === summerTemp
    ? [[winterTemp, 1]]
    : [[winterTemp, 0.5], [summerTemp, 0.5]]
  const winds = Object.entries(windWeights)
  return { temps, winds }
}

export const HOUSES = S.houses

// --- Archetype selection (port of select_floorplan) ---
const OLDER = new Set(['vintage_prior_1940', 'vintage_1940_1959', 'vintage_1960_1979'])

export function selectArchetype(typehuq, sqftrange, vintage, stories, centralAC) {
  const yesAC = centralAC === 'yesAHS'
  const older = OLDER.has(vintage)

  if (typehuq === 'MH') {
    if (yesAC) {
      if (older) return 'MH-4'
      return vintage === 'vintage_1980_1999' ? 'MH-1' : 'MH-2'
    }
    return 'MH-3'
  }
  if (typehuq === 'DH') {
    if (sqftrange === 'floor_area_0_1499') {
      if (yesAC) return 'DH-2'
      return older ? 'DH-29' : 'DH-42'
    }
    if (stories === 'single_story') return older ? 'DH-7' : 'DH-1'
    return yesAC ? 'DH-17' : 'DH-81'
  }
  if (typehuq === 'AH') {
    if (sqftrange === 'floor_area_0_1499') {
      if (stories === 'single_story') {
        if (yesAC) return older ? 'AH-3' : 'AH-39'
        return 'AH-8'
      }
      return 'AH-1'
    }
    if (sqftrange === 'floor_area_1500_2499') return 'AH-21'
    return 'AH-34'
  }
  // APT
  if (sqftrange === 'floor_area_0_1499') {
    if (yesAC) return 'APT-1'
    if (vintage === 'vintage_prior_1940' || vintage === 'vintage_1940_1959') return 'APT-4'
    if (vintage === 'vintage_1960_1979') return 'APT-5'
    if (vintage === 'vintage_1980_1999' || vintage === 'vintage_2000_2009') return 'APT-3'
    return 'APT-62'
  }
  return yesAC ? 'APT-35' : 'APT-28'
}

// --- Floor-area selection / interpolation among the 24 ---
// The user describes a home (type + total floor area); we bracket the two homes
// of that type nearest in floor area and interpolate their exposure outputs.

// Homes of a type, sorted by floor area (ft²): [{ id, area }].
export function homesByArea(type) {
  return HOUSES
    .filter((h) => archetypes[h].type === type && archetypes[h].floor_area_ft2 != null)
    .map((h) => ({ id: h, area: archetypes[h].floor_area_ft2 }))
    .sort((a, b) => a.area - b.area)
}

// Two homes bracketing `floorArea` within a type, plus the weight toward the
// larger. Clamps at the ends (no extrapolation).
export function bracketByArea(type, floorArea) {
  const pool = homesByArea(type)
  if (!pool.length) return null
  if (floorArea <= pool[0].area) return { below: pool[0].id, above: pool[0].id, w: 0, pool }
  const top = pool[pool.length - 1]
  if (floorArea >= top.area) return { below: top.id, above: top.id, w: 1, pool }
  let lo = pool[0], hi = top
  for (let i = 0; i < pool.length - 1; i++) {
    if (pool[i].area <= floorArea && floorArea <= pool[i + 1].area) {
      lo = pool[i]; hi = pool[i + 1]; break
    }
  }
  const span = hi.area - lo.area
  return { below: lo.id, above: hi.id, w: span > 0 ? (floorArea - lo.area) / span : 0, pool }
}

// Linearly blend two exposure result objects (numeric fields) toward `above`.
function blendExposure(a, b, w) {
  if (!b || w === 0) return a
  if (w === 1) return b
  const out = {}
  for (const k of Object.keys(a)) {
    out[k] = typeof a[k] === 'number' ? a[k] * (1 - w) + b[k] * w : a[k]
  }
  return out
}

// weightedExposure for a described home: interpolate between the bracketing homes.
export function interpExposure(args, bracket) {
  if (!bracket) return weightedExposure(args)
  const a = weightedExposure({ ...args, house: bracket.below })
  if (bracket.above === bracket.below) return a
  return blendExposure(a, weightedExposure({ ...args, house: bracket.above }), bracket.w)
}
