<script>
  import { BENCHMARKS } from '@lib/constants.js'
  import { fmt } from '@lib/format.js'

  // Long-term exposure split into stove + outdoor, against annual benchmarks.
  let { stove = 0, outdoor = 0 } = $props()
  let total = $derived(stove + outdoor)

  const who = BENCHMARKS.whoAnnual
  const canada = BENCHMARKS.canadaAnnual

  // Scale so the bar always shows WHO + Canada markers and the value with headroom.
  let max = $derived(Math.max(total * 1.15, canada * 1.25, who * 2.2))
  const pct = (v, m) => `${Math.min(100, (v / m) * 100)}%`
</script>

<div class="bb">
  <div class="track">
    <div class="seg stove" style="width:{pct(stove, max)}"></div>
    <div class="seg outdoor" style="width:{pct(outdoor, max)}"></div>
    <!-- benchmark markers -->
    <div class="mark who" style="left:{pct(who, max)}">
      <span class="mlabel">WHO {fmt(who)}</span>
    </div>
    <div class="mark canada" style="left:{pct(canada, max)}">
      <span class="mlabel">Canada {fmt(canada)}</span>
    </div>
  </div>
  <div class="legend">
    <span class="key"><i class="sw stove"></i>Stove {fmt(stove)}</span>
    <span class="key"><i class="sw outdoor"></i>Outdoor {fmt(outdoor)}</span>
    <span class="key total">Total {fmt(total)} ppb</span>
  </div>
</div>

<style>
  .bb { width: 100%; }
  .track {
    position: relative; display: flex; height: 30px;
    background: var(--surface-2); border-radius: 8px; overflow: visible;
    border: 1px solid var(--line);
  }
  .seg { height: 100%; transition: width .35s cubic-bezier(.4,0,.2,1); }
  .seg.stove { background: var(--stove); border-radius: 8px 0 0 8px; }
  .seg.outdoor { background: var(--outdoor); }
  .seg.stove:only-child, .seg.outdoor:last-child { border-radius: 0 8px 8px 0; }
  .mark {
    position: absolute; top: -6px; bottom: -6px; width: 2px;
    background: var(--ink); opacity: .55;
  }
  .mark.who { background: var(--good); opacity: .9; }
  .mark.canada { background: var(--warn); opacity: .9; }
  .mlabel {
    position: absolute; top: -18px; left: 50%; transform: translateX(-50%);
    font-size: 10.5px; white-space: nowrap; color: var(--ink-2); font-weight: 600;
  }
  .legend {
    display: flex; gap: 16px; margin-top: 26px; font-size: 12.5px;
    color: var(--ink-2); flex-wrap: wrap; align-items: center;
  }
  .key { display: inline-flex; align-items: center; gap: 6px; }
  .key.total { margin-left: auto; font-weight: 650; color: var(--ink); }
  .sw { width: 11px; height: 11px; border-radius: 3px; display: inline-block; }
  .sw.stove { background: var(--stove); }
  .sw.outdoor { background: var(--outdoor); }
</style>
