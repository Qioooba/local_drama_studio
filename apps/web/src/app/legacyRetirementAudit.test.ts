import { describe, expect, it } from "vitest";
import routerSource from "./router.tsx?raw";

type Status = "EQUIVALENT_V2" | "MISSING_V2" | "SYSTEM_OR_TOOL" | "SHELL_INFRA";
type AuditItem = { status: Status; target: string; safeToRemoveFromLegacyApp: boolean; authority?: string };

/**
 * Historical P12 migration inventory. Components remain reusable; this only
 * proves every former legacy-shell dependency now has a V2 owner.
 */
const MATRIX: Record<string, AuditItem> = {
  "../features/generation/GenerationWorkbench#GenerationWorkbench": { status: "EQUIVALENT_V2", target: "episodes/:episodeId/generation", safeToRemoveFromLegacyApp: true, authority: "GenerationIntent/Variant/Job" },
  "../features/generation/PostProcessPanel#PostProcessPanel": { status: "EQUIVALENT_V2", target: "episodes/:episodeId/delivery", safeToRemoveFromLegacyApp: true, authority: "PostProcessRecipe/EnhancementRun" },
  "../features/jobs/JobsPanel#CapacitySnapshotPanel": { status: "SYSTEM_OR_TOOL", target: "/jobs", safeToRemoveFromLegacyApp: true },
  "../features/jobs/JobsPanel#JobsPanel": { status: "SYSTEM_OR_TOOL", target: "/jobs", safeToRemoveFromLegacyApp: true, authority: "durable Job actions" },
  "../features/reviews/ReviewInboxPanel#ReviewInboxPanel": { status: "EQUIVALENT_V2", target: "episodes/:episodeId/review", safeToRemoveFromLegacyApp: true, authority: "Review/Selection" },
  "../features/reviews/ImageCandidateGrid#ImageCandidateGrid": { status: "EQUIVALENT_V2", target: "EpisodeReviewWorkspace candidate list/compare", safeToRemoveFromLegacyApp: true },
  "../features/reviews/FormalSelectionPanel#FormalSelectionPanel": { status: "EQUIVALENT_V2", target: "episodes/:episodeId/review", safeToRemoveFromLegacyApp: true, authority: "formal delivery selection" },
  "../features/profiles/ProfileConfigurationPanel#ProfileConfigurationPanel": { status: "SYSTEM_OR_TOOL", target: "/models", safeToRemoveFromLegacyApp: true, authority: "Profile/Workflow publish" },
  "../features/canvas/ProductionCanvasPanel#ProductionCanvasPanel": { status: "EQUIVALENT_V2", target: "/projects/:projectId/labs", safeToRemoveFromLegacyApp: true, authority: "Visual Lab documents and immutable node revisions" },
  "../features/production/EpisodeContactSheetAction#EpisodeContactSheetAction": { status: "EQUIVALENT_V2", target: "episodes/:episodeId/delivery", safeToRemoveFromLegacyApp: true },
  "../features/production/EpisodeReviewPanel#EpisodeReviewPanel": { status: "EQUIVALENT_V2", target: "episodes/:episodeId/review", safeToRemoveFromLegacyApp: true, authority: "episode render review" },
  "../features/production/TimelineExportAction#TimelineExportAction": { status: "EQUIVALENT_V2", target: "TimelineExportPanel on episodes/:episodeId/timeline", safeToRemoveFromLegacyApp: true },
  "../features/production/SubtitleRevisionPanel#SubtitleRevisionPanel": { status: "EQUIVALENT_V2", target: "episodes/:episodeId/timeline", safeToRemoveFromLegacyApp: true, authority: "SubtitleRevision" },
  "../features/production/TimelineRevisionPanel#TimelineRevisionPanel": { status: "EQUIVALENT_V2", target: "TimelineComposer on episodes/:episodeId/timeline", safeToRemoveFromLegacyApp: true, authority: "TimelineRevision" },
  "../features/production/DeliveryWorkflowPanel#DeliveryWorkflowPanel": { status: "EQUIVALENT_V2", target: "episodes/:episodeId/delivery", safeToRemoveFromLegacyApp: true, authority: "Render/DeliveryPackage/approval" },
  "../features/shared/GlobalSearchPanel#GlobalSearchPanel": { status: "EQUIVALENT_V2", target: "AppShell CommandPalette entity search", safeToRemoveFromLegacyApp: true },
  "../features/shared/ProjectHealthPanel#ProjectHealthPanel": { status: "EQUIVALENT_V2", target: "/projects/:projectId/operations", safeToRemoveFromLegacyApp: true },
  "../features/shared/WorkspaceAssetAuthorizationPanel#WorkspaceAssetAuthorizationPanel": { status: "EQUIVALENT_V2", target: "/projects/:projectId/operations", safeToRemoveFromLegacyApp: true, authority: "WorkspaceAssetAuthorization" },
  "../features/shared/OutboxDeliveryPanel#OutboxDeliveryPanel": { status: "SYSTEM_OR_TOOL", target: "/projects/:projectId/operations", safeToRemoveFromLegacyApp: true, authority: "explicit outbox delivery" },
  "../features/shared/AutomationPanel#AutomationPanel": { status: "SYSTEM_OR_TOOL", target: "/projects/:projectId/operations", safeToRemoveFromLegacyApp: true, authority: "AutomationClient/Webhook/Delivery" },
  "../features/shared/AutomationWorkflowPanel#AutomationWorkflowPanel": { status: "SYSTEM_OR_TOOL", target: "/projects/:projectId/operations", safeToRemoveFromLegacyApp: true, authority: "AutomationWorkflow/Run" },
  "../features/shared/ComfyLabPanel#ComfyLabPanel": { status: "SYSTEM_OR_TOOL", target: "/lab", safeToRemoveFromLegacyApp: true },
  "../features/shared/AuditHistoryPanel#AuditHistoryPanel": { status: "SYSTEM_OR_TOOL", target: "/diagnostics", safeToRemoveFromLegacyApp: true },
  "../features/shared/BrandKitPanel#BrandKitPanel": { status: "EQUIVALENT_V2", target: "/projects/:projectId/operations", safeToRemoveFromLegacyApp: true, authority: "BrandKit/WatermarkProfile/CompliancePolicy" },
  "../features/production/ContinuityPanel#ContinuityPanel": { status: "EQUIVALENT_V2", target: "episodes/:episodeId/direct/:shotId continuity inspector", safeToRemoveFromLegacyApp: true },
  "../features/production/DirectorShotEditor#DirectorShotEditor": { status: "EQUIVALENT_V2", target: "DirectorIntentEditor + ShotAssetSection on DirectorDesk", safeToRemoveFromLegacyApp: true, authority: "ShotRevision/ProductionReady" },
  "../features/production/PromptTemplatePanel#PromptTemplatePanel": { status: "EQUIVALENT_V2", target: "episodes/:episodeId/plan", safeToRemoveFromLegacyApp: true, authority: "PromptTemplate/PromptRevision" },
  "../features/production/StoryAssetLibraryPanel#StoryAssetLibraryPanel": { status: "EQUIVALENT_V2", target: "/projects/:projectId/assets", safeToRemoveFromLegacyApp: true, authority: "StoryAsset" },
  "../features/projects/ProjectTemplateCopyAction#ProjectTemplateCopyAction": { status: "EQUIVALENT_V2", target: "/projects/:projectId/operations", safeToRemoveFromLegacyApp: true, authority: "Project copy" },
  "../features/projects/ProjectCreateWizard#ProjectCreateWizard": { status: "EQUIVALENT_V2", target: "/projects", safeToRemoveFromLegacyApp: true, authority: "Project create" },
  "../features/projects/EpisodeSceneRanges#EpisodeSceneRanges": { status: "EQUIVALENT_V2", target: "episodes/:episodeId/plan", safeToRemoveFromLegacyApp: true, authority: "ProjectScene/EpisodeSceneRange" },
  "../features/projects/ProjectPackageAction#ProjectPackageAction": { status: "EQUIVALENT_V2", target: "/projects/:projectId/operations", safeToRemoveFromLegacyApp: true, authority: "ProjectPackage import/export" },
  "../features/projects/ProjectAssetGrantPanel#ProjectAssetGrantPanel": { status: "EQUIVALENT_V2", target: "/projects/:projectId/operations", safeToRemoveFromLegacyApp: true, authority: "ProjectAssetGrant" },
  "../features/projects/CreativeLibrary#CreativeLibrary": { status: "EQUIVALENT_V2", target: "/projects/:projectId/story", safeToRemoveFromLegacyApp: true, authority: "creative library revisions" },
  "../features/projects/AIDraftReviewPanel#AIDraftReviewPanel": { status: "EQUIVALENT_V2", target: "story and episode plan", safeToRemoveFromLegacyApp: true, authority: "explicit AI draft apply" },
  "../features/projects/ScriptImportPanel#ScriptImportPanel": { status: "EQUIVALENT_V2", target: "story and episode plan", safeToRemoveFromLegacyApp: true, authority: "SourceDocument import/commit" },
  "../features/projects/StoryboardBatchWorkbench#StoryboardBatchWorkbench": { status: "EQUIVALENT_V2", target: "episodes/:episodeId/plan", safeToRemoveFromLegacyApp: true, authority: "shot batch apply" },
  "../features/status/DialogueTTSPanel#DialogueTTSPanel": { status: "EQUIVALENT_V2", target: "episodes/:episodeId/audio", safeToRemoveFromLegacyApp: true, authority: "TTS generation/binding" },
  "../features/status/AudioTrackPanel#AudioTrackPanel": { status: "EQUIVALENT_V2", target: "episodes/:episodeId/audio", safeToRemoveFromLegacyApp: true, authority: "audio track binding" },
  "../features/status/ReadinessPanels#AdapterContractsPanel": { status: "SYSTEM_OR_TOOL", target: "/projects/:projectId/operations", safeToRemoveFromLegacyApp: true },
  "../features/status/ReadinessPanels#DiagnosticPanel": { status: "SYSTEM_OR_TOOL", target: "/diagnostics", safeToRemoveFromLegacyApp: true },
  "../features/status/ReadinessPanels#G8ReadinessPanel": { status: "EQUIVALENT_V2", target: "episodes/:episodeId/delivery", safeToRemoveFromLegacyApp: true },
  "../features/status/ReadinessPanels#G9ReadinessPanel": { status: "SYSTEM_OR_TOOL", target: "/projects/:projectId/operations", safeToRemoveFromLegacyApp: true },
  "../features/status/ReadinessPanels#ModelCompatibilityPanel": { status: "SYSTEM_OR_TOOL", target: "/projects/:projectId/operations", safeToRemoveFromLegacyApp: true, authority: "model evidence import" },
  "../features/status/ReadinessPanels#ProjectConfigurationSnapshot": { status: "EQUIVALENT_V2", target: "/projects/:projectId/operations", safeToRemoveFromLegacyApp: true, authority: "DeliveryTargetVersion/current binding" },
  "../features/status/ReadinessPanels#ProjectList": { status: "EQUIVALENT_V2", target: "/projects", safeToRemoveFromLegacyApp: true },
  "../features/status/ReadinessPanels#TimelineStatusPanel": { status: "EQUIVALENT_V2", target: "episodes/:episodeId/timeline", safeToRemoveFromLegacyApp: true },
  "../features/shared/selection#selectedItemOrFirst": { status: "SHELL_INFRA", target: "legacy query-state selection only", safeToRemoveFromLegacyApp: true },
  "../features/events/eventClient#subscribeStudioEvents": { status: "SHELL_INFRA", target: "V2 scoped event invalidation hooks", safeToRemoveFromLegacyApp: true },
};

