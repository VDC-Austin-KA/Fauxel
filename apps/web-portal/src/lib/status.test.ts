import { describe, expect, it } from "vitest";
import { STATUS_COLORS, percentColor, statusColor, statusKind, textOn } from "./status";

describe("status colors (platform contract)", () => {
  it("maps states to contract hex values", () => {
    expect(statusColor("INSTALLED")).toBe("#22c55e");
    expect(statusColor("INSULATED_OR_FINISHED")).toBe("#22c55e");
    expect(statusColor("FRAMED_OR_ROUGHED")).toBe("#eab308");
    expect(statusColor("DEVIATION_FLAGGED")).toBe("#ef4444");
    expect(statusColor("NOT_STARTED", 0)).toBe("#9ca3af");
  });
  it("uses percent for in-progress when state is NOT_STARTED", () => {
    expect(statusKind("NOT_STARTED", 40)).toBe("inProgress");
    expect(statusKind("NOT_STARTED", 100)).toBe("installed");
    expect(statusKind("NOT_STARTED", 0)).toBe("notStarted");
  });
  it("deviation wins over percent", () => {
    expect(statusKind("DEVIATION_FLAGGED", 100)).toBe("deviation");
  });
  it("percentColor endpoints", () => {
    expect(percentColor(0)).toBe(STATUS_COLORS.notStarted);
    expect(percentColor(100)).toBe(STATUS_COLORS.installed);
    expect(percentColor(50)).toBe(STATUS_COLORS.inProgress);
    expect(percentColor(150)).toBe(STATUS_COLORS.installed);
  });
  it("textOn picks contrast", () => {
    expect(textOn("#ffffff")).toBe("#0f172a");
    expect(textOn("#0f172a")).toBe("#ffffff");
  });
});
