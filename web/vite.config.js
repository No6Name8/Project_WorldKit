import { defineConfig } from "vite";

// The viewer (../viewer) is copied to public/viewer by scripts/copy-viewer.mjs; the shared design tokens are imported from ../viewer/assets.
export default defineConfig({
  base: "/",
  server: { fs: { allow: [".."] } },
  build: { target: "es2020", sourcemap: false },
});
