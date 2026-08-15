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
    fireEvent.change(screen.getByLabelText("模型 Profile"), { target: { value: "profile-v1" } });
    fireEvent.change(screen.getByLabelText("模板"), { target: { value: "{subject_action}, cinematic" } });
    fireEvent.change(screen.getByLabelText("展开结果"), { target: { value: "turn, cinematic" } });
    fireEvent.change(screen.getByLabelText("负向词"), { target: { value: "flicker" } });
    fireEvent.click(screen.getByRole("button", { name: "冻结展开结果" }));
    await waitFor(() => expect(createPrompt).toHaveBeenCalled());
    const payload = vi.mocked(createPrompt).mock.calls[0][0];
    expect(payload.content_text).toBe("turn, cinematic");
    expect(payload.structured).toEqual({ source_fields: { subject_action: "turn" }, template_text: "{subject_action}, cinematic", expanded_text: "turn, cinematic", negative_text: "flicker", language: "en", model_profile_version_id: "profile-v1" });
  });

  it("keeps freeze disabled until every explicit field is selected", () => {
    renderPanel();
    expect((screen.getByRole("button", { name: "冻结展开结果" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
