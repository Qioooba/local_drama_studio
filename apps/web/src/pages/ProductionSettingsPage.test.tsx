import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getG9Readiness, getProjectConfiguration, listProjects } from "../generated/api";
import { ProductionSettingsPage } from "./ProductionSettingsPage";

vi.mock("../generated/api", () => ({
  getG9Readiness: vi.fn(),
  getProjectConfiguration: vi.fn(),
  listProjects: vi.fn(),
  listWorkspaceAssetAuthorizations: vi.fn().mockResolvedValue({ items: [] }),
  reviewInbox: vi.fn().mockResolvedValue({ items: [] }),
}));

vi.mock("../features/production-settings-v2/ProductionSettingsOverview", () => ({
  ProductionSettingsOverview: ({ projectId }: { projectId: string }) => <div>ProductionSettingsOverview {projectId}</div>,
}));

vi.mock("../features/freshness/FreshnessPanel", () => ({
  FreshnessPanel: ({ projectId }: { projectId: string }) => <div>FreshnessPanel {projectId}</div>,
}));

vi.mock("../features/status/ReadinessPanels", () => ({
  G9ReadinessPanel: ({ readiness }: { readiness: { episode: { code: string } } }) => <div>G9 {readiness.episode.code}</div>,
  ProjectConfigurationSnapshot: ({ projectId }: { projectId?: string }) => <div>Delivery target authority {projectId}</div>,
}));

vi.mock("../features/projects/ProjectAssetGrantPanel", () => ({ ProjectAssetGrantPanel: () => <div>ProjectAssetGrantPanel</div> }));
vi.mock("../features/projects/ProjectPackageAction", () => ({ ProjectPackageAction: () => <div>ProjectPackageAction</div> }));
vi.mock("../features/projects/ProjectTemplateCopyAction", () => ({ ProjectTemplateCopyAction: () => <div>ProjectTemplateCopyAction</div> }));
vi.mock("../features/shared/AutomationPanel", () => ({ AutomationPanel: () => <div>AutomationPanel</div> }));
vi.mock("../features/shared/AutomationWorkflowPanel", () => ({ AutomationWorkflowPanel: () => <div>AutomationWorkflowPanel</div> }));
vi.mock("../features/shared/BrandKitPanel", () => ({ BrandKitPanel: () => <div>BrandKitPanel</div> }));
vi.mock("../features/shared/OutboxDeliveryPanel", () => ({ OutboxDeliveryPanel: () => <div>OutboxDeliveryPanel</div> }));
vi.mock("../features/shared/ProjectHealthPanel", () => ({ ProjectHealthPanel: () => <div>ProjectHealthPanel</div> }));
vi.mock("../features/shared/WorkspaceAssetAuthorizationPanel", () => ({ WorkspaceAssetAuthorizationPanel: () => <div>WorkspaceAssetAuthorizationPanel</div> }));

const configuration = { production_plan: null, selected_delivery_target_version_id: null, delivery_targets: [], profile_bindings: [], impact: { remote_transport_allowed: false } };
const g9 = { episode: { id: "episode-1", code: "EP01", title: "Pilot" }, status: "IN_PROGRESS", checks: [], evidence: {} };

describe("ProductionSettingsPage tabbed structure", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listProjects).mockResolvedValue({ items: [{ id: "project-1", code: "P1", title: "Project One", status: "ACTIVE" }] } as never);
    vi.mocked(getProjectConfiguration).mockResolvedValue({ configuration } as never);
    vi.mocked(getG9Readiness).mockResolvedValue({ readiness: g9 } as never);
  });

  it("renders overview tab by default and switches between tabs without losing context", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/projects/project-1/production-settings"]}>
          <Routes>
            <Route path="/projects/:projectId/production-settings" element={<ProductionSettingsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    expect(await screen.findByText("ProductionSettingsOverview project-1")).toBeTruthy();
    expect(screen.queryByText("FreshnessPanel project-1")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "时效与失效" }));
    expect(await screen.findByText("FreshnessPanel project-1")).toBeTruthy();
    expect(screen.queryByText("ProductionSettingsOverview project-1")).toBeNull();

    // Switch to delivery tab
    fireEvent.click(screen.getByText(/交付目标与品牌/i));
    expect(await screen.findByText("Delivery target authority project-1")).toBeTruthy();
    expect(screen.getByText("BrandKitPanel")).toBeTruthy();
    expect(screen.getByText("ProjectHealthPanel")).toBeTruthy();
    expect(screen.getByText("G9 EP01")).toBeTruthy();

    // Switch to automation tab
    fireEvent.click(screen.getByText(/自动化与外发/i));
    expect(await screen.findByText("AutomationPanel")).toBeTruthy();
    expect(screen.getByText("AutomationWorkflowPanel")).toBeTruthy();
    expect(screen.getByText("OutboxDeliveryPanel")).toBeTruthy();

    // Switch to assets tab
    fireEvent.click(screen.getByText(/授权与项目包/i));
    expect(await screen.findByText("WorkspaceAssetAuthorizationPanel")).toBeTruthy();
    expect(screen.getByText("ProjectAssetGrantPanel")).toBeTruthy();
    expect(screen.getByText("ProjectPackageAction")).toBeTruthy();
    expect(screen.getByText("ProjectTemplateCopyAction")).toBeTruthy();
  });
});
