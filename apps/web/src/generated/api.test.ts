import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiRequestError,
  appendProjectEpisode,
  buildDeliveryPackage,
  commitEpisodeTimelineRefresh,
  createPostProcessRecipe,
  createShotTransitionConstraint,
  createSubtitleRevision,
  createTimelineRevision,
  createEpisodeTimelineDraftV2,
  createDeliveryTargetVersion,
  planEpisodeTimelineRefresh,
  getDeliveryPackage,
  listDeliveryPackageFiles,
  listEpisodeDeliveryPackages,
  selectDeliveryTargetVersion,
  getFrameAnchor,
  getEnhancementRun,
  getAuditProof,
  getDiagnostics,
  dryRunDiagnosticFix,
  scanLocalModelRegistry,
  getPostProcessRecipe,
  getProjectCreatorSetup,
  getEpisodeEditWorkspaceV2,
  getProjectEpisodeCatalog,
  listPostProcessRecipes,
  planEnhancementRun,
  publishPostProcessRecipe,
  renderEpisode,
  resolveProfileCameraPlan,
  reviewInbox,
  reviewInboxPage,
  requestJson,
  runEnhancement,
  submitGenerationVariant,
  verifyDeliveryPackage,
  withdrawDeliveryPackage,
  freezeEpisodeTimelineV2,
} from "./api";
import { apiJsonResponse } from "../test/apiResponse";

