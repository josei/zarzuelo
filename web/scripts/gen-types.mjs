// Genera src/api/types.ts desde los JSON Schema que exporta `kb export` (models.py = fuente única).
import { compileFromFile } from "json-schema-to-typescript";
import { writeFileSync, existsSync } from "node:fs";

const dir = "public/api/v1/schema";
if (!existsSync(dir)) {
  console.error("Falta public/api/v1/schema: ejecuta `kb export` antes.");
  process.exit(1);
}
let out = "/* Generado por scripts/gen-types.mjs. No editar a mano. */\n";
out += await compileFromFile(`${dir}/api.schema.json`, { bannerComment: "", additionalProperties: false });
writeFileSync("src/api/types.ts", out);
