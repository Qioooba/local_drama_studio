import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { listScriptBreakdownDrafts } from "../../generated/api";
import { AIDraftReviewPanel } from "./AIDraftReviewPanel";

vi.mock("../../generated/api", () => ({ listScriptBreakdownDrafts: vi.fn() }));
function renderPanel() { const client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); return render(<QueryClientProvider client={client}><AIDraftReviewPanel projectId="project-1" /></QueryClientProvider>); }

describe("AIDraftReviewPanel", () => {
  beforeEach(() => vi.mocked(listScriptBreakdownDrafts).mockReset());
  it("labels persisted suggestions as not applied and exposes no apply button", async () => {
    vi.mocked(listScriptBreakdownDrafts).mockResolvedValue({ automatic_apply: false, requires_human_action: true, items: [{ id: "draft", project_id: "project-1", source_document_version_id: "version", import_session_id: "session", status: "DRAFT_READY", source_document_code: "script", source_document_title: "剧本", draft: { scenes: [{ shots: [{}, {}] }] }, confidence: {}, application_status: "NOT_APPLIED", automatic_apply: false, requires_human_action: true, created_at: "now" }] });
    renderPanel();
    expect(await screen.findByText(/DRAFT_READY · NOT_APPLIED/)).toBeTruthy();
    expect(screen.getByText("1 个建议场次 · 2 个建议镜头")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });
  it("does not invent suggestions for an empty production project", async () => {
    vi.mocked(listScriptBreakdownDrafts).mockResolvedValue({ automatic_apply: false, requires_human_action: true, items: [] });
    renderPanel();
    expect(await screen.findByText(/不会显示模拟建议/)).toBeTruthy();
  });
});
