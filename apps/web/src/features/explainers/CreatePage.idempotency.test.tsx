/**
 * FE-A02: the create idempotency key must be bound to the WHOLE request.
 *
 * The page hashed only ``title:inputKind:targetSeconds``.  A user who fixed a wrong
 * configuration — a corrected topic, a different output set, a different automation
 * mode, a repaired script — clicked again with the SAME key and a DIFFERENT body, and
 * the server answered ``IDEMPOTENCY_PAYLOAD_MISMATCH`` instead of creating the
 * explainer.  A pure network retry (identical body) must still reuse the key.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createExplainer, preflightExplainerPlan } from "../../generated/api";
import { resetCommandIdCache } from "../../services/commandId";
import { ExplainerCreatePage } from "./CreatePage";

vi.mock("../../generated/api", () => ({
  createExplainer: vi.fn(),
  preflightExplainerPlan: vi.fn(),
  importExplainerSource: vi.fn(),
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
  fireEvent.click(screen.getByRole("button", { name: "检查并一键生成" }));
  await waitFor(() => expect(createExplainer).toHaveBeenCalled());
}

function lastKey(): string {
  const calls = vi.mocked(createExplainer).mock.calls;
  return String(calls[calls.length - 1][1]);
}

describe("explainer create idempotency (FE-A02)", () => {
  beforeEach(() => {
    resetCommandIdCache();
    vi.mocked(createExplainer).mockReset();
    vi.mocked(preflightExplainerPlan).mockReset();
    vi.mocked(createExplainer).mockResolvedValue({
      project: { id: "project-1" },
    } as never);
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
    fireEvent.change(screen.getByPlaceholderText("例如：灯塔最后一页值班记录"), {
      target: { value: "灯塔最后一页值班记录" },
    });
    fireEvent.change(screen.getByPlaceholderText("要讲清楚哪一个问题？"), {
      target: { value: "值班记录为什么重要" },
    });
    await createOnce();
    const firstKey = lastKey();

    // The user fixes the answer to "what is this about?" and retries.
    fireEvent.change(screen.getByPlaceholderText("要讲清楚哪一个问题？"), {
      target: { value: "为什么值班记录被撕掉了一页" },
    });
    await createOnce();
    expect(lastKey()).not.toBe(firstKey);

    // The corrected request really carried the new topic.
    const calls = vi.mocked(createExplainer).mock.calls;
    expect((calls[calls.length - 1][0] as { topic: string }).topic).toBe("为什么值班记录被撕掉了一页");
  });

  it("reuses the key for a pure retry of the identical request", async () => {
    mount();
    fireEvent.change(screen.getByPlaceholderText("例如：灯塔最后一页值班记录"), {
      target: { value: "同一份请求" },
    });
    fireEvent.change(screen.getByPlaceholderText("要讲清楚哪一个问题？"), {
      target: { value: "同一个主题" },
    });
    await createOnce();
    const firstKey = lastKey();
    await createOnce();
    expect(lastKey()).toBe(firstKey);
  });
});
