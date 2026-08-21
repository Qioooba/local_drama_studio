import { describe, expect, it } from "vitest";
import { parseScriptBreakdownDraftList } from "./api";

const validItem = {
  id: "draft-1",
  project_id: "project-1",
  source_document_version_id: "version-1",
  import_session_id: "session-1",
  status: "DRAFT_READY",
  source_document_code: "DOC_001",
  source_document_title: "第一幕",
  draft: { scenes: [] },
  confidence: { questions: [], source_passages: [] },
  profile_version_id: null,
  evidence_status: "COMPLETE",
  application_status: "NOT_APPLIED",
  automatic_apply: false,
  requires_human_action: true,
  created_at: "2026-08-21T00:00:00Z",
};

describe("script breakdown response runtime contract", () => {
  it("accepts the canonical envelope", () => {
    const parsed = parseScriptBreakdownDraftList({
      items: [validItem],
      automatic_apply: false,
      requires_human_action: true,
    });
    expect(parsed.items[0].id).toBe("draft-1");
  });

  it.each([
    {},
    { items: {}, automatic_apply: false, requires_human_action: true },
    { items: [{ ...validItem, draft: { scenes: {} } }], automatic_apply: false, requires_human_action: true },
    { items: [validItem], automatic_apply: true, requires_human_action: false },
  ])("fails closed for malformed or unsafe envelopes", (payload) => {
    expect(() => parseScriptBreakdownDraftList(payload)).toThrow(/SCRIPT_BREAKDOWN_CONTRACT_INVALID/);
  });
});
