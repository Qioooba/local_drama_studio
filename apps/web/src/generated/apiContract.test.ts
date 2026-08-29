import { describe, expect, it } from "vitest";
import { ApiContractError, parseDocumentImportEnvelope } from "./api";

const validImport = {
  import: {
    source_document_id: "source-1",
    source_document_version_id: "source-version-1",
    import_session_id: "session-1",
    media_version_id: "media-1",
    stored_source: {
      scope: "PROJECT",
      kind: "FILE",
      display_name: "照骨灯_凡人修仙原创长篇_约200分钟.txt",
      server_absolute_path: "F:\\DramaProjects\\project-1\\00_admin\\imports\\source.txt",
      rel_path: "00_admin/imports/source.txt",
      download_url: "/api/v1/media-versions/media-1/content",
      download_filename: "照骨灯_凡人修仙原创长篇_约200分钟.txt",
    },
    status: "PREVIEW_READY",
    preview_hash: "a".repeat(64),
    index_status: "READY",
    preview: {
      character_count: 8,
      paragraph_count: 2,
      paragraphs: ["第一段", "第二段"],
      preview_character_limit: 1_000,
      preview_truncated: false,
      offset_unit: "UNICODE_CODEPOINT",
      requires_llm_confirmation: true,
    },
  },
};

describe("document import API contract", () => {
  it("accepts the canonical transport-safe envelope", () => {
    expect(parseDocumentImportEnvelope(validImport)).toEqual(validImport);
  });

  it.each([
    {},
    { import: { ...validImport.import, stored_source: undefined } },
    { import: { ...validImport.import, stored_source_path: "D:\\server\\source.txt", stored_source: undefined } },
    { import: { ...validImport.import, stored_source: { ...validImport.import.stored_source, display_name: undefined } } },
    { import: { ...validImport.import, stored_source: { ...validImport.import.stored_source, server_absolute_path: undefined } } },
    { import: { ...validImport.import, preview: undefined } },
  ])("rejects malformed and legacy envelopes before React receives them", (payload) => {
    expect(() => parseDocumentImportEnvelope(payload)).toThrow(ApiContractError);
    expect(() => parseDocumentImportEnvelope(payload)).toThrow(/DOCUMENT_IMPORT_CONTRACT_INVALID/);
  });
});