describe("generated G8 timeline client", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockImplementation(async (path: string) => apiJsonResponse(
      path.endsWith("/session/bootstrap") ? { token: "test-token", mode: "LOCAL_ONLY" } : {},
    ));
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("posts an immutable timeline revision with its input snapshot", async () => {
    await createTimelineRevision("episode/1", {
      items: [{ track_type: "VIDEO", media_version_id: "media-1", start_us: 0, end_us: 1_000_000 }],
      input_snapshot: { source_revision: 3 },
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/episodes/episode%2F1/timeline-revisions",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ items: [{ track_type: "VIDEO", media_version_id: "media-1", start_us: 0, end_us: 1_000_000 }], input_snapshot: { source_revision: 3 } }) }),
    );
    const mutation = fetchMock.mock.calls.find(([path]) => path === "/api/v1/episodes/episode%2F1/timeline-revisions");
    expect(new Headers(mutation?.[1]?.headers).get("X-Local-Instance-Token")).toBe("test-token");
  });

  it("uses the typed Edit v2 aggregate and separate draft/freeze commands", async () => {
    await getEpisodeEditWorkspaceV2("episode/1", { historyLimit: 12 });
    await createEpisodeTimelineDraftV2("episode/1", { clips: [{ shot_id: "shot-1", media_version_id: "media-1", duration_us: 1_000_000 }], expected_latest_revision_id: null, expected_upstream_fingerprint: "a".repeat(64), idempotency_key: "draft-1" });
    await freezeEpisodeTimelineV2("timeline/1", { expected_latest_revision_id: "timeline/1", expected_upstream_fingerprint: "a".repeat(64), idempotency_key: "freeze-1" });
    expect(fetchMock.mock.calls.map(([path]) => path).filter((path) => !String(path).endsWith("/session/bootstrap"))).toEqual([
      "/api/v2/episodes/episode%2F1/post/edit?history_limit=12",
      "/api/v2/episodes/episode%2F1/post/edit/timeline-drafts",
      "/api/v2/post/edit/timeline-revisions/timeline%2F1:freeze",
    ]);
  });

  it("preflights and commits stale timeline refresh with the exact plan hash", async () => {
    await planEpisodeTimelineRefresh("episode/1");
    await commitEpisodeTimelineRefresh("episode/1", "plan/hash");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/episodes/episode%2F1/timeline-refresh:plan",
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/episodes/episode%2F1/timeline-refresh:commit", expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ expected_plan_hash: "plan/hash" }),
    }));
  });

  it("loads the global season and episode context with one project catalog request", async () => {
    await getProjectEpisodeCatalog("project/one");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/projects/project%2Fone/episode-catalog",
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
  });

  it("loads first-production milestones through one read-only setup request", async () => {
    await getProjectCreatorSetup("project/one");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/projects/project%2Fone/creator-setup",
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
  });

  it("appends one project episode through the encoded transactional structure endpoint", async () => {
    const payload = { season_id: "season-1", create_new_season: false, episode_title: "追加篇", target_duration_ms: 60_000 };
    await appendProjectEpisode("project/one", payload);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/projects/project%2Fone/episodes:append",
      expect.objectContaining({ method: "POST", body: JSON.stringify(payload) }),
    );
  });

  it("rebootstraps and retries one rejected write after the local API process restarts", async () => {
    let bootstrapCalls = 0;
    let mutationCalls = 0;
    fetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path.endsWith("/session/bootstrap")) {
        bootstrapCalls += 1;
        return apiJsonResponse({ token: bootstrapCalls === 1 ? "old-token" : "new-token", mode: "LOCAL_ONLY" });
      }
      mutationCalls += 1;
      const token = new Headers(init?.headers).get("X-Local-Instance-Token");
      if (mutationCalls === 1) {
        expect(token).toBe("old-token");
        return apiJsonResponse(
          { error: { code: "CSRF_TOKEN_REQUIRED", message: "instance token expired" } },
          { status: 403, headers: { "X-Request-Id": "request-old" } },
        );
      }
      expect(token).toBe("new-token");
      return apiJsonResponse({ saved: true });
    });

    await expect(requestJson<{ saved: boolean }>("/api/v1/test-write", { method: "POST" }, "http://127.0.0.1:3999")).resolves.toEqual({ saved: true });
    expect(bootstrapCalls).toBe(2);
    expect(mutationCalls).toBe(2);
  });

  it("posts subtitles through encoded episode paths", async () => {
    await createSubtitleRevision("episode/1", {
      cues: [{ start_us: 0, end_us: 500_000, text: "local" }],
      authority: { text_authority: "SCRIPT", source_document_version_id: "script-v1" },
      format: "SRT",
    });
    const calls = fetchMock.mock.calls.filter(([path]) => !String(path).endsWith("/session/bootstrap"));
    expect(calls[0][0]).toBe("/api/v1/episodes/episode%2F1/subtitle-revisions");
  });

  it("uses explicit render and delivery endpoints without hidden requests", async () => {
    await renderEpisode("timeline/1");
    await buildDeliveryPackage({ episode_render_version_id: "render/1", target_version_id: "target/1" });
    await verifyDeliveryPackage("package/1");
    await withdrawDeliveryPackage("package/1", "integrity review");
    expect(fetchMock.mock.calls.map(([path]) => path).filter((path) => !String(path).endsWith("/session/bootstrap"))).toEqual([
      "/api/v1/timeline-revisions/timeline%2F1:render",
      "/api/v1/delivery-packages",
      "/api/v1/delivery-packages/package%2F1:verify",
      "/api/v1/delivery-packages/package%2F1:withdraw",
    ]);
  });

  it("exposes delivery history, immutable target versions, and file verification paths", async () => {
    await listEpisodeDeliveryPackages("episode/1");
    await getDeliveryPackage("package/1");
    await listDeliveryPackageFiles("package/1");
    await createDeliveryTargetVersion("project/1", "target/1", { spec: { path_rel: "06_delivery/v2", width: 640, height: 360, fps: 30 } });
    await selectDeliveryTargetVersion("project/1", "version/2");
    expect(fetchMock.mock.calls.map(([path]) => path).filter((path) => !String(path).endsWith("/session/bootstrap"))).toEqual([
      "/api/v1/episodes/episode%2F1/delivery-packages",
      "/api/v1/delivery-packages/package%2F1",
      "/api/v1/delivery-packages/package%2F1/files",
      "/api/v1/projects/project%2F1/delivery-targets/target%2F1/versions",
      "/api/v1/delivery-target-versions/version%2F2:select?project_id=project%2F1",
    ]);
  });

  it("covers frame anchors, transition constraints, recipes, and enhancement runs", async () => {
    await getFrameAnchor("anchor/1");
    await createShotTransitionConstraint({
      from_shot_id: "shot-1",
      to_shot_id: "shot-2",
      constraint_type: "POSE_CONTINUITY",
    });
    await createPostProcessRecipe({ code: "scale", title: "Scale", steps: [{ kind: "SCALE" }] });
    await listPostProcessRecipes();
    await getPostProcessRecipe("recipe/1");
    await publishPostProcessRecipe("recipe/1");
    await planEnhancementRun({ input_media_version_id: "media/1", recipe_id: "recipe/1" });
    await runEnhancement({ input_media_version_id: "media/1", recipe_id: "recipe/1", plan_hash: "a".repeat(64) });
    await getEnhancementRun("run/1");
    expect(fetchMock.mock.calls.map(([path]) => path).filter((path) => !String(path).endsWith("/session/bootstrap"))).toEqual([
      "/api/v1/frame-anchors/anchor%2F1",
      "/api/v1/shot-transitions",
      "/api/v1/post-process-recipes",
      "/api/v1/post-process-recipes",
      "/api/v1/post-process-recipes/recipe%2F1",
      "/api/v1/post-process-recipes/recipe%2F1:publish",
      "/api/v1/enhancement-runs:plan",
      "/api/v1/enhancement-runs",
      "/api/v1/enhancement-runs/run%2F1",
    ]);
  });

  it("resolves CameraPlan against one explicit local Profile without runtime contact", async () => {
    await resolveProfileCameraPlan("profile/1", { shot_type: "CLOSEUP", movement: "PUSH_IN", direction: "FORWARD", intensity: 0.5, curve: "LINEAR" });
    expect(fetchMock.mock.calls.map(([path]) => path).filter((path) => !String(path).endsWith("/session/bootstrap"))).toEqual([
      "/api/v1/profile-versions/profile%2F1:resolve-camera-plan",
    ]);
  });

  it("submits a confirmed immutable Variant plan through the atomic Job endpoint", async () => {
    await submitGenerationVariant({ intent_id: "intent-1", variant_type: "BASE", branch_reason: "ui", profile_version_id: "profile-1", seed_policy: "EXPLICIT", explicit_seed: 42, plan_hash: "a".repeat(64), idempotency_key: "variant-submit-1" });
    expect(fetchMock.mock.calls.map(([path]) => path).filter((path) => !String(path).endsWith("/session/bootstrap"))).toEqual([
      "/api/v1/generation-variants:submit",
    ]);
  });

  it("surfaces structured API failures with a traceable request ID", async () => {
    fetchMock.mockResolvedValueOnce(apiJsonResponse(
      {
        error: {
          code: "REVISION_CONFLICT",
          message: "版本已变化",
          request_id: "req-body-123",
          retryable: true,
          suggested_action: "刷新后重试",
        },
      },
      { status: 409, headers: { "X-Request-Id": "req-header-fallback" } },
    ));

    const error = await getFrameAnchor("stale-anchor").catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(ApiRequestError);
    expect(error).toMatchObject({
      status: 409,
      code: "REVISION_CONFLICT",
      requestId: "req-body-123",
      retryable: true,
      suggestedAction: "刷新后重试",
    });
    expect(String(error)).toContain("请求 ID req-body-123");
  });

  it("encodes cross-project review inbox filters before requesting a page", async () => {
    await reviewInbox("project/1", "", { media_kind: "VIDEO", episode_id: "episode/2", age: "OLD", priority: "HIGH", blocking: "BLOCKED", min_age_days: 7, max_age_days: 30, include_resolved: true });
    await reviewInboxPage("project/1", 20, 10, "VIDEO", "OLD", "HIGH", "BLOCKED", "episode/2");
    expect(fetchMock.mock.calls.map(([path]) => path).filter((path) => !String(path).endsWith("/session/bootstrap"))).toEqual([
      "/api/v1/reviews/inbox?project_id=project%2F1&media_kind=VIDEO&episode_id=episode%2F2&age=OLD&priority=HIGH&blocking=BLOCKED&min_age_days=7&max_age_days=30&include_resolved=true",
      "/api/v1/reviews/inbox?project_id=project%2F1&media_kind=VIDEO&age=OLD&priority=HIGH&blocking=BLOCKED&episode_id=episode%2F2&cursor=20&limit=10",
    ]);
  });

  it("keeps diagnostics and model registry calls local and explicit", async () => {
    await getDiagnostics();
    await dryRunDiagnosticFix("GPU_DRIVER_CUDA");
    await scanLocalModelRegistry("C:/models selected", 12);
    expect(fetchMock.mock.calls.map(([path]) => path).filter((path) => !String(path).endsWith("/session/bootstrap"))).toEqual([
      "/api/v1/diagnostics",
      "/api/v1/diagnostics/GPU_DRIVER_CUDA:dry-run-fix",
      "/api/v1/model-registry:scan",
    ]);
    expect(fetchMock.mock.calls.at(-1)?.[1]?.body).toBe(JSON.stringify({ root_path: "C:/models selected", max_files: 12 }));
  });

  it("requests the bounded redacted audit export proof with stable filters", async () => {
    await getAuditProof({ project_id: "project/1", action: "REVIEW_SUBMITTED", cursor: 42, limit: 50 }, 25);
    expect(fetchMock.mock.calls.map(([path]) => path).filter((path) => !String(path).endsWith("/session/bootstrap"))).toEqual([
      "/api/v1/audit-events/proof?project_id=project%2F1&action=REVIEW_SUBMITTED&max_events=25",
    ]);
  });
});