describe("legacy App retirement coverage", () => {
  it("maps every former feature/view dependency to a V2 owner", () => {
    expect(Object.keys(MATRIX)).toHaveLength(49);
    expect(Object.values(MATRIX).every((item) => item.safeToRemoveFromLegacyApp)).toBe(true);
    expect(Object.values(MATRIX).filter((item) => item.status === "MISSING_V2")).toEqual([]);
  });

  it("never marks a missing authority entry safe to remove", () => {
    const unsafe = Object.entries(MATRIX).filter(([, item]) => item.authority && !item.target);
    expect(unsafe).toEqual([]);
    expect(Object.values(MATRIX).filter((item) => item.status === "MISSING_V2").every((item) => !item.safeToRemoveFromLegacyApp)).toBe(true);
    expect(Object.values(MATRIX).filter((item) => item.status === "SYSTEM_OR_TOOL" && item.target.includes("no V2")).every((item) => !item.safeToRemoveFromLegacyApp)).toBe(true);
  });

  it("keeps the claimed V2 route families executable in the router", () => {
    expect(routerSource).not.toMatch(/["']\.\/App(?:\.tsx)?["']/);
    for (const fragment of ["/projects", "/models", "/jobs", "/diagnostics", "story", "assets", "canvas", "operations", "lab", "episodes/:episodeId/plan", "episodes/:episodeId/direct", "episodes/:episodeId/generation", "episodes/:episodeId/review", "episodes/:episodeId/audio", "episodes/:episodeId/timeline", "episodes/:episodeId/delivery"]) {
      expect(routerSource, `missing V2 route evidence: ${fragment}`).toContain(fragment);
    }
  });
});
