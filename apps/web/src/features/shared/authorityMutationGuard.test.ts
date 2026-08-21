import { describe, expect, it } from "vitest";
import directorDesk from "../../pages/DirectorDeskPage.tsx?raw";
import episodeReview from "../episode-review-v2/EpisodeReviewWorkspace.tsx?raw";
import directorIntent from "../director-v2/DirectorIntentEditor.tsx?raw";
import frameBridge from "../director-v2/FrameBridgeControls.tsx?raw";
import generationWorkbench from "../generation/GenerationWorkbench.tsx?raw";
import timelineComposer from "../timeline-v2/TimelineComposer.tsx?raw";
import formalSelection from "../reviews/FormalSelectionPanel.tsx?raw";
import legacyDirectorRevision from "../production/DirectorShotEditor.tsx?raw";

/**
 * Doc 02 §40: these surfaces mutate server-authoritative facts. They may keep
 * local tabs, inspector selection, drag/reorder previews, preflight plans and
 * pending dialogs, but must not publish an authoritative success before the
 * command response arrives.
 */
const AUTHORITY_SURFACES: Record<string, string> = {
  "DirectorDesk selection/approval": directorDesk,
  "EpisodeReview selection/approval": episodeReview,
  "Director immutable revision": directorIntent,
  "Frame Bridge anchors/lock": frameBridge,
  "Generation result": generationWorkbench,
  "Timeline compose revision": timelineComposer,
  "Formal delivery selection": formalSelection,
  "Legacy immutable shot revision": legacyDirectorRevision,
};

const CALLBACK_BOUNDARY = /\b(?:mutationFn|onSuccess|onError|onSettled|onMutate)\s*:/g;
const FORBIDDEN_OPTIMISTIC_AUTHORITY = [
  /\bsetQueryData\s*\(/,
  /\bsetQueriesData\s*\(/,
  /\bsetSubmitted(?:VariantId|Count)?\s*\(/,
  /\bset(?:SelectedVariant|Approved|Approval|Revision|Boundary|Timeline)\w*\s*\(/,
  /\bset(?:Feedback|Message)\s*\(\s*[`'"][^`'"]*(?:成功|已采用|已批准|已冻结|已锁定|已创建)/,
] as const;

function onMutateBodies(source: string): string[] {
  const starts = [...source.matchAll(/\bonMutate\s*:/g)];
  return starts.map((match) => {
    const start = (match.index ?? 0) + match[0].length;
    CALLBACK_BOUNDARY.lastIndex = start;
    const next = CALLBACK_BOUNDARY.exec(source);
    return source.slice(start, next?.index ?? source.length);
  });
}

describe("server-authority optimistic update guard", () => {
  it.each(Object.entries(AUTHORITY_SURFACES))("%s never fabricates authority in onMutate", (_name, source) => {
    for (const body of onMutateBodies(source)) {
      for (const forbidden of FORBIDDEN_OPTIMISTIC_AUTHORITY) expect(body).not.toMatch(forbidden);
    }
  });

  it("allows local-only navigation and preview state", () => {
    const localUiOnly = `
      const chooseInspectorTab = () => setInspectorTab("picture");
      const previewReorder = () => setDraftOrder(["shot-2", "shot-1"]);
      const mutation = useMutation({ mutationFn: saveOrder, onSuccess: refresh });
    `;
    expect(onMutateBodies(localUiOnly)).toEqual([]);
  });

  it("detects cache writes and synthetic successes if introduced into onMutate", () => {
    const invalid = `useMutation({
      mutationFn: selectCandidate,
      onMutate: () => { queryClient.setQueryData(["desk"], { selected: true }); setFeedback("已采用"); },
      onSuccess: refresh,
    })`;
    const body = onMutateBodies(invalid)[0];
    expect(FORBIDDEN_OPTIMISTIC_AUTHORITY.some((forbidden) => forbidden.test(body))).toBe(true);
  });
});
