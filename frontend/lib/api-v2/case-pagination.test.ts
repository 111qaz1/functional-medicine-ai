import { describe, expect, it } from "vitest";

import { CASES_PER_PAGE, casePageCount, casePageOffset, parseCasePage } from "./case-pagination";

describe("case pagination", () => {
  it("uses a fixed page size of fifty cases", () => {
    expect(CASES_PER_PAGE).toBe(50);
    expect(casePageOffset(1)).toBe(0);
    expect(casePageOffset(2)).toBe(50);
  });

  it("normalizes invalid pages and computes the last available page", () => {
    expect(parseCasePage(null)).toBe(1);
    expect(parseCasePage("invalid")).toBe(1);
    expect(parseCasePage("0")).toBe(1);
    expect(casePageCount(0)).toBe(1);
    expect(casePageCount(101)).toBe(3);
  });
});
