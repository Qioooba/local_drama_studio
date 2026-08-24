import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { copyProjectTemplate } from "../../generated/api";
import { ProjectTemplateCopyAction } from "./ProjectTemplateCopyAction";

vi.mock("../../generated/api", () => ({ copyProjectTemplate: vi.fn() }));

describe("ProjectTemplateCopyAction", () => {
  it("discloses exclusions and submits an explicit clean template copy", async () => {
    const copied = { id: "copy", code: "source_copy", title: "Source 副本", status: "DRAFT", revision: 1 };
    vi.mocked(copyProjectTemplate).mockResolvedValue({ project: copied, copy_report: { source_project_id: "source", copied: {}, excluded: ["media"] } });
    const onCopied = vi.fn();
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    render(<QueryClientProvider client={client}><ProjectTemplateCopyAction project={{ id: "source", code: "source", title: "Source", status: "ACTIVE", revision: 3 }} onCopied={onCopied} /></QueryClientProvider>);
    fireEvent.click(screen.getByRole("button", { name: "复制为新剧模板" }));
    expect(screen.getByText(/不复制媒体、角色授权资产、BrandKit、任务、审核或交付历史/)).toBeTruthy();
    const generatedCode = screen.getByText("新项目标识").parentElement?.querySelector("strong")?.textContent;
    expect(generatedCode).toMatch(/^source_copy_/);
    fireEvent.change(screen.getByLabelText("新项目标题"), { target: { value: "干净副本" } });
    fireEvent.click(screen.getByRole("button", { name: "确认复制" }));
    await waitFor(() => expect(copyProjectTemplate).toHaveBeenCalledWith("source", { code: generatedCode, title: "干净副本" }));
    expect(onCopied).toHaveBeenCalledWith(copied);
  });
});
