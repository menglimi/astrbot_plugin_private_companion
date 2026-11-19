// Build the single-file voxel controller from the ES-module sources.
//
//   npm install --no-save esbuild@0.25.0
//   node build.mjs            # writes ../voxel-room.js
//   node build.mjs --check    # rebuilds to a sibling file and compares bytes
//
// Three.js and OrbitControls are bundled from ../vendor, so the output is a
// classic IIFE that the Dashboard can load with <script defer>. The page never
// talks to a CDN and needs no runtime dependency.
import { build } from "esbuild";
import { createHash } from "node:crypto";
import { readFile, unlink, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(fileURLToPath(import.meta.url));
const entry = path.join(root, "panels", "home-room-3d.source.js");
const target = path.resolve(root, "..", "voxel-room.js");
const check = process.argv.includes("--check");
const output = check ? `${target}.check` : target;

const options = {
  entryPoints: [entry],
  outfile: output,
  bundle: true,
  minify: true,
  format: "iife",
  target: "es2020",
  charset: "utf8",
  legalComments: "eof",
  logLevel: "info",
};

if (!check) {
  await build(options);
  const bytes = await readFile(output);
  console.log(`built ${path.relative(root, output)} (${(bytes.length / 1024).toFixed(1)} KB)`);
  process.exit(0);
}

await build(options);
const [actual, expected] = await Promise.all([readFile(output), readFile(target)]);
await unlink(output);
const digest = value => createHash("sha256").update(value).digest("hex");
if (digest(actual) !== digest(expected)) {
  console.error("voxel-room.js is stale: run `node build.mjs`");
  console.error(`  source=${digest(actual)}  output=${digest(expected)}`);
  process.exit(1);
}
console.log("voxel-room.js matches the current sources");
