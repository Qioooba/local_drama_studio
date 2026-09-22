import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { uploadScriptDocument } from "./api";

const fetchMock = vi.fn();

function apiResponse(body: unknown): Response {
  const headers = new Headers({ "Content-Type": "application/json", "X-API-Contract-Version": "localdrama.api.2026-08-29.3" });
  return new Response(JSON.stringify(body), { status: 200, headers });
}

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (path: string) => apiResponse(
    path.endsWith("/session/bootstrap")
      ? { token: "t", mode: "LOCAL_ONLY" }
      : {
        import: {
          source_document_id: "d", source_document_version_id: "v", import_session_id: "s", media_version_id: "m",
          stored_source: {
            scope: "PROJECT", kind: "FILE", display_name: "episode.md", server_absolute_path: "C:/tmp/episode.md",
            rel_path: "01_source/episode.md", download_url: "/api/v1/media-versions/m/content", download_filename: "episode.md",
          },
          status: "PREVIEW_READY", preview_hash: "a".repeat(64), index_status: "READY",
          preview: { character_count: 2, paragraph_count: 1, paragraphs: ["正文"], preview_character_limit: 10, preview_truncated: false, offset_unit: "UNICODE_CODEPOINT", requires_llm_confirmation: true },
        },
      },
  ));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => vi.unstubAllGlobals());

describe("uploadScriptDocument cancellation support", () => {
  it("passes a caller signal through to fetch so a superseded parse can be aborted", async () => {
    const controller = new AbortController();
    await uploadScriptDocument("project-1", new File(["正文"], "episode.md", { type: "text/markdown" }), "", controller.signal);
    const upload = fetchMock.mock.calls.find(([path]) => String(path).endsWith("/imports:upload"));
    expect(upload?.[1]?.signal).toBe(controller.signal);
  });

  it("sends no signal when the caller does not need cancellation", async () => {
    await uploadScriptDocument("project-1", new File(["正文"], "episode.md"));
    const upload = fetchMock.mock.calls.find(([path]) => String(path).endsWith("/imports:upload"));
    expect(upload?.[1]?.signal).toBeUndefined();
    expect(new Headers(upload?.[1]?.headers).get("X-File-Name")).toBe("episode.md");
  });

  it("really aborts the request when the signal fires", async () => {
    const controller = new AbortController();
    fetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      if (String(path).endsWith("/session/bootstrap")) return apiResponse({ token: "t", mode: "LOCAL_ONLY" });
      return new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
      });
    });
    const pending = uploadScriptDocument("project-1", new File(["正文"], "episode.md"), "", controller.signal);
    await Promise.resolve();
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  });
});
