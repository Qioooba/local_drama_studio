import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API_CONTRACT_VERSION, systemContract } from "../generated/api";
import { ApiCompatibilityGate } from "./ApiCompatibilityGate";

vi.mock("../generated/api", async (loadOriginal) => {
  const original = await loadOriginal<typeof import("../generated/api")>();
  return { ...original, systemContract: vi.fn() };
});

function renderGate() {
  return render(
    <ApiCompatibilityGate><p>工作区已载入</p></ApiCompatibilityGate>,
  );
}

describe("ApiCompatibilityGate", () => {
  beforeEach(() => vi.mocked(systemContract).mockReset());
  afterEach(cleanup);

  it("renders the application only after the API contract matches", async () => {
    vi.mocked(systemContract).mockResolvedValue({
      app: "LocalDramaStudio",
      version: "0.1.0",
      api_contract_version: API_CONTRACT_VERSION,
      mode: "LOCAL_ONLY",
      network_mode: "LOCAL_ONLY",
    });
    renderGate();
    expect(await screen.findByText("工作区已载入")).toBeInTheDocument();
  });

  it("blocks every route when the API process belongs to another contract", async () => {
    vi.mocked(systemContract).mockResolvedValue({
      app: "LocalDramaStudio",
      version: "0.1.0",
      api_contract_version: "legacy.contract",
      mode: "LOCAL_ONLY",
      network_mode: "LOCAL_ONLY",
    } as unknown as Awaited<ReturnType<typeof systemContract>>);
    renderGate();
    expect(await screen.findByRole("heading", { name: "界面与本地服务版本不一致" })).toBeInTheDocument();
    expect(screen.queryByText("工作区已载入")).not.toBeInTheDocument();
    expect(screen.getByText(/项目文件、任务和不可变版本均未修改/)).toBeInTheDocument();
  });
});
