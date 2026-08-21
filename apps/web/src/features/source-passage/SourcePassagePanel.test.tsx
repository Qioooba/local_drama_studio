import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SourcePassagePanel } from "./SourcePassagePanel";

function payload(start: number, end: number, options: { total?: number; hasMore?: boolean } = {}) {
  return {
    source_document_version_id: "source-v1",
    source_start: start,
    source_end: end,
    requested_end: start + 8_000,
    offset_unit: "UNICODE_CODEPOINT",
    text: `片段 ${start}-${end}`,
    text_sha256: "a".repeat(64),
    source_text_sha256: "b".repeat(64),
    total_character_count: options.total ?? 20_000,
    has_more: options.hasMore ?? true,
    maximum_character_count: 8_000,
    read_only: true,
  };
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><SourcePassagePanel sourceDocumentVersionId="source-v1" initialStart={9_000} initialEnd={9_200} /></QueryClientProvider>);
}

afterEach(() => vi.restoreAllMocks());

describe("SourcePassagePanel", () => {
  it("requests bounded pages and navigates without loading the whole document", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify(payload(8_000, 16_000)), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(payload(16_000, 20_000, { hasMore: false })), { status: 200 }));
    renderPanel();

    expect(await screen.findByText("片段 8000-16000")).toBeTruthy();
    expect(fetchMock.mock.calls[0]?.[0]).toContain("start=8000&end=16000");
    expect(screen.getByText("字符 8,000–16,000 / 20,000")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "后一片段 →" }));

    expect(await screen.findByText("片段 16000-20000")).toBeTruthy();
    expect(fetchMock.mock.calls[1]?.[0]).toContain("start=16000&end=24000");
    expect((screen.getByRole("button", { name: "后一片段 →" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows an actionable error and retries the same bounded range", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: { message: "本地原文暂不可读" } }), { status: 503 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(payload(8_000, 16_000)), { status: 200 }));
    renderPanel();

    expect(await screen.findByText("本地原文暂不可读")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("片段 8000-16000")).toBeTruthy();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });
});
