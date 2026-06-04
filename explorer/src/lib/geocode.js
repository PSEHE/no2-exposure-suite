// Resolve a user's address or ZIP to a 5-digit ZIP code.
//
// - A 5-digit ZIP is used directly (no network call).
// - A free-text address is geocoded with OpenStreetMap's Nominatim (CORS-
//   enabled, no key). Nominatim asks callers to be polite: we only call on
//   explicit submit (never per-keystroke) and attribute OSM in the UI.
//   For a high-traffic deployment, swap in a dedicated geocoder.

export async function resolveToZip(query) {
  const q = (query || '').trim()
  if (!q) throw new Error('Enter an address or ZIP code.')

  const zipMatch = q.match(/\b(\d{5})\b/)
  // Pure ZIP (or starts with one) → use directly.
  if (/^\d{5}$/.test(q)) return { zip: q, label: null }

  // Otherwise geocode the address.
  const url =
    'https://nominatim.openstreetmap.org/search?format=jsonv2&addressdetails=1&limit=1&countrycodes=us&q=' +
    encodeURIComponent(q)
  let data
  try {
    const r = await fetch(url, { headers: { Accept: 'application/json' } })
    data = await r.json()
  } catch (e) {
    // Network/geocoder unavailable — fall back to any 5-digit ZIP in the text.
    if (zipMatch) return { zip: zipMatch[1], label: null }
    throw new Error('Could not reach the address lookup service. Try entering your ZIP code.')
  }
  if (!data || !data.length) {
    if (zipMatch) return { zip: zipMatch[1], label: null }
    throw new Error('Address not found. Try adding city and state, or enter your ZIP code.')
  }
  const m = data[0]
  const zip = (m.address?.postcode || '').slice(0, 5)
  if (!/^\d{5}$/.test(zip)) {
    if (zipMatch) return { zip: zipMatch[1], label: m.display_name }
    throw new Error('Could not determine a ZIP code for that address.')
  }
  return { zip, label: m.display_name, lat: +m.lat, lon: +m.lon }
}
