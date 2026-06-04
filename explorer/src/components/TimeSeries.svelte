<script>
  import { scaleLinear, line, area, curveMonotoneX, max as d3max } from 'd3'
  import { BENCHMARKS } from '@lib/constants.js'

  let { data } = $props() // {hours, kitchen, bedroom, cooking}

  const W = 660, H = 300
  const m = { top: 18, right: 16, bottom: 30, left: 44 }
  const iw = W - m.left - m.right
  const ih = H - m.top - m.bottom

  let x = $derived(scaleLinear().domain([0, 24]).range([0, iw]))
  // Adaptive y-axis: scale to the curve (with headroom), min ceiling 30 ppb.
  let yMax = $derived(Math.max((d3max(data.kitchen) ?? 1) * 1.3, 30))
  let y = $derived(scaleLinear().domain([0, yMax]).range([ih, 0]).nice())
  // Only draw the 1-hr benchmark line when it falls within view.
  let showBench = $derived(BENCHMARKS.epa1hr <= y.domain()[1])

  const mkLine = (arr, xs, ys) =>
    line().x((_, i) => xs(data.hours[i])).y((d) => ys(d)).curve(curveMonotoneX)(arr)
  const mkArea = (arr, xs, ys) =>
    area().x((_, i) => xs(data.hours[i])).y0(ys(0)).y1((d) => ys(d)).curve(curveMonotoneX)(arr)

  let kitchenPath = $derived(mkLine(data.kitchen, x, y))
  let kitchenArea = $derived(mkArea(data.kitchen, x, y))
  let bedroomPath = $derived(mkLine(data.bedroom, x, y))

  // Cooking periods -> shaded bands [{x0,x1}]
  let bands = $derived.by(() => {
    const out = []
    let start = null
    for (let i = 0; i < data.cooking.length; i++) {
      if (data.cooking[i] && start === null) start = data.hours[i]
      if (!data.cooking[i] && start !== null) { out.push({ x0: start, x1: data.hours[i] }); start = null }
    }
    if (start !== null) out.push({ x0: start, x1: 24 })
    return out
  })

  const xticks = [0, 6, 12, 18, 24]
  const xlab = { 0: '12a', 6: '6a', 12: '12p', 18: '6p', 24: '12a' }
  let yticks = $derived(y.ticks(4))
</script>

<div class="ts">
  <svg viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMid meet" role="img"
       aria-label="24-hour kitchen NO2 concentration">
    <g transform="translate({m.left},{m.top})">
      <!-- cooking bands -->
      {#each bands as b}
        <rect class="band" x={x(b.x0)} y={0} width={Math.max(2, x(b.x1) - x(b.x0))} height={ih} />
      {/each}

      <!-- gridlines + y labels -->
      {#each yticks as t}
        <line class="grid" x1={0} x2={iw} y1={y(t)} y2={y(t)} />
        <text class="ylab" x={-8} y={y(t)} dy="0.32em" text-anchor="end">{t}</text>
      {/each}
      <text class="axis-title" transform="translate({-32},{ih / 2}) rotate(-90)" text-anchor="middle">NO₂ (ppb)</text>

      <!-- benchmark line (only when in view) -->
      {#if showBench}
        <line class="bench epa" x1={0} x2={iw} y1={y(BENCHMARKS.epa1hr)} y2={y(BENCHMARKS.epa1hr)} />
        <text class="bench-lab" x={iw} y={y(BENCHMARKS.epa1hr) - 4} text-anchor="end">EPA / WHO 1-hr (100)</text>
      {:else}
        <text class="bench-lab off" x={iw} y={2} text-anchor="end">well below 1-hr benchmark (100 ppb)</text>
      {/if}

      <!-- curves -->
      <path class="karea" d={kitchenArea} />
      <path class="bedroom" d={bedroomPath} />
      <path class="kitchen" d={kitchenPath} />

      <!-- x axis -->
      {#each xticks as t}
        <text class="xlab" x={x(t)} y={ih + 20} text-anchor="middle">{xlab[t]}</text>
      {/each}
    </g>
  </svg>
  <div class="legend">
    <span class="key"><i class="ln kitchen"></i>Kitchen</span>
    <span class="key"><i class="ln bedroom"></i>Bedroom</span>
    <span class="key"><i class="sw band"></i>Cooking</span>
  </div>
</div>

<style>
  .ts { width: 100%; }
  svg { width: 100%; height: auto; display: block; }
  .band { fill: var(--stove); opacity: .10; }
  .grid { stroke: var(--line); stroke-width: 1; }
  .ylab, .xlab { fill: var(--muted); font-size: 11px; }
  .axis-title { fill: var(--muted); font-size: 11px; }
  .bench { stroke: var(--bad); stroke-width: 1.5; stroke-dasharray: 5 4; opacity: .8; }
  .bench-lab { fill: var(--bad); font-size: 10.5px; opacity: .9; }
  .bench-lab.off { fill: var(--muted); }
  .karea { fill: var(--stove); opacity: .12; }
  .kitchen { fill: none; stroke: var(--stove); stroke-width: 2.4; }
  .bedroom { fill: none; stroke: var(--outdoor); stroke-width: 2; stroke-dasharray: 4 4; }
  .legend { display: flex; gap: 16px; font-size: 12.5px; color: var(--ink-2); margin-top: 4px; }
  .key { display: inline-flex; align-items: center; gap: 6px; }
  .ln { width: 16px; height: 0; border-top: 3px solid; display: inline-block; }
  .ln.kitchen { border-color: var(--stove); }
  .ln.bedroom { border-color: var(--outdoor); border-top-style: dashed; }
  .sw.band { width: 12px; height: 12px; border-radius: 3px; background: var(--stove); opacity: .2; display: inline-block; }
</style>
