export type FrameBridgeWrite = {
  id: string;
  from_shot_id: string;
  to_shot_id: string;
  from_anchor_id: string | null;
  to_anchor_id: string | null;
  enforcement: string;
  compatibility_status: string;
  boundary_revision: number;
  revision: number;
  is_stale: boolean;
  stale_reason: string | null;
  locked: boolean;
  inherited_from_anchor_id?: string;
};

type FrameBridgeResponse = { frame_bridge: FrameBridgeWrite };

export class FrameBridgeApiError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly details?: Record<string, unknown>;

  constructor(message: string, status: number, code?: string, details?: Record<string, unknown>) {
    super(message);
    this.name = "FrameBridgeApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

async function writeFrameBridge(
  transitionId: string,
  command: "inherit" | "current-frame" | "source-frame" | "lock" | "unlock",
  payload: Record<string, unknown>,
): Promise<FrameBridgeWrite> {
  const response = await fetch(`/api/v1/frame-bridges/${encodeURIComponent(transitionId)}/${command}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    let code: string | undefined;
    let details: Record<string, unknown> | undefined;
    try {
      const envelope = await response.json() as {
        code?: string;
        message?: string;
        details?: Record<string, unknown>;
        error?: { code?: string; message?: string; details?: Record<string, unknown> };
        detail?: string | { code?: string; message?: string; details?: Record<string, unknown> };
      };
      if (typeof envelope.detail === "string") message = envelope.detail;
      else if (envelope.detail) {
        message = envelope.detail.message ?? message;
        code = envelope.detail.code;
        details = envelope.detail.details;
      }
      message = envelope.message ?? message;
      code = envelope.code ?? code;
      details = envelope.details ?? details;
      message = envelope.error?.message ?? message;
      code = envelope.error?.code ?? code;
      details = envelope.error?.details ?? details;
    } catch {
      // Keep the HTTP status when a proxy returns a non-JSON response.
    }
    throw new FrameBridgeApiError(message, response.status, code, details);
  }
  const result = await response.json() as FrameBridgeResponse;
  return result.frame_bridge;
}

export function inheritFrameBridge(
  transitionId: string,
  expectedBoundaryRevision: number,
  options: { sourceAnchorId?: string; lock?: boolean } = {},
) {
  return writeFrameBridge(transitionId, "inherit", {
    expected_boundary_revision: expectedBoundaryRevision,
    source_anchor_id: options.sourceAnchorId,
    lock: options.lock,
  });
}

export function setFrameBridgeCurrentFrame(
  transitionId: string,
  expectedBoundaryRevision: number,
  candidate: { mediaVersionId: string } | { frameAnchorId: string },
) {
  return writeFrameBridge(transitionId, "current-frame", {
    expected_boundary_revision: expectedBoundaryRevision,
    ...( "mediaVersionId" in candidate
      ? { media_version_id: candidate.mediaVersionId }
      : { frame_anchor_id: candidate.frameAnchorId }),
  });
}

export function setFrameBridgeLocked(transitionId: string, expectedBoundaryRevision: number, locked: boolean) {
  return writeFrameBridge(transitionId, locked ? "lock" : "unlock", {
    expected_boundary_revision: expectedBoundaryRevision,
  });
}

export function setFrameBridgeSourceFrame(transitionId: string, expectedBoundaryRevision: number, frameAnchorId: string) {
  return writeFrameBridge(transitionId, "source-frame", {
    expected_boundary_revision: expectedBoundaryRevision,
    frame_anchor_id: frameAnchorId,
  });
}
