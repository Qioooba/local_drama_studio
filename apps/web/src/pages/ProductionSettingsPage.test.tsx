import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getProjectConfiguration, listProjects } from "../generated/api";
import { ProductionSettingsPage } from "./ProductionSettingsPage";

vi.mock("../generated/api", () => ({ getProjectConfiguration: vi.fn(), listProjects: vi.fn(), listWorkspaceAssetAuthorizations: vi.fn().mockResolvedValue({ items: [] }), reviewInbox: vi.fn().mockResolvedValue({ items: [] }) }));
vi.mock("../features/production-settings-v2/ProductionSettingsOverview", () => ({ ProductionSettingsOverview: ({ projectId }: { projectId: string }) => <div>ProductionSettingsOverview {projectId}</div> }));
vi.mock("../features/production-settings-v2/MediaDerivativeMaintenancePanel", () => ({ MediaDerivativeMaintenancePanel: () => <div>MediaDerivativeMaintenancePanel</div> }));
vi.mock("../features/status/ReadinessPanels", () => ({ ProjectConfigurationSnapshot: ({ projectId }: { projectId?: string }) => <div>Delivery target authority {projectId}</div> }));
vi.mock("../features/projects/ProjectAssetGrantPanel", () => ({ ProjectAssetGrantPanel: () => <div>ProjectAssetGrantPanel</div> }));
vi.mock("../features/projects/ProjectPackageAction", () => ({ ProjectPackageAction: () => <div>ProjectPackageAction</div> }));
vi.mock("../features/projects/ProjectTemplateCopyAction", () => ({ ProjectTemplateCopyAction: () => <div>ProjectTemplateCopyAction</div> }));
vi.mock("../features/shared/AutomationPanel", () => ({ AutomationPanel: () => <div>AutomationPanel</div> }));
vi.mock("../features/shared/AutomationWorkflowPanel", () => ({ AutomationWorkflowPanel: () => <div>AutomationWorkflowPanel</div> }));
vi.mock("../features/shared/BrandKitPanel", () => ({ BrandKitPanel: () => <div>BrandKitPanel</div> }));
vi.mock("../features/shared/OutboxDeliveryPanel", () => ({ OutboxDeliveryPanel: () => <div>OutboxDeliveryPanel</div> }));
vi.mock("../features/shared/WorkspaceAssetAuthorizationPanel", () => ({ WorkspaceAssetAuthorizationPanel: () => <div>WorkspaceAssetAuthorizationPanel</div> }));

const configuration = { production_plan: null, selected_delivery_target_version_id: null, delivery_targets: [], profile_bindings: [], impact: { remote_transport_allowed: false } };

function renderSection(section: string) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter initialEntries={[`/projects/project-1/settings/${section}`]}><Routes><Route path="/projects/:projectId/settings/:section" element={<ProductionSettingsPage />} /></Routes></MemoryRouter></QueryClientProvider>);
}

describe("ProductionSettingsPage routed sections", () => {
  beforeEach(() => { vi.clearAllMocks(); vi.mocked(listProjects).mockResolvedValue({ items: [{ id: "project-1", code: "P1", title: "Project One", status: "ACTIVE" }] } as never); vi.mocked(getProjectConfiguration).mockResolvedValue({ configuration } as never); });
  afterEach(() => cleanup());

  it("loads each owner by URL without nested settings tabs", async () => {
    renderSection("production");
    expect(await screen.findByText("ProductionSettingsOverview project-1")).toBeTruthy();
    expect(screen.queryByRole("tablist")).toBeNull();
    cleanup();

    renderSection("delivery");
    expect(await screen.findByText("Delivery target authority project-1")).toBeTruthy();
    expect(screen.getByText("BrandKitPanel")).toBeTruthy();
    cleanup();

    renderSection("automation");
    expect(await screen.findByText("AutomationWorkflowPanel")).toBeTruthy();
    fireEvent.click(screen.getByText("专家：本机脚本接口与回调"));
    expect(await screen.findByText("AutomationPanel")).toBeTruthy();
    cleanup();

    renderSection("rights");
    expect(await screen.findByText("WorkspaceAssetAuthorizationPanel")).toBeTruthy();
    expect(screen.getByText("ProjectAssetGrantPanel")).toBeTruthy();
    cleanup();

    renderSection("data");
    expect(await screen.findByText("MediaDerivativeMaintenancePanel")).toBeTruthy();
    expect(screen.getByText("ProjectPackageAction")).toBeTruthy();
    expect(await screen.findByText("ProjectTemplateCopyAction")).toBeTruthy();
  });
});
