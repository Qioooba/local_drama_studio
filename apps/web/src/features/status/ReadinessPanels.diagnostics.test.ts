import { describe, expect, it } from "vitest";
import {
  diagnosticCategoryLabel,
  diagnosticCheckDescription,
  diagnosticCheckTitle,
  diagnosticObservedSummary,
  diagnosticRemediationText,
  diagnosticStatusLabel,
} from "../diagnostics/DiagnosticsOverview";

describe("本机环境诊断中文展示", () => {
  it("把内部状态和检查代码转换为中文", () => {
    expect(diagnosticStatusLabel("DEGRADED")).toBe("需要处理");
    expect(diagnosticStatusLabel("PASS")).toBe("正常");
    expect(diagnosticCategoryLabel("runtime")).toBe("生成与语言服务");
    expect(diagnosticCheckTitle("COMFYUI_NODE_REGISTRY")).toBe("生成工作流节点");
    expect(diagnosticCheckDescription("FFMPEG")).toContain("视频转码");
    expect(diagnosticRemediationText("COMFYUI_NODE_REGISTRY", "fallback")).toContain("ComfyUI 节点");
  });

  it("用人能看懂的句子概括检查数据", () => {
    expect(diagnosticObservedSummary("DISK_SPACE", { free_bytes: 20 * 1024 ** 3, total_bytes: 100 * 1024 ** 3 }))
      .toBe("剩余 20.0 GB，总容量 100.0 GB");
    expect(diagnosticObservedSummary("COMFYUI_NODE_REGISTRY", { capability_count: 5, missing_capability_nodes: ["V2V"] }))
      .toBe("已登记 5 类能力；仍缺少：V2V");
    expect(diagnosticObservedSummary("REMOTE_PROVIDER", { automatic_remote_fallback: false }))
      .toBe("不会自动切换到远程或云端服务");
    expect(diagnosticObservedSummary("FFMPEG", { version: "ffmpeg version 8.1.2-full_build Copyright" }))
      .toBe("已安装 FFmpeg 8.1.2-full_build");
    expect(diagnosticObservedSummary("GPU_MANIFEST", { name: "RTX 3090 Ti", total_bytes: null }))
      .toBe("RTX 3090 Ti；显存 未知");
  });
});
