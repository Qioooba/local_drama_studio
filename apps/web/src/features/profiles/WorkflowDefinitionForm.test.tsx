import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { expect, it, vi } from "vitest";
import * as api from "../../generated/api";
import { WorkflowDefinitionForm } from "./WorkflowDefinitionForm";

vi.mock("../../generated/api", () => ({
  listWorkflowDefinitions: vi.fn().mockResolvedValue({ items: [{ code: "QWEN_IDENTITY_3", title: "三人参考", available: true, fields: { model: { type: "string", label: "模型文件", default: "Qwen/model.gguf", runtime_input: { class_type: "UnetLoaderGGUF", input: "unet_name" } } }, semantic_bindings: {} }] }),
  getWorkflowDefinitionRuntimeOptions: vi.fn().mockResolvedValue({ fields: { model: { options: [{ value: "Qwen\\model.gguf", label: "Qwen\\model.gguf" }] } } }),
  instantiateWorkflowDefinition: vi.fn().mockResolvedValue({ workflow_version: { code: "qwen_identity_3", version_no: 2 } }),
}));

it("requires selecting an installed filename and submits its exact runtime spelling", async () => {
  render(<WorkflowDefinitionForm disabled={false} onCreated={() => {}} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "从工作流定义创建候选" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "从工作流定义创建候选" }));
  const create = screen.getByRole("button", { name: "创建不可变候选版本" });
  expect(create).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "读取本机模型选项" }));
  await screen.findByRole("option", { name: "Qwen\\model.gguf" });
  expect(create).toBeDisabled();
  fireEvent.change(screen.getByLabelText("模型文件"), { target: { value: "Qwen\\model.gguf" } });
  expect(create).toBeEnabled();
  fireEvent.click(create);
  await waitFor(() => expect(api.instantiateWorkflowDefinition).toHaveBeenCalledWith("QWEN_IDENTITY_3", expect.objectContaining({ parameters: { model: "Qwen\\model.gguf" } })));
});
