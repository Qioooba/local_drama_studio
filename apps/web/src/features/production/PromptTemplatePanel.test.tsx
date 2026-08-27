import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPrompt, listPrompts, type Profile } from "../../generated/api";
import { PromptTemplatePanel } from "./PromptTemplatePanel";

vi.mock("../../generated/api", () => ({ createPrompt: vi.fn(), listPrompts: vi.fn() }));

const profile = { id: "profile", code: "i2v", title: "I2V Profile", version_id: "profile-v1", capability: "I2V", status: "PUBLISHED" } as Profile;
function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><PromptTemplatePanel projectId="project-1" shot={{ id: "shot-1", current_revision: { subject_action: "turn" } }} profiles={[profile]} /></QueryClientProvider>);
}

describe("PromptTemplatePanel", () => {
  beforeEach(() => {
    vi.mocked(listPrompts).mockReset().mockResolvedValue({ items: [] });
    vi.mocked(createPrompt).mockReset().mockResolvedValue({ prompt: { id: "prompt" }, revision: { id: "revision", prompt_id: "prompt", revision_no: 1, parent_revision_id: null, content_text: "turn, cinematic", structured: {}, content_hash: "a".repeat(64), status: "FROZEN" } });
  });

  it("freezes source fields, template, expansion, negative text, language and profile", async () => {
    renderPanel();
    fireEvent.change(screen.getByLabelText("标题"), { target: { value: "主镜提示词" } });
    fireEvent.change(screen.getByLabelText("语言"), { target: { value: "en" } });
    fireEvent.change(screen.getByLabelText("模型配置"), { target: { value: "profile-v1" } });
    fireEvent.change(screen.getByLabelText("模板"), { target: { value: "{subject_action}, cinematic" } });
    fireEvent.change(screen.getByLabelText("最终提示词"), { target: { value: "turn, cinematic" } });
    fireEvent.change(screen.getByLabelText("负向词"), { target: { value: "flicker" } });
    fireEvent.click(screen.getByRole("button", { name: "冻结最终提示词" }));
    await waitFor(() => expect(createPrompt).toHaveBeenCalled());
    const payload = vi.mocked(createPrompt).mock.calls[0][0];
    expect(payload.content_text).toBe("turn, cinematic");
    expect(payload.structured).toEqual({ source_fields: { subject_action: "turn" }, template_text: "{subject_action}, cinematic", expanded_text: "turn, cinematic", negative_text: "flicker", language: "en", model_profile_version_id: "profile-v1" });
  });

  it("keeps freeze disabled until every explicit field is selected", () => {
    renderPanel();
    expect((screen.getByRole("button", { name: "冻结最终提示词" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("renders explicit fallbacks for legacy prompts without language or profile identity", async () => {
    vi.mocked(listPrompts).mockResolvedValue({ items: [{ id: "legacy", title: "旧提示词", revision_no: 1, content_text: "legacy", content_hash: "b".repeat(64), structured: {}, status: "FROZEN" }] });
    renderPanel();
    expect(await screen.findByText("语言未记录 · 历史生成配置")).toBeTruthy();
    fireEvent.click(screen.getByText("高级：查看校验信息"));
    expect(screen.getByText("未记录")).toBeTruthy();
    expect(document.body.textContent).not.toContain("undefined");
  });

  it("reuses another shot's frozen prompt only after an explicit apply action", async () => {
    vi.mocked(listPrompts).mockImplementation(async (_projectId, ownerType) => ownerType ? { items: [] } : { items: [{
      id: "reuse-1", owner_type: "SHOT", owner_id: "shot-2", purpose: "GENERATION_TEMPLATE", title: "同场角色近景",
      revision_no: 2, content_text: "mother close-up, warm light", content_hash: "c".repeat(64), status: "FROZEN",
      structured: { template_text: "{character}, close-up", expanded_text: "mother close-up, warm light", negative_text: "flicker", language: "en", model_profile_version_id: "profile-v1" },
    }] });
    renderPanel();
    expect((screen.getByLabelText("最终提示词") as HTMLTextAreaElement).value).toBe("");
    fireEvent.click(await screen.findByRole("button", { name: "采用为当前草稿" }));
    expect((screen.getByLabelText("最终提示词") as HTMLTextAreaElement).value).toBe("mother close-up, warm light");
    expect((screen.getByLabelText("负向词") as HTMLTextAreaElement).value).toBe("flicker");
    expect((screen.getByLabelText("语言") as HTMLSelectElement).value).toBe("en");
    expect(createPrompt).not.toHaveBeenCalled();
  });
});
