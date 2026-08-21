export type ShotRevisionWrite = {
  id: string;
  shot_id: string;
  revision_no: number;
  fields: Record<string, unknown>;
  is_frozen: boolean;
};

export type SaveDirectorIntentInput = {
  shotId: string;
  fields: Record<string, unknown>;
  expectedRevisionNo: number;
  freeze?: boolean;
};

export class DirectorIntentApiError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly details?: Record<string, unknown>;

  constructor(message: string, status: number, code?: string, details?: Record<string, unknown>) {
    super(message);
    this.name = "DirectorIntentApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export async function saveDirectorIntentRevision(input: SaveDirectorIntentInput): Promise<ShotRevisionWrite> {
  const response = await fetch(`/api/v1/projects/shots/${encodeURIComponent(input.shotId)}/revisions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      fields: input.fields,
      freeze: input.freeze ?? false,
      expected_revision_no: input.expectedRevisionNo,
    }),
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    let code: string | undefined;
    let details: Record<string, unknown> | undefined;
    try {
      const payload = await response.json() as {
        code?: string;
        message?: string;
        error?: { code?: string; message?: string; details?: Record<string, unknown> };
        detail?: string | { code?: string; message?: string; details?: Record<string, unknown> };
        details?: Record<string, unknown>;
      };
      if (typeof payload.detail === "string") message = payload.detail;
      else if (payload.detail) {
        message = payload.detail.message ?? message;
        code = payload.detail.code;
        details = payload.detail.details;
      }
      message = payload.message ?? message;
      code = payload.code ?? code;
      details = payload.details ?? details;
      message = payload.error?.message ?? message;
      code = payload.error?.code ?? code;
      details = payload.error?.details ?? details;
    } catch {
      // Keep the status text when an upstream proxy returns HTML.
    }
    throw new DirectorIntentApiError(message, response.status, code, details);
  }
  const payload = await response.json() as { shot_revision: ShotRevisionWrite };
  return payload.shot_revision;
}
