<script>
  import { interpolateYlOrRd } from 'd3'
  import { fmt } from '@lib/format.js'

  // Abstract cutaway of a home showing how NO2 concentrates near the kitchen
  // and falls off in other rooms (the multizone idea, made visible).
  let { kitchen = 0, living = 0, bedroom = 0 } = $props()

  // Color ramp: 0 -> pale, 100 ppb (1-hr benchmark) -> deep red.
  const col = (v) => interpolateYlOrRd(0.12 + 0.88 * Math.min(1, v / 100))
  const ink = (v) => (v > 55 ? '#fff' : 'var(--ink)')

  const W = 660, H = 230
  const rooms = $derived([
    { name: 'Kitchen', v: kitchen, x: 30, w: 230, stove: true },
    { name: 'Living room', v: living, x: 260, w: 210 },
    { name: 'Bedroom', v: bedroom, x: 470, w: 160 },
  ])
  const bodyY = 70, bodyH = 120
</script>

<div class="hv">
  <svg viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="NO2 by room">
    <!-- roof -->
    <polygon points="14,72 330,18 646,72" fill="var(--surface-2)" stroke="var(--line-2)" />
    <!-- rooms -->
    {#each rooms as r}
      <g>
        <rect x={r.x} y={bodyY} width={r.w} height={bodyH} fill={col(r.v)}
              stroke="var(--line-2)" rx="3" />
        {#if r.stove}
          <!-- simple stove glyph -->
          <rect x={r.x + 16} y={bodyY + bodyH - 34} width="34" height="22" rx="3"
                fill="none" stroke={ink(r.v)} stroke-width="1.5" opacity="0.8" />
          <circle cx={r.x + 25} cy={bodyY + bodyH - 23} r="3" fill={ink(r.v)} opacity="0.8" />
          <circle cx={r.x + 41} cy={bodyY + bodyH - 23} r="3" fill={ink(r.v)} opacity="0.8" />
        {/if}
        <text x={r.x + r.w / 2} y={bodyY + 26} text-anchor="middle"
              fill={ink(r.v)} font-size="13" font-weight="600">{r.name}</text>
        <text x={r.x + r.w / 2} y={bodyY + 50} text-anchor="middle"
              fill={ink(r.v)} font-size="18" font-weight="700" class="tnum">{fmt(r.v)}</text>
        <text x={r.x + r.w / 2} y={bodyY + 66} text-anchor="middle"
              fill={ink(r.v)} font-size="10.5" opacity="0.8">ppb</text>
      </g>
    {/each}
    <!-- floor -->
    <line x1="20" y1={bodyY + bodyH} x2="640" y2={bodyY + bodyH} stroke="var(--line-2)" stroke-width="2" />
  </svg>
  <p class="cap">Peak NO₂ by room — concentrations are highest in the kitchen and fall off toward other rooms as air mixes and decays.</p>
</div>

<style>
  .hv { width: 100%; }
  svg { width: 100%; height: auto; display: block; }
  .cap { font-size: 12px; color: var(--muted); margin-top: 8px; }
</style>
