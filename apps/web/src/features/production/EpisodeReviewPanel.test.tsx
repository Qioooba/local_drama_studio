import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { EpisodeReviewPanel } from "./EpisodeReviewPanel";

vi.mock("../../generated/api", () => ({ submitEpisodeRenderReview: vi.fn() }));

const templates = [{
  id: "template-1",
  code: "episode_render",
  subject_type: "EPISODE_RENDER_VERSION",
  items: [{ id: "decode", label: "可解码", required: true }, { id: "audio", label: "音轨混音", required: true }],
}] as never;

describe("EpisodeReviewPanel", () => {
  it("explains every submit blocker before enabling review", () => {
    render(<EpisodeReviewPanel render={{ id: "render-1", revision: 1 }} templates={templates} />);
    const submit = screen.getByRole("button", { name: "提交整集审核" });
    expect(submit).toHaveProperty("disabled", true);
    expect(screen.getByText(/还需完成 2 个必填检查/)).toBeTruthy();
    for (const group of ["可解码 *", "音轨混音 *"]) fireEvent.click(screen.getByRole("group", { name: group }).querySelector("input")!);
    expect(submit).toHaveProperty("disabled", false);
    fireEvent.change(screen.getByLabelText("审核决定"), { target: { value: "REJECTED" } });
    expect(submit).toHaveProperty("disabled", true);
    expect(screen.getByText(/拒绝整集渲染必须填写原因/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "节奏需要调整" }));
    expect(screen.getByLabelText("备注 / 拒绝原因")).toHaveProperty("value", "节奏需要调整");
    expect(submit).toHaveProperty("disabled", false);
  });
});
