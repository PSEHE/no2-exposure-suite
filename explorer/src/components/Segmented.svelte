<script>
  // A labeled segmented-control. options: [{token, label, hint}]
  let { label, options, value = $bindable(), hint = '' } = $props()
  let active = $derived(options.find((o) => o.token === value))
</script>

<div class="seg-field">
  <div class="seg-head">
    <span class="seg-label">{label}</span>
    {#if active?.hint}<span class="seg-hint">{active.hint}</span>{/if}
  </div>
  <div class="seg" role="radiogroup" aria-label={label}>
    {#each options as o}
      <button
        type="button"
        class="seg-btn"
        class:active={o.token === value}
        aria-pressed={o.token === value}
        onclick={() => (value = o.token)}
      >{o.label}</button>
    {/each}
  </div>
</div>

<style>
  .seg-field { margin-bottom: 16px; }
  .seg-head { display: flex; align-items: baseline; gap: 8px; margin-bottom: 6px; flex-wrap: wrap; }
  .seg-label { font-weight: 600; font-size: 13.5px; }
  .seg-hint { font-size: 12px; color: var(--muted); }
  .seg {
    display: flex; flex-wrap: wrap; gap: 4px; background: var(--surface-2);
    padding: 4px; border-radius: 11px; border: 1px solid var(--line);
  }
  .seg-btn {
    flex: 1 1 auto; min-width: 52px; border: none; background: transparent; cursor: pointer;
    padding: 8px 6px; border-radius: 8px; font-size: 13px; color: var(--ink-2);
    transition: background .12s, color .12s, box-shadow .12s; white-space: nowrap;
  }
  .seg-btn:hover { color: var(--ink); }
  .seg-btn.active {
    background: var(--surface); color: var(--accent);
    font-weight: 600; box-shadow: 0 1px 3px rgba(20,30,40,.12);
  }
</style>
