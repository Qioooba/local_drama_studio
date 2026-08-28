import { afterEach, describe, expect, it, vi } from "vitest";
import {
  DIAGNOSTIC_RUN_TIMEOUT_MS,
  diagnosticErrorMessage,
  diagnosticRequestWithTimeout,
} from "./DiagnosticsPage";

describe("diagnostic request timing", () => {
  afterEach(() => vi.useRealTimers());

  it("does not report a false timeout for a diagnostic run longer than ten seconds", async () => {
    vi.useFakeTimers();
    const request = new Promise<string>((resolve) => window.setTimeout(() => resolve("complete"), 17_500));
    const result = diagnosticRequestWithTimeout(request, "诊断超时", DIAGNOSTIC_RUN_TIMEOUT_MS);

    await vi.advanceTimersByTimeAsync(17_500);

    await expect(result).resolves.toBe("complete");
  });

  it("still bounds a diagnostic run and provides a recovery-oriented message", async () => {
    vi.useFakeTimers();
    const request = new Promise<string>(() => undefined);
    const result = diagnosticRequestWithTimeout(request, "服务端可能仍在完成，请稍后重新读取", DIAGNOSTIC_RUN_TIMEOUT_MS);
    const rejection = expect(result).rejects.toThrow("服务端可能仍在完成，请稍后重新读取");

    await vi.advanceTimersByTimeAsync(DIAGNOSTIC_RUN_TIMEOUT_MS);

    await rejection;
  });

  it("shows the useful error text without the Error prefix or encoded trailing spaces", () => {
    expect(diagnosticErrorMessage(new Error("诊断失败 &#x20;"))).toBe("诊断失败");
  });
});
