<script>
  import archetypes from '@data/archetypes.json'
  import { weightedExposure, HOUSES } from '@lib/engine.js'
  import { simulateDay, airExchange } from '@lib/boxmodel.js'
  import {
    HOOD_OPTIONS, USE_OPTIONS, WINDOW_OPTIONS, OCCUPANCY_OPTIONS, TEMP_OPTIONS,
    TYPE_GROUPS, NATIONAL_WIND_WEIGHTS, DEFAULT_OUTDOOR_PPB, BENCHMARKS,
  } from '@lib/constants.js'
  import { fmt, times } from '@lib/format.js'
  import Segmented from './components/Segmented.svelte'
  import BenchmarkBar from './components/BenchmarkBar.svelte'
  import TimeSeries from './components/TimeSeries.svelte'
  import HouseViz from './components/HouseViz.svelte'

  // --- controls (state) ---
  let house = $state('DH-1')
  let hood = $state('NoHood')
  let use = $state('med')
  let win = $state('moderate')
  let oc = $state('median')
  let temp = $state('RT')
  let outdoor = $state(DEFAULT_OUTDOOR_PPB)
  let advanced = $state(false)

  // --- home picker options, grouped by type, with a friendly size label ---
  function sizeLabel(v) {
    if (v == null) return ''
    if (v < 350) return 'compact'
    if (v < 550) return 'small'
    if (v < 800) return 'medium'
    return 'large'
  }
  const grouped = TYPE_GROUPS.map((g) => ({
    label: g.label,
    homes: HOUSES.filter((h) => archetypes[h].type === g.type).map((h) => ({
      id: h,
      text: `${sizeLabel(archetypes[h].total_volume_m3)} · ${Math.round(archetypes[h].total_volume_m3)} m³ (${h})`,
    })),
  }))

  let arche = $derived(archetypes[house])
  let kvol = $derived(arche?.kitchen_volume_m3 ?? 30)

  const winds = Object.entries(NATIONAL_WIND_WEIGHTS)

  // --- fine-tune (continuous) knobs; 1.0 = exact CONTAM scenario ---
  let cookMult = $state(1) // cooking amount × default
  let volMult = $state(1) // kitchen size × default
  let airMult = $state(1) // ventilation × default
  let showFineTune = $state(false)
  const DECAY = 0.86
  // Physically-motivated multiplier on the STOVE component. For a well-mixed
  // zone the steady-state concentration ∝ emission, ∝ 1/volume, ∝ 1/(air-
  // exchange + decay). Equals 1 at default knob positions, so we stay exactly
  // on the CONTAM value and scale physically off-grid.
  let lambda0 = $derived(airExchange(win, temp, 'BREEZE'))
  let stoveMult = $derived(
    cookMult * (1 / volMult) * ((lambda0 + DECAY) / (lambda0 * airMult + DECAY))
  )
  let tuned = $derived(cookMult !== 1 || volMult !== 1 || airMult !== 1)

  // Personal exposure (chosen time-in-kitchen) and kitchen-air metrics
  // (high-kitchen-occupancy scenario, where personal exposure ≈ kitchen air).
  let exp = $derived(
    weightedExposure({ house, hood, use, win, oc, temps: [[temp, 1]], winds, outdoorNO2: outdoor })
  )
  let kitchenExp = $derived(
    weightedExposure({ house, hood, use, win, oc: 'ninetyfifth_kitchen', temps: [[temp, 1]], winds, outdoorNO2: outdoor })
  )

  // --- headline values (stove component scaled by the fine-tune multiplier) ---
  let stoveLong = $derived(exp.stoveLong * stoveMult)
  let annual = $derived(stoveLong + exp.outdoorLong)
  let worstHr = $derived(kitchenExp.stoveHrMax * stoveMult + kitchenExp.outdoorLong)
  let whoX = $derived(annual / BENCHMARKS.whoAnnual)

  // --- 24-h kitchen time-series, anchored to the exact (tuned) kitchen peak ---
  let ts = $derived(
    simulateDay({
      use, hood, win, temp, wind: 'BREEZE',
      kitchenVol: kvol * volMult, outdoorNO2: outdoor, peakAnchor: kitchenExp.peakMax * stoveMult,
    })
  )

  // --- "what helps" interventions: resulting annual ppb + % reduction ---
  let whatHelps = $derived.by(() => {
    const annualOf = (mods) => {
      const e = weightedExposure({
        house, hood: mods.hood ?? hood, use: mods.use ?? use, win: mods.win ?? win,
        oc, temps: [[temp, 1]], winds, outdoorNO2: outdoor,
      })
      const sm = mods.use === 'zero' ? 0 : stoveMult
      return e.stoveLong * sm + e.outdoorLong
    }
    const base = annual
    const candidates = []
    if (use !== 'zero') {
      if (hood !== '75CE') candidates.push({ label: 'Use a high-efficiency hood', val: annualOf({ hood: '75CE' }) })
      if (win !== 'open') candidates.push({ label: 'Keep a window open', val: annualOf({ win: 'open' }) })
      candidates.push({ label: 'Switch to an electric stove', val: annualOf({ use: 'zero' }) })
    }
    return candidates
      .map((c) => ({ ...c, cut: base > 0 ? (base - c.val) / base : 0 }))
      .filter((c) => c.cut > 0.005)
      .sort((a, b) => b.cut - a.cut)
  })

  function resetFineTune() { cookMult = 1; volMult = 1; airMult = 1 }

  // Peak concentration by room for the house cutaway (kitchen highest).
  let rooms = $derived.by(() => {
    const k = Math.max(...ts.kitchen)
    const b = Math.max(...ts.bedroom)
    return { kitchen: k, living: b + (k - b) * 0.5, bedroom: b }
  })

  // --- plain-language interpretation ---
  let interp = $derived.by(() => {
    const out = []
    if (use === 'zero') {
      out.push('With the stove off, your indoor NO₂ comes only from outdoor air seeping in.')
    } else {
      const sharePct = annual > 0 ? Math.round((stoveLong / annual) * 100) : 0
      out.push(`Your gas stove adds about ${fmt(stoveLong)} ppb of long-term NO₂ — roughly ${sharePct}% of the ${fmt(annual)} ppb you breathe at home on a typical day.`)
    }
    if (annual > BENCHMARKS.whoAnnual) {
      out.push(`That exceeds the WHO annual guideline (${fmt(BENCHMARKS.whoAnnual)} ppb) by ${whoX.toFixed(1)}×.`)
    } else {
      out.push(`That stays below the WHO annual guideline (${fmt(BENCHMARKS.whoAnnual)} ppb).`)
    }
    if (worstHr > BENCHMARKS.epa1hr) {
      out.push(`During cooking, kitchen NO₂ can exceed the 1-hour health benchmark of 100 ppb (peak ≈ ${fmt(worstHr)} ppb).`)
    }
    return out
  })
