import { getSourceDocumentPassage } from "../../generated/api";

export const SOURCE_PASSAGE_MAX_CHARACTERS = 8_000;

export type SourcePassage = {
  source_document_version_id: string;
  source_start: number;
  source_end: number;
  requested_end: number;
  offset_unit: "UNICODE_CODEPOINT";
  text: string;
  text_sha256: string;
  source_text_sha256: string;
  total_character_count: number | null;
  has_more: boolean;
  maximum_character_count: number;
  read_only: true;
};

export async function getSourcePassage(
  sourceDocumentVersionId: string,
  start: number,
  end: number,
  baseUrl = "",
): Promise<SourcePassage> {
  const safeStart = Math.max(0, Math.floor(start));
  const safeEnd = Math.max(safeStart + 1, Math.min(Math.floor(end), safeStart + SOURCE_PASSAGE_MAX_CHARACTERS));
  return getSourceDocumentPassage(sourceDocumentVersionId, safeStart, safeEnd, baseUrl);
}
