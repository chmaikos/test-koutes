/**
 * Asset generation for vite-plugin-pwa.
 *
 * Reads ``public/favicon.svg`` and emits the icon set the webmanifest +
 * iOS Add-to-Home-Screen need (favicon.ico, pwa-*.png, maskable-*.png,
 * apple-touch-icon-*.png) into ``public/``. Run with
 * ``npm run generate:pwa-assets`` whenever the source SVG changes; the
 * generated PNGs are committed so the production build doesn't depend
 * on the generator running in CI.
 */
import {
  defineConfig,
  minimal2023Preset as preset,
} from "@vite-pwa/assets-generator/config";

export default defineConfig({
  preset,
  images: ["public/favicon.svg"],
});
