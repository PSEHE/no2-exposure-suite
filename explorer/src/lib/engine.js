// Exact-lookup engine over the 86,400-scenario CONTAM library.
// Port of core/library.py: scenario lookup, ZIP-weighted annual exposure, and
// archetype selection. Every value returned for an in-grid scenario IS the
// stored CONTAM result.

import library from '@data/scenario_library.json'

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
// temperature and wind weights, then add the outdoor contribution via the
// CONTA penetration fraction (outdoor indoor = CONTA/100 * outdoor_NO2).
//   temps: [[token, weight], ...]   winds: [[token, weight], ...]
export function weightedExposure({ house, hood, use, win, oc, temps, winds, outdoorNO2 = 0 }) {
  let stoveLong = 0, outdoorLong = 0, stoveHrW = 0
  let stoveHrMax = 0, stove8Max = 0, peakMax = 0, wsum = 0
  for (const [t, tw] of temps) {
    for (const [wd, ww] of winds) {
      const c = tw * ww
      if (!c) continue
      const r = lookup(house, hood, use, win, t, wd, oc)
      stoveLong += r.no2.dayavg * c
      outdoorLong += (c * outdoorNO2 * r.conta.dayavg) / 100
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
