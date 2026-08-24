import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../generated/api";
import { BrandKitPanel } from "./BrandKitPanel";

vi.mock("../../generated/api", () => ({
  createBrandKit: vi.fn(),
  createCompliancePolicy: vi.fn(),
  createWatermarkProfile: vi.fn(),
  listBrandControls: vi.fn(),
}));

describe("BrandKitPanel creator-facing publishing rules", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listBrandControls).mockResolvedValue({ brand_kits: [], watermark_profiles: [], compliance_policies: [] });
    vi.mocked(api.createBrandKit).mockResolvedValue({ brand_kit: { version_no: 2 } });
    vi.mocked(api.createWatermarkProfile).mockResolvedValue({ watermark_profile: { version_no: 3 } });
    vi.mocked(api.createCompliancePolicy).mockResolvedValue({ compliance_policy: { version_no: 4 } });
  });

  it("derives all technical payloads from one semantic form and one save action", async () => {
    render(<BrandKitPanel projectId="project-1" />);
    expect(screen.queryByLabelText("BrandKit 代码")).toBeNull();
    expect(screen.queryByLabelText("BrandKit 令牌 JSON")).toBeNull();
    expect(screen.queryByLabelText("最长时长（毫秒）")).toBeNull();
    fireEvent.change(screen.getByLabelText("规范名称"), { target: { value: "第一季发布规范" } });
    fireEvent.change(screen.getByLabelText("品牌主色"), { target: { value: "#e4572e" } });
    fireEvent.change(screen.getByLabelText("水印文字"), { target: { value: "第一季样片" } });
    fireEvent.change(screen.getByLabelText("水印位置"), { target: { value: "TOP_LEFT" } });
    fireEvent.change(screen.getByLabelText("单条作品最长时长"), { target: { value: "180" } });
    fireEvent.click(screen.getByRole("button", { name: "保存发布规则" }));

    await waitFor(() => expect(api.createCompliancePolicy).toHaveBeenCalled());
    expect(api.createBrandKit).toHaveBeenCalledWith("project-1", expect.objectContaining({
      code: expect.stringMatching(/^brand-/),
      title: "第一季发布规范",
      tokens: expect.objectContaining({ colors: { primary: "#e4572e" } }),
    }));
    expect(api.createWatermarkProfile).toHaveBeenCalledWith("project-1", expect.objectContaining({
      config: expect.objectContaining({ text: "第一季样片", position: "TOP_LEFT" }),
    }));
    expect(api.createCompliancePolicy).toHaveBeenCalledWith("project-1", expect.objectContaining({
      rules: expect.objectContaining({ max_duration_ms: 180000, require_watermark: true }),
    }));
    expect(await screen.findByText(/视觉 v2、水印 v3、审核 v4/)).toBeTruthy();
  });
});
