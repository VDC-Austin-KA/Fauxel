// Render wrangler.toml from wrangler.template.toml. Usage: FAUXEL_DOMAIN=ackerworks.com node render-config.mjs
// FAUXEL_MEDIA=local: no R2 (media stays in the PC's storage behind the tunnel): drops the /media/* route and the R2 binding.
import { readFileSync, writeFileSync } from "node:fs";
const domain = (process.env.FAUXEL_DOMAIN || "ackerworks.com").trim().toLowerCase();
if (!/^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$/.test(domain)) {
  console.error(`Invalid FAUXEL_DOMAIN: ${domain}`); process.exit(1);
}
const local = (process.env.FAUXEL_MEDIA || "").trim().toLowerCase() === "local";
const here = new URL(".", import.meta.url);
let out = readFileSync(new URL("wrangler.template.toml", here), "utf8").replaceAll("${FAUXEL_DOMAIN}", domain);
if (local) out = out.replace(/^\s*\{ pattern = "[^"]*\/media\/\*".*\r?\n/m, "").replace(/\[\[r2_buckets\]\][^[]*?bucket_name = "fauxel-media"\r?\n/, "");
writeFileSync(new URL("wrangler.toml", here), out);
console.log(`wrangler.toml rendered for ${domain}${local ? " (FAUXEL_MEDIA=local: no R2, /api/* only)" : ""}`);
