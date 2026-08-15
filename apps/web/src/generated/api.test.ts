import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiRequestError,
  bindEpisodeAudio,
  buildDeliveryPackage,
  createPostProcessRecipe,
  createShotTransitionConstraint,
  createSubtitleRevision,
  createTimelineRevision,
  createDeliveryTargetVersion,
  getDeliveryPackage,
  listDeliveryPackageFiles,
  listEpisodeDeliveryPackages,
  selectDeliveryTargetVersion,
  getFrameAnchor,
  getEnhancementRun,
  getDiagnostics,
  dryRunDiagnosticFix,
  scanLocalModelRegistry,
  getPostProcessRecipe,
  listPostProcessRecipes,
  planEnhancementRun,
  publishPostProcessRecipe,
  renderEpisode,
  resolveProfileCameraPlan,
  reviewInbox,
  reviewInboxPage,
  runEnhancement,
  submitGenerationVariant,
  verifyDeliveryPackage,
  withdrawDeliveryPackage,
} from "./api";

describe("generated G8 timeline client", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockImplementation(async (path: string) => ({
      ok: true,
      headers: { get: () => null },
      json: async () => path.endsWith("/session/bootstrap") ? { token: "test-token", mode: "LOCAL_ONLY" } : {},
    }));
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

  it("posts subtitle and authorized audio bindings through encoded episode paths", async () => {
    await createSubtitleRevision("episode/1", {
      cues: [{ start_us: 0, end_us: 500_000, text: "local" }],
      authority: { text_authority: "SCRIPT", source_document_version_id: "script-v1" },
      format: "SRT",
    });
    await bindEpisodeAudio("episode/1", { media_version_id: "audio/1", track_type: "DIALOGUE", start_us: 0, end_us: 500_000, source_license_status: "USER_OWNED", license_evidence_path_rel: "00_admin/audio-license.json" });
    const calls = fetchMock.mock.calls.filter(([path]) => !String(path).endsWith("/session/bootstrap"));
    expect(calls[0][0]).toBe("/api/v1/episodes/episode%2F1/subtitle-revisions");
    expect(calls[1][0]).toBe("/api/v1/episodes/episode%2F1/audio-bindings");
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
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 409,
      headers: { get: (name: string) => name === "X-Request-Id" ? "req-header-fallback" : null },
      json: async () => ({
        error: {
          code: "REVISION_CONFLICT",
          message: "版本已变化",
          request_id: "req-body-123",
          retryable: true,
          suggested_action: "刷新后重试",
        },
      }),
    });

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
    await reviewInbox("project/1", "", { media_kind: "VIDEO", episode_id: "episode/2", age: "OLD", priority: "HIGH", blocking: "BLOCKED", min_age_days: 7, max_age_days: 30 });
    await reviewInboxPage("project/1", 20, 10, "VIDEO", "OLD", "HIGH", "BLOCKED", "episode/2");
    expect(fetchMock.mock.calls.map(([path]) => path).filter((path) => !String(path).endsWith("/session/bootstrap"))).toEqual([
      "/api/v1/reviews/inbox?project_id=project%2F1&media_kind=VIDEO&episode_id=episode%2F2&age=OLD&priority=HIGH&blocking=BLOCKED&min_age_days=7&max_age_days=30",
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
});
