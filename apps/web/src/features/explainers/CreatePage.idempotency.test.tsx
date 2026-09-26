/**
 * FE-A02: the create idempotency key must be bound to the WHOLE request.
 *
 * The page hashed only ``title:inputKind:targetSeconds``.  A user who fixed a
 * wrong configuration — a corrected topic, a different output set, a different
 * automation mode, a repaired script — clicked again with the SAME key and a
 * DIFFERENT body, and the server answered ``IDEMPOTENCY_PAYLOAD_MISMATCH``
 * instead of creating the explainer.  A pure network retry (identical body) must
 * still reuse the key — which now also covers the orthogonal `script_policy`
 * choice of §C1.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createExplainer, listCapabilityOptions, preflightExplainerPlan } from "../../generated/api";
import { resetCommandIdCache } from "../../services/commandId";
import { ExplainerCreatePage } from "./CreatePage";

vi.mock("../../generated/api", () => ({
  createExplainer: vi.fn(),
  preflightExplainerPlan: vi.fn(),
  importExplainerSource: vi.fn(),
  startExplainerRun: vi.fn(),
  patchExplainerVisualPreferences: vi.fn(),
  listCapabilityOptions: vi.fn(),
}));

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ExplainerCreatePage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function createOnce() {
  fireEvent.click(screen.getByRole("button", { name: "一键生成到预览" }));
  await waitFor(() => expect(createExplainer).toHaveBeenCalled());
}

function lastKey(): string {
  const calls = vi.mocked(createExplainer).mock.calls;
  return String(calls[calls.length - 1][1]);
}

function lastPayload(): Record<string, unknown> {
  const calls = vi.mocked(createExplainer).mock.calls;
  return calls[calls.length - 1][0] as unknown as Record<string, unknown>;
}

describe("explainer create idempotency (FE-A02)", () => {
  beforeEach(() => {
    resetCommandIdCache();
    vi.mocked(createExplainer).mockReset();
    vi.mocked(preflightExplainerPlan).mockReset();
    vi.mocked(listCapabilityOptions).mockResolvedValue({
      capability: "LLM_STORY_PARSE",
      scope: { project_id: null, episode_id: null, shot_id: null },
      selection: { mode: "AUTO", source: "NONE", profile_version_id: null, ready: false, option: null, blockers: [] },
      options: [],
      configured_runtime: null,
      summary: { total_count: 0, selectable_count: 0, blocked_count: 0 },
      repair_href: "/system/capabilities",
      read_only: true,
      runtime_contacted: false,
      network_contacted: false,
      mutated: false,
    } as never);
    vi.mocked(createExplainer).mockResolvedValue({ project: { id: "project-1" }, video: { revision: 3 } } as never);
    vi.mocked(preflightExplainerPlan).mockResolvedValue({
      executable: true,
      plan_hash: "p".repeat(64),
      blockers: [],
      capability_snapshot: { probed: true },
      estimate: { stage: "ESTIMATED", note: "" },
      categories: {},
      task_skeleton: [],
    } as never);
  });

  afterEach(() => {
    cleanup();
  });

  it("rotates the key when the user fixes a wrong configuration", async () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: "从主题开始（次入口）" }));
    fireEvent.change(screen.getByLabelText("作品标题"), { target: { value: "灯塔最后一页值班记录" } });
    fireEvent.change(screen.getByLabelText("题目"), { target: { value: "值班记录为什么重要" } });
    await createOnce();
    const firstKey = lastKey();

    // The user fixes the answer to "what is this about?" and retries.
    fireEvent.change(screen.getByLabelText("题目"), { target: { value: "为什么值班记录被撕掉了一页" } });
    await createOnce();
    expect(lastKey()).not.toBe(firstKey);

    // The corrected request really carried the new topic.
    expect(lastPayload().topic).toBe("为什么值班记录被撕掉了一页");
    // …and the orthogonal processing policy travels with it (§C1).
    expect(lastPayload().script_policy).toBe("CREATE_FROM_TOPIC");
    expect(lastPayload().input_kind).toBe("TOPIC");
  });

  it("reuses the key for a pure retry of the identical request", async () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: "从主题开始（次入口）" }));
    fireEvent.change(screen.getByLabelText("作品标题"), { target: { value: "同一份请求" } });
    fireEvent.change(screen.getByLabelText("题目"), { target: { value: "同一个主题" } });
    await createOnce();
    const firstKey = lastKey();
    await createOnce();
    expect(lastKey()).toBe(firstKey);
  });

  it("rotates the key when the content policy changes even though the channel does not", async () => {
    mount();
    // Same TXT-style channel (粘贴正文), different policy → different command.
    fireEvent.click(screen.getByRole("button", { name: /已有口播稿/ }));
    fireEvent.change(screen.getByLabelText("作品标题"), { target: { value: "同一份正文" } });
    fireEvent.change(screen.getByLabelText("口播稿正文"), { target: { value: "同一段正文内容。" } });
    await createOnce();
    const preserveKey = lastKey();
    expect(lastPayload().script_policy).toBe("PRESERVE_ORIGINAL");

    fireEvent.click(screen.getByRole("button", { name: /故事资料/ }));
    await createOnce();
    expect(lastPayload().script_policy).toBe("ADAPT_SOURCES");
    expect(lastKey()).not.toBe(preserveKey);
  });
});
