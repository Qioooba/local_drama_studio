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

function errorMessage(body: unknown, status: number): string {
  if (body && typeof body === "object") {
    const detail = (body as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object") {
      const message = (detail as { message?: unknown }).message;
      if (typeof message === "string") return message;
    }
  }
  return `原文片段请求失败（HTTP ${status}）`;
}

/** Feature-local adapter until the generated client contains getSourceDocumentPassage. */
export async function getSourcePassage(
  sourceDocumentVersionId: string,
  start: number,
  end: number,
  baseUrl = "",
): Promise<SourcePassage> {
  const safeStart = Math.max(0, Math.floor(start));
  const safeEnd = Math.max(safeStart + 1, Math.min(Math.floor(end), safeStart + SOURCE_PASSAGE_MAX_CHARACTERS));
  const query = new URLSearchParams({ start: String(safeStart), end: String(safeEnd) });
  const path = `/api/v1/source-document-versions/${encodeURIComponent(sourceDocumentVersionId)}/passage?${query}`;
  const response = await fetch(`${baseUrl}${path}`);
  if (!response.ok) {
    let body: unknown;
    try { body = await response.json(); } catch { body = null; }
    throw new Error(errorMessage(body, response.status));
  }
  return response.json() as Promise<SourcePassage>;
}
