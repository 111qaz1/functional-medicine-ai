export const CASES_PER_PAGE = 50;

export function parseCasePage(value: string | null): number {
  const page = Number.parseInt(value ?? "1", 10);
  return Number.isFinite(page) && page > 0 ? page : 1;
}

export function casePageCount(total: number): number {
  return Math.max(1, Math.ceil(Math.max(0, total) / CASES_PER_PAGE));
}

export function casePageOffset(page: number): number {
  return (Math.max(1, page) - 1) * CASES_PER_PAGE;
}
