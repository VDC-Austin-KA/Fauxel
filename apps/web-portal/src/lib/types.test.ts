import { describe, expect, it } from "vitest";
import { currentModelIds, type BimModel } from "./types";

const m = (id: string, name: string, version: number, source: BimModel["source"] = "ifc"): BimModel =>
  ({ id, project_id: "p", name, source, version, created_at: "2026-11-20T00:00:00Z" });

describe("currentModelIds", () => {
  it("keeps the latest IFC version of each discipline model", () => {
    const ids = currentModelIds([m("a1", "BDC-A-Architectural", 1), m("a2", "BDC-A-Architectural", 2),
      m("s1", "BDC-S-Structural", 1), m("r1", "Hall A.rvt", 4, "revit")]);
    expect(ids).toEqual(["a2", "s1"]);
  });
  it("handles no models", () => expect(currentModelIds(undefined)).toEqual([]));
});
