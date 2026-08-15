import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DialogueTTSPanel } from "./DialogueTTSPanel";

describe("DialogueTTSPanel", () => {
  it("shows an honest empty blocked state without mock candidates", () => {
    render(<DialogueTTSPanel lines={[]} voices={[]} />);
    expect(screen.getByText("TTS PROFILE MISSING")).toBeTruthy();
    expect(screen.getByText("当前集没有对白文本 revision；未创建 Mock 候选。")).toBeTruthy();
  });

  it("projects immutable candidate provenance counts", () => {
    render(<DialogueTTSPanel lines={[{ id: "line", episode_id: "episode", shot_id: null, code: "DLG-001", speaker: "A", text_revisions: [{ id: "text", revision_no: 2, text: "line", text_hash: "h", pronunciation: {} }], candidates: [{ id: "candidate", dialogue_text_revision_id: "text", voice_profile_version_id: "voice", media_version_id: "media", emotion: "neutral", speech_rate: 1, seed: 42, model_ref: "local", candidate_kind: "PREVIEW", status: "READY", provenance: {} }], selection: null }]} voices={[]} />);
    expect(screen.getByText("DLG-001")).toBeTruthy();
    expect(screen.getByText("v2")).toBeTruthy();
    expect(screen.getByText("真实 TTS 生成保持阻塞")).toBeTruthy();
  });
});
