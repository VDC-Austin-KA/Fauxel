export class MockError extends Error {
  constructor(public status: number, message: string) { super(message); }
}
export function need<T>(v: T | undefined, what = "Not found"): T {
  if (!v) throw new MockError(404, what);
  return v;
}
