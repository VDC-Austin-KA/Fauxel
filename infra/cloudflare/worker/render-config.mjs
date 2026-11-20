// Render wrangler.toml from wrangler.template.toml. Usage: FAUXEL_DOMAIN=ackerworks.com node render-config.mjs
import { readFileSync, writeFileSync } from "node:fs";
const domain = (process.env.FAUXEL_DOMAIN || "ackerworks.com").trim().toLowerCase();
if (!/^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$/.test(domain)) {
  console.error(`Invalid FAUXEL_DOMAIN: ${domain}`); process.exit(1);
}
const here = new URL(".", import.meta.url);
const out = readFileSync(new URL("wrangler.template.toml", here), "utf8").replaceAll("${FAUXEL_DOMAIN}", domain);
writeFileSync(new URL("wrangler.toml", here), out);
console.log(`wrangler.toml rendered for ${domain}`);
