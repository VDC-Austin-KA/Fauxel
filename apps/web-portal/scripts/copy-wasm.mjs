// Copies web-ifc.wasm into public/wasm so the IFC viewer can load it from the same origin.
import { copyFileSync, mkdirSync, existsSync } from "node:fs";
const src = "node_modules/web-ifc/web-ifc.wasm";
if (existsSync(src)) {
  mkdirSync("public/wasm", { recursive: true });
  copyFileSync(src, "public/wasm/web-ifc.wasm");
  console.log("copied web-ifc.wasm -> public/wasm/");
} else {
  console.warn("web-ifc.wasm not found; IFC viewing will fall back to the box model");
}
