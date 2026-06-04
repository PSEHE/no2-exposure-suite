import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'
import { viteSingleFile } from 'vite-plugin-singlefile'
import { fileURLToPath, URL } from 'node:url'

// Builds the Explorer into ONE self-contained dist/index.html (all JS/CSS/data
// inlined) so it can be hosted on GitHub Pages or opened directly.
export default defineConfig({
  plugins: [svelte(), viteSingleFile()],
  resolve: {
    alias: {
      // Import the widget-ready JSON produced by `python -m core.export_web_data`
      // without copying it into the explorer source tree.
      '@data': fileURLToPath(new URL('../web_data', import.meta.url)),
      '@lib': fileURLToPath(new URL('./src/lib', import.meta.url)),
    },
  },
  server: {
    fs: { allow: ['..'] }, // allow importing from ../web_data during dev
  },
  // Inline large JSON as a single JSON.parse(...) (no tree-shaking, faster parse).
  json: { stringify: true },
})
