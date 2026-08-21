import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuditHistoryPanel } from "./AuditHistoryPanel";
import { getAuditProof, listAuditEvents } from "../../generated/api";

vi.mock("../../generated/api", () => ({ getAuditProof: vi.fn(), listAuditEvents: vi.fn() }));

describe("AuditHistoryPanel", () => {
  beforeEach(() => {
    vi.mocked(listAuditEvents).mockReset();
    vi.mocked(getAuditProof).mockReset();
    vi.mocked(listAuditEvents).mockResolvedValue({
      items: [{ event_id: 9, actor: "local-user", role_context: "operator", action: "REVIEW_SUBMITTED", subject_type: "media_version", subject_id: "media-9", project_id: "project-1", before_revision: null, after_revision: null, request_id: null, job_id: null, occurred_at: "2026-08-15 12:00:00", summary: "人工审核已提交", metadata: { token: "[REDACTED]" }, metadata_redacted: true, local_only: true, network_contacted: false, mutated: false }], next_cursor: 4, cursor: 0, limit: 50, filters: {}, local_only: true, network_contacted: false, mutated: false,
    });
    vi.mocked(getAuditProof).mockResolvedValue({ algorithm: "SHA-256-chain-v1", scope: "filtered-redacted-audit-export", event_count: 1, first_event_id: 9, last_event_id: 9, chain_sha256: "a".repeat(64), truncated: false, max_events: 1000, filters: {}, metadata_redacted: true, local_only: true, network_contacted: false, mutated: false });
  });

  it("loads project-scoped redacted history and follows a stable cursor", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={queryClient}><AuditHistoryPanel projectId="project-1" /></QueryClientProvider>);
    expect(await screen.findByText("REVIEW_SUBMITTED")).toBeTruthy();
    expect(screen.getByText(/仅本地/)).toBeTruthy();
    await waitFor(() => expect(listAuditEvents).toHaveBeenCalledWith(expect.objectContaining({ project_id: "project-1", cursor: 0, limit: 50 })));
    fireEvent.click(screen.getByRole("button", { name: "更早事件" }));
    await waitFor(() => expect(listAuditEvents).toHaveBeenLastCalledWith(expect.objectContaining({ project_id: "project-1", cursor: 4, limit: 50 })));
    fireEvent.click(screen.getByRole("button", { name: "生成哈希证明" }));
    expect(await screen.findByText(/导出证明/)).toBeTruthy();
    expect(getAuditProof).toHaveBeenCalledWith(expect.objectContaining({ project_id: "project-1" }), 1000);
  });
});
