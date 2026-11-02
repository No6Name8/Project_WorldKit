// Copies the vanilla three.js viewer into public/viewer (generated, gitignored) so the web app serves it at /viewer/.
import { cpSync, mkdirSync, rmSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, "..", "..", "viewer");
const dst = join(here, "..", "public", "viewer");
if (!existsSync(join(src, "index.html"))) {
  console.error(`viewer not found at ${src}. Build from a full checkout of the repository (the web app serves ../viewer).`);
  process.exit(1);
}
rmSync(dst, { recursive: true, force: true });
mkdirSync(dst, { recursive: true });
for (const f of ["index.html", "physics.js"]) cpSync(join(src, f), join(dst, f));
for (const d of ["assets", "vendor"]) cpSync(join(src, d), join(dst, d), { recursive: true });
console.log("viewer copied to public/viewer");
