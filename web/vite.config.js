import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { fileURLToPath } from "node:url";

// One plain script per screen part, loaded by the server's HTML pages with a
// <script> tag (no module loader needed on kiosks or in the Windows app).
export default defineConfig({
  plugins: [svelte()],
  build: {
    outDir: fileURLToPath(new URL("../server/atsuit/static/ui", import.meta.url)),
    emptyOutDir: true,
    target: "es2020",
    cssCodeSplit: false,
    lib: {
      entry: fileURLToPath(new URL("src/desk/main.js", import.meta.url)),
      name: "ATDesk",
      formats: ["iife"],
      fileName: () => "desk.js",
      cssFileName: "desk",
    },
  },
});
