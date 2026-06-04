// Per-ZIP lookup over the bundled zip_data.json (outdoor NO2, climate, wind,
// and a default archetype). Bundled into the single-file build so the address
// feature works fully client-side (only geocoding needs the network).
import zipTable from '@data/zip_data.json'

export function zipLookup(zip) {
  return zipTable[String(zip)] ?? null
}
