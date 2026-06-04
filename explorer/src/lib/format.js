// Small formatting helpers.

export function fmt(x, dp = 1) {
  if (x == null || Number.isNaN(x)) return '—'
  if (x === 0) return '0'
  if (x < 1) return x.toFixed(2)
  if (x < 10) return x.toFixed(1)
  return x.toFixed(0)
}

// Round to 2 significant figures (matches the prior widget's display).
export function sig2(x) {
  if (!x) return 0
  const r = Number(x.toPrecision(2))
  return r
}

export function times(x, ref) {
  if (!ref) return '—'
  return (x / ref).toFixed(1)
}
