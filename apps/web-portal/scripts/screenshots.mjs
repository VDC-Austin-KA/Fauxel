// Usage: npm run build && (npm run preview &) && npm run screenshots
import { chromium } from "playwright-core";
import { existsSync, mkdirSync } from "node:fs";

const base = process.env.BASE ?? "http://localhost:4173";
const exe = process.env.CHROMIUM ?? "/opt/pw-browsers/chromium";
if (!existsSync(exe)) { console.error("No chromium at", exe, "- skipping screenshots"); process.exit(0); }
mkdirSync("docs/screenshots", { recursive: true });

const browser = await chromium.launch({ executablePath: exe, args: ["--no-sandbox", "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
const errors = [];
page.on("console", (m) => { if (m.type() === "error" || m.type() === "warning") errors.push(`[${m.type()}] ${m.text()}`); });
page.on("pageerror", (e) => errors.push("[pageerror] " + e.message));

const shot = async (name, wait = 600) => { await page.waitForTimeout(wait); await page.screenshot({ path: `docs/screenshots/${name}.png` }); console.log("saved", name); };

await page.goto(base + "/");
await page.waitForSelector("main >> text=Bastrop Data Hall 1");
await page.waitForTimeout(800);
await shot("01-portfolio");

await page.click("main >> text=Bastrop Data Hall 1");
await page.waitForSelector("text=Subcontractor pay-app verification");
await page.waitForTimeout(1200);
await shot("02-dashboard");
await page.screenshot({ path: "docs/screenshots/02b-dashboard-full.png", fullPage: false });
const pid = page.url().split("/p/")[1].split(/[/?]/)[0];

// zone drill-down
await page.locator('[aria-label*="L1-A"]').first().click();
await page.waitForTimeout(500);
await page.locator("text=Zone L1-A").scrollIntoViewIfNeeded();
await shot("02c-dashboard-zone-drilldown");

await page.goto(`${base}/p/${pid}/viewer`);
await page.waitForSelector('[data-testid="pano-view"]');
await page.waitForTimeout(3500);
await shot("03-viewer", 800);
await page.locator('label:has-text("Compare dates")').click();
await page.waitForTimeout(2500);
await shot("03b-viewer-compare", 800);
await page.locator('label:has-text("Compare dates")').click();
await page.locator('button:has-text("Look-through")').click();
await page.waitForTimeout(800);
await shot("03c-viewer-lookthrough", 500);

await page.goto(`${base}/p/${pid}/verify`);
await page.waitForSelector("text=CV proposes");
await page.waitForTimeout(2500);
await shot("04-verification");

await page.goto(`${base}/p/${pid}/elements`);
await page.waitForSelector('[role="grid"]');
await page.waitForTimeout(600);
await page.locator('[role="row"]').nth(3).click();
await page.waitForTimeout(1500);
await shot("05-elements");

await page.goto(`${base}/p/${pid}/schedule`);
await page.waitForSelector("text=Earned value");
await shot("06-schedule", 1200);

await page.goto(`${base}/p/${pid}/qc`);
await page.waitForSelector('[aria-label="Deviations"]');
await shot("07-qc", 2500);

await page.goto(`${base}/p/${pid}/uploads`);
await page.waitForSelector("text=BIM model (IFC)");
await shot("08-uploads");
// direct multipart flow in demo mode: multi-part files, progress, job polling
await page.setInputFiles('[data-testid="file-capture"]', [
  { name: "L1-walk-0905.insv", mimeType: "application/octet-stream", buffer: Buffer.alloc(1_400_000, 1) },
  { name: "L1-pano-001.jpg", mimeType: "image/jpeg", buffer: Buffer.alloc(300_000, 2) },
]);
await page.setInputFiles('[data-testid="file-model"]', [{ name: "Bastrop-ARCH.ifc", mimeType: "application/octet-stream", buffer: Buffer.alloc(900_000, 3) }]);
await page.locator('button:has-text("Upload (")').first().click();
await page.locator('button:has-text("Upload (")').first().click().catch(() => {});
await page.waitForTimeout(700);
await shot("08b-uploads-progress", 100);
await page.waitForSelector("text=Capture ready", { timeout: 20000 });
await shot("08c-uploads-done", 300);

await page.goto(`${base}/p/${pid}/settings`);
await page.waitForSelector("text=System status");
await shot("09-settings", 1000);
await page.locator('button:has-text("Connect Autodesk Forma")').click();
await page.waitForSelector('select[aria-label="Autodesk hub"]');
await page.selectOption('select[aria-label="Autodesk hub"]', { index: 1 });
await page.waitForTimeout(300);
await page.selectOption('select[aria-label="Autodesk project"]', { index: 1 });
await page.locator('button:has-text("Save link")').click();
await page.waitForSelector("text=BDH1-ARCH.rvt");
await page.locator('button:has-text("Sync locations")').click();
await page.locator('button:has-text("Push issues")').click();
await page.waitForSelector("text=Issues pushed");
await page.locator("text=Autodesk Forma (APS)").scrollIntoViewIfNeeded();
await shot("09b-settings-aps", 500);

// tablet
await page.setViewportSize({ width: 820, height: 1100 });
await page.goto(`${base}/p/${pid}`);
await page.waitForSelector("text=Subcontractor pay-app verification");
await shot("10-dashboard-tablet", 1200);
await page.goto(`${base}/p/${pid}/viewer`);
await page.waitForSelector('[data-testid="pano-view"]');
await page.waitForTimeout(3000);
await shot("11-viewer-tablet", 800);

console.log(errors.length ? "CONSOLE ISSUES:\n" + [...new Set(errors)].join("\n") : "no console errors");
await browser.close();
