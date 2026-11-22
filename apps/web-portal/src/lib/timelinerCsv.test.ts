import { describe, expect, it } from "vitest";
import { timelinerCsv } from "./timelinerCsv";
import type { Activity } from "./types";

describe("timelinerCsv", () => {
  it("writes one TimeLiner row per activity keyed by the P6 activity id", () => {
    const csv = timelinerCsv([{ external_id: "A1000", name: 'Level 1 "SOG"', planned_start: "2026-04-24", planned_finish: "2026-06-23", wbs: "Structure" } as Activity]);
    expect(csv.split("\r\n")[0]).toBe("Activity ID,Task Name,Planned Start,Planned End,Task Type,WBS");
    expect(csv.split("\r\n")[1]).toBe('"A1000","Level 1 ""SOG""","2026-04-24","2026-06-23","Construct","Structure"');
  });
});