</script>

<div class="page">
  <header class="head">
    <div class="head-inner">
      <h1>Gas Stove NO₂ Explorer</h1>
      <p class="sub">See how cooking, ventilation, and your home shape the nitrogen dioxide you breathe — using the multizone CONTAM model behind Kashtan et al. (<a href="https://www.science.org/doi/10.1126/sciadv.adm8680" target="_blank" rel="noopener">Sci. Adv. 2024</a>, <a href="https://doi.org/10.1093/pnasnexus/pgaf341" target="_blank" rel="noopener">PNAS Nexus 2025</a>).</p>
    </div>
  </header>

  <main class="grid">
    <!-- ===================== CONTROLS ===================== -->
    <section class="card controls">
      <h2 class="ctitle">Your home &amp; habits</h2>

      <div class="field">
        <div class="seg-label">Home type</div>
        <select bind:value={house}>
          {#each grouped as g}
            <optgroup label={g.label}>
              {#each g.homes as h}<option value={h.id}>{g.label} — {h.text}</option>{/each}
            </optgroup>
          {/each}
        </select>
        <div class="kv">Kitchen ≈ {fmt(kvol)} m³ · whole home ≈ {fmt(arche?.total_volume_m3)} m³</div>
      </div>

      <Segmented label="Stove use" options={USE_OPTIONS} bind:value={use} />
      <Segmented label="Range hood" options={HOOD_OPTIONS} bind:value={hood} />
      <Segmented label="Windows" options={WINDOW_OPTIONS} bind:value={win} />
      <Segmented label="Time in kitchen" options={OCCUPANCY_OPTIONS} bind:value={oc} />

      <div class="field">
        <div class="seg-head">
          <span class="seg-label">Outdoor NO₂ near you</span>
          <span class="seg-hint">{fmt(outdoor)} ppb · set automatically by address soon</span>
        </div>
        <input type="range" min="0" max="35" step="0.5" bind:value={outdoor} />
      </div>

      <button class="adv-toggle" onclick={() => (advanced = !advanced)}>
        {advanced ? '▾' : '▸'} Weather
      </button>
      {#if advanced}
        <Segmented label="Outdoor temperature" options={TEMP_OPTIONS} bind:value={temp} />
        <p class="note">Wind is averaged over a national distribution. Colder weather and wind increase air exchange, which lowers indoor buildup.</p>
      {/if}

      <button class="adv-toggle" onclick={() => (showFineTune = !showFineTune)}>
        {showFineTune ? '▾' : '▸'} Fine-tune {#if tuned}<span class="tuned-dot" title="adjusted"></span>{/if}
      </button>
      {#if showFineTune}
        <p class="note">Adjust continuously around the exact CONTAM scenario (physical scaling).</p>
        <div class="slider">
          <div class="srow"><span>Cooking amount</span><span class="sval">{cookMult.toFixed(2)}×</span></div>
          <input type="range" min="0.25" max="3" step="0.05" bind:value={cookMult} />
        </div>
        <div class="slider">
          <div class="srow"><span>Kitchen size</span><span class="sval">{volMult.toFixed(2)}×</span></div>
          <input type="range" min="0.5" max="2.5" step="0.05" bind:value={volMult} />
        </div>
        <div class="slider">
          <div class="srow"><span>Ventilation</span><span class="sval">{airMult.toFixed(2)}×</span></div>
          <input type="range" min="0.5" max="3" step="0.05" bind:value={airMult} />
        </div>
        {#if tuned}<button class="reset" onclick={resetFineTune}>Reset to CONTAM defaults</button>{/if}
      {/if}
    </section>

    <!-- ===================== RESULTS ===================== -->
    <section class="results">
      <div class="heroes">
        <div class="card hero">
          <div class="hlabel">Average NO₂ you breathe at home</div>
          <div class="hvalue tnum" class:over={annual > BENCHMARKS.whoAnnual}>
            {fmt(annual)}<span class="unit">ppb</span>
          </div>
          <div class="hsub">
            {#if annual > BENCHMARKS.whoAnnual}
              <span class="pill bad">{whoX.toFixed(1)}× WHO guideline</span>
            {:else}
              <span class="pill good">below WHO guideline</span>
            {/if}
          </div>
        </div>
        <div class="card hero">
          <div class="hlabel">Worst 1-hour during cooking</div>
          <div class="hvalue tnum" class:over={worstHr > BENCHMARKS.epa1hr}>
            {fmt(worstHr)}<span class="unit">ppb</span>
          </div>
          <div class="hsub">
            {#if worstHr > BENCHMARKS.epa1hr}
              <span class="pill bad">over EPA/WHO 1-hr (100)</span>
            {:else}
              <span class="pill good">under EPA/WHO 1-hr (100)</span>
            {/if}
          </div>
        </div>
      </div>

      <div class="card block">
        <div class="block-title">Long-term exposure vs. health guidelines</div>
        <BenchmarkBar stove={stoveLong} outdoor={exp.outdoorLong} />
      </div>

      {#if whatHelps.length}
        <div class="card block">
          <div class="block-title">What would lower it</div>
          <div class="helps">
            {#each whatHelps as h}
              <div class="help-row">
                <span class="help-label">{h.label}</span>
                <span class="help-bar"><span class="help-fill" style="width:{Math.round((1 - h.cut) * 100)}%"></span></span>
                <span class="help-val tnum">{fmt(h.val)} ppb</span>
                <span class="help-cut">−{Math.round(h.cut * 100)}%</span>
              </div>
            {/each}
          </div>
        </div>
      {/if}

      <div class="card block">
        <div class="block-title">A day of kitchen NO₂</div>
        <TimeSeries data={ts} />
      </div>

      <div class="card block">
        <div class="block-title">How NO₂ spreads through the home</div>
        <HouseViz kitchen={rooms.kitchen} living={rooms.living} bedroom={rooms.bedroom} />
      </div>

      <div class="card block interp">
        {#each interp as line}<p>{line}</p>{/each}
      </div>
    </section>
  </main>

  <footer class="foot">
    Estimates use exact CONTAM model results for the chosen scenario; the 24-hour curve is an
    anchored physical illustration. Built on Kashtan et al. 2024/2025. ·
    <span class="muted">Drafted by Claude with prompts engineered by Yannai Kashtan</span>
  </footer>
</div>

<style>
  .page { max-width: 1120px; margin: 0 auto; padding: 0 20px 48px; }
  .head { padding: 28px 0 18px; }
  .head-inner { max-width: 760px; }
  h1 { font-size: 27px; }
  .sub { color: var(--ink-2); margin-top: 8px; font-size: 14.5px; }

  .grid { display: grid; grid-template-columns: 366px 1fr; gap: 18px; align-items: start; }
  @media (max-width: 880px) { .grid { grid-template-columns: 1fr; } }

  .controls { padding: 20px; position: sticky; top: 16px; }
  @media (max-width: 880px) { .controls { position: static; } }
  .ctitle { font-size: 15px; margin-bottom: 16px; color: var(--ink); }
  .field { margin-bottom: 16px; }
  .seg-label { font-weight: 600; font-size: 13.5px; margin-bottom: 6px; display: block; }
  .seg-head { display: flex; align-items: baseline; gap: 8px; margin-bottom: 6px; flex-wrap: wrap; }
  .seg-hint { font-size: 12px; color: var(--muted); }
  .kv { font-size: 12px; color: var(--muted); margin-top: 6px; }
  .adv-toggle {
    background: none; border: none; color: var(--accent); cursor: pointer;
    font-size: 13px; font-weight: 600; padding: 4px 0; margin-top: 2px;
  }
  .note { font-size: 12px; color: var(--muted); margin-top: 4px; }
  .tuned-dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: var(--accent); margin-left: 2px; vertical-align: middle; }
  .slider { margin: 10px 0; }
  .srow { display: flex; justify-content: space-between; font-size: 12.5px; color: var(--ink-2); margin-bottom: 5px; }
  .sval { font-variant-numeric: tabular-nums; color: var(--accent); font-weight: 600; }
  .reset { background: none; border: 1px solid var(--line-2); border-radius: 8px; color: var(--ink-2);
    cursor: pointer; font-size: 12px; padding: 6px 10px; margin-top: 8px; }
  .reset:hover { border-color: var(--accent); color: var(--accent); }

  .results { display: flex; flex-direction: column; gap: 18px; }
  .heroes { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
  @media (max-width: 520px) { .heroes { grid-template-columns: 1fr; } }
  .hero { padding: 18px 20px; }
  .hlabel { font-size: 12.5px; color: var(--ink-2); font-weight: 600; }
  .hvalue { font-size: 46px; font-weight: 720; line-height: 1.05; margin-top: 6px; color: var(--good); }
  .hvalue.over { color: var(--bad); }
  .unit { font-size: 18px; font-weight: 600; color: var(--muted); margin-left: 6px; }
  .hsub { margin-top: 8px; }
  .pill { font-size: 12px; font-weight: 650; padding: 3px 9px; border-radius: 999px; }
  .pill.bad { background: #fdecea; color: var(--bad); }
  .pill.good { background: #e9f6ee; color: var(--good); }

  .block { padding: 18px 20px; }
  .block-title { font-size: 13.5px; font-weight: 650; margin-bottom: 16px; color: var(--ink); }
  .interp { display: flex; flex-direction: column; gap: 8px; }
  .interp p { font-size: 14px; color: var(--ink-2); }

  .helps { display: flex; flex-direction: column; gap: 12px; }
  .help-row { display: grid; grid-template-columns: 1fr 90px auto auto; align-items: center; gap: 12px; font-size: 13.5px; }
  .help-label { color: var(--ink); }
  .help-bar { height: 8px; background: var(--surface-2); border-radius: 5px; overflow: hidden; border: 1px solid var(--line); }
  .help-fill { display: block; height: 100%; background: var(--good); transition: width .35s cubic-bezier(.4,0,.2,1); }
  .help-val { color: var(--ink-2); font-size: 12.5px; min-width: 56px; text-align: right; }
  .help-cut { color: var(--good); font-weight: 650; font-size: 13px; min-width: 44px; text-align: right; }
  @media (max-width: 520px) {
    .help-row { grid-template-columns: 1fr auto auto; }
    .help-bar { display: none; }
  }

  .foot { margin-top: 26px; font-size: 11.5px; color: var(--muted); text-align: center; line-height: 1.6; }
</style>
