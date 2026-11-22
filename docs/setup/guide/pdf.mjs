import { chromium } from "../../../apps/web-portal/node_modules/playwright-core/index.mjs";
const b = await chromium.launch({ executablePath: "/usr/bin/google-chrome", args: ["--no-sandbox"] });
const page = await b.newPage();
await page.goto("file://" + new URL("./guide.html", import.meta.url).pathname + "", { waitUntil: "networkidle" });
await page.pdf({ path: "" + new URL("../Fauxel-Setup-Guide.pdf", import.meta.url).pathname + "", format: "Letter", printBackground: true, preferCSSPageSize: true,
  displayHeaderFooter: true, headerTemplate: "<span></span>",
  footerTemplate: '<div style="width:100%;font-family:Inter,Arial;font-size:7.5px;color:#94a3b8;padding:0 0.6in;display:flex;justify-content:space-between"><span>Fauxel Setup Guide · Navisworks, Revit &amp; fauxel.ackerworks.com</span><span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>' });
await b.close();
