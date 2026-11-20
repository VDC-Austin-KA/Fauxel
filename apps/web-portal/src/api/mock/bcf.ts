// Minimal BCF 3.0 zip writer (store-only) for the demo "Export BCF" button.
import type { Deviation } from "@/lib/types";

const CRC = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; }
  return t;
})();
function crc32(b: Uint8Array): number { let c = 0xffffffff; for (const x of b) c = CRC[(c ^ x) & 255] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; }

function zip(files: { name: string; data: string }[]): Blob {
  const enc = new TextEncoder();
  const parts: Uint8Array[] = [], central: Uint8Array[] = [];
  let offset = 0;
  for (const f of files) {
    const name = enc.encode(f.name), data = enc.encode(f.data), crc = crc32(data);
    const h = new DataView(new ArrayBuffer(30));
    h.setUint32(0, 0x04034b50, true); h.setUint16(4, 20, true); h.setUint32(14, crc, true); h.setUint32(18, data.length, true); h.setUint32(22, data.length, true); h.setUint16(26, name.length, true);
    parts.push(new Uint8Array(h.buffer), name, data);
    const c = new DataView(new ArrayBuffer(46));
    c.setUint32(0, 0x02014b50, true); c.setUint16(4, 20, true); c.setUint16(6, 20, true); c.setUint32(16, crc, true); c.setUint32(20, data.length, true); c.setUint32(24, data.length, true); c.setUint16(28, name.length, true); c.setUint32(42, offset, true);
    central.push(new Uint8Array(c.buffer), name);
    offset += 30 + name.length + data.length;
  }
  const csize = central.reduce((a, b) => a + b.length, 0);
  const e = new DataView(new ArrayBuffer(22));
  e.setUint32(0, 0x06054b50, true); e.setUint16(8, files.length, true); e.setUint16(10, files.length, true); e.setUint32(12, csize, true); e.setUint32(16, offset, true);
  return new Blob([...parts, ...central, new Uint8Array(e.buffer)] as BlobPart[], { type: "application/octet-stream" });
}

export function buildBcf(project: string, devs: Deviation[], version: "3.0" | "2.1" = "3.0"): Blob {
  const files = [{ name: "bcf.version", data: version === "3.0" ? `<?xml version="1.0" encoding="UTF-8"?><Version VersionId="3.0"><DetailedVersion>3.0</DetailedVersion></Version>` : `<?xml version="1.0" encoding="UTF-8"?><Version VersionId="2.1"><DetailedVersion>2.1</DetailedVersion></Version>` }];
  for (const d of devs) {
    files.push({
      name: `${d.id}/markup.bcf`,
      data: `<?xml version="1.0" encoding="UTF-8"?><Markup><Topic Guid="${d.id}" TopicType="${d.kind}" TopicStatus="${d.status}"><Title>${(d.element_name ?? d.element_uid).replace(/&/g, "&amp;")}</Title><Priority>${d.severity}</Priority><CreationDate>${d.created_at}</CreationDate><Description>${d.note.replace(/&/g, "&amp;")} (${project})</Description></Topic></Markup>`,
    });
  }
  return zip(files);
}
