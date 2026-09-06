import { Suspense, lazy, type ReactNode } from "react";
import { Navigate, createBrowserRouter, useLocation, useParams } from "react-router-dom";
import { LegacyRouteBoundary } from "./legacyRoute";
import { AppShell } from "../layouts/AppShell";
import { FeatureFlagRoute } from "./featureFlags";
import { RouteErrorBoundary } from "../components/ui/ErrorBoundary";
import { routes } from "./routeRegistry";
import { PostShell, ProjectSettingsShell, SystemShell } from "../pages/WorkspaceShells";

const ProjectHomePage = lazy(() => import("../pages/ProjectHomePage").then((module) => ({ default: module.ProjectHomePage })));
const AssetBiblePage = lazy(() => import("../pages/AssetBiblePage").then((module) => ({ default: module.AssetBiblePage })));
const EpisodePlanPage = lazy(() => import("../pages/EpisodePlanPage").then((module) => ({ default: module.EpisodePlanPage })));
const DirectorDeskPage = lazy(() => import("../pages/DirectorDeskPage").then((module) => ({ default: module.DirectorDeskPage })));
const EpisodeReviewPage = lazy(() => import("../pages/EpisodeReviewPage").then((module) => ({ default: module.EpisodeReviewPage })));
const AudioPage = lazy(() => import("../pages/AudioPage").then((module) => ({ default: module.AudioPage })));
const TimelinePage = lazy(() => import("../pages/TimelinePage").then((module) => ({ default: module.TimelinePage })));
const DeliveryPage = lazy(() => import("../pages/DeliveryPage").then((module) => ({ default: module.DeliveryPage })));
const ModelsPage = lazy(() => import("../pages/ModelsPage").then((module) => ({ default: module.ModelsPage })));
const JobsPage = lazy(() => import("../pages/JobsPage").then((module) => ({ default: module.JobsPage })));
const DiagnosticsPage = lazy(() => import("../pages/DiagnosticsPage").then((module) => ({ default: module.DiagnosticsPage })));
const ProjectsPage = lazy(() => import("../pages/ProjectsPage").then((module) => ({ default: module.ProjectsPage })));
const QuickCreatePage = lazy(() => import("../pages/QuickCreatePage").then((module) => ({ default: module.QuickCreatePage })));
const QcPoliciesPage = lazy(() => import("../pages/QcPoliciesPage").then((module) => ({ default: module.QcPoliciesPage })));
const DirectorRecipesPage = lazy(() => import("../pages/DirectorRecipesPage").then((module) => ({ default: module.DirectorRecipesPage })));
const ProductionSettingsPage = lazy(() => import("../pages/ProductionSettingsPage").then((module) => ({ default: module.ProductionSettingsPage })));
const ProjectCapabilitiesPage = lazy(() => import("../pages/ProjectCapabilitiesPage").then((module) => ({ default: module.ProjectCapabilitiesPage })));
const StoryWorkspacePage = lazy(() => import("../pages/StoryWorkspacePage").then((module) => ({ default: module.StoryWorkspacePage })));
const AdaptationPlanningPage = lazy(() => import("../features/story-adaptation/AdaptationPlanningPage").then((module) => ({ default: module.AdaptationPlanningPage })));
const AdaptationPlanWorkspacePage = lazy(() => import("../features/story-adaptation/AdaptationPlanWorkspacePage").then((module) => ({ default: module.AdaptationPlanWorkspacePage })));
const SystemWorkflowsPage = lazy(() => import("../pages/SystemWorkflowsPage").then((module) => ({ default: module.SystemWorkflowsPage })));
const VisualLabListPage = lazy(() => import("../features/visual-lab/VisualLabListPage").then((module) => ({ default: module.VisualLabListPage })));
const VisualLabWorkspacePage = lazy(() => import("../features/visual-lab/VisualLabWorkspacePage").then((module) => ({ default: module.VisualLabWorkspacePage })));
const HomePage = lazy(() => import("../pages/HomePage").then((module) => ({ default: module.HomePage })));

const page = (content: ReactNode) => <Suspense fallback={<main className="route-loading" role="status">正在载入工作区…</main>}>{content}</Suspense>;

function LegacyProjectRedirect({ target }: { target: "settings" | "capabilities" | "quality" | "directing" | "labs" | "jobs" | "diagnostics" | "workflows" }) {
  const { projectId = "" } = useParams();
  const location = useLocation();
  if (target === "settings") {
    const legacyView = new URLSearchParams(location.search).get("view");
    const section = legacyView === "delivery" ? "delivery" : legacyView === "automation" ? "automation" : legacyView === "assets" ? "rights" : legacyView === "freshness" ? "data" : "production";
    return <Navigate to={routes.settings(projectId, section)} replace />;
  }
  if (target === "capabilities") return <Navigate to={routes.settings(projectId, "capabilities")} replace />;
  if (target === "quality") return <Navigate to={routes.settings(projectId, "quality")} replace />;
  if (target === "directing") return <Navigate to={routes.settings(projectId, "directing")} replace />;
  if (target === "labs") return <Navigate to={routes.visualLabs(projectId)} replace />;
  if (target === "jobs") return <Navigate to={routes.systemJobs(projectId)} replace />;
  if (target === "diagnostics") return <Navigate to={routes.systemDiagnostics(projectId)} replace />;
  return <Navigate to={routes.systemWorkflows(projectId)} replace />;
}

function LegacyEpisodeRedirect({ target }: { target: "studio" | "generate" | "production" | "review" | "audio" | "edit" }) {
  const { projectId = "", episodeId = "", shotId } = useParams();
  const location = useLocation();
  if (target === "studio" || target === "generate") {
    const search = new URLSearchParams(location.search);
    if (target === "generate") search.set("focus", "generate");
    return <Navigate to={{ pathname: routes.shotStudio(projectId, episodeId, shotId), search: search.toString() ? `?${search}` : "", hash: location.hash }} replace />;
  }
  const to = target === "production" ? routes.episodeProduction(projectId, episodeId) : target === "review" ? routes.postReview(projectId, episodeId) : target === "audio" ? routes.postAudio(projectId, episodeId) : routes.postEdit(projectId, episodeId);
  return <Navigate to={{ pathname: to, search: location.search, hash: location.hash }} replace />;
}

function EpisodeProductionRedirect() {
  const { projectId = "", episodeId = "" } = useParams();
  const location = useLocation();
  return <Navigate to={{ pathname: routes.episodePlan(projectId, episodeId), search: location.search, hash: location.hash }} replace />;
}

function LegacyGlobalRedirect({ target }: { target: "capabilities" | "jobs" | "diagnostics" | "workflows" }) {
  const location = useLocation();
  return <Navigate to={{ pathname: `/system/${target}`, search: location.search, hash: location.hash }} replace />;
}

function RootRouteBoundary() {
  const location = useLocation();
  const params = new URLSearchParams(location.search);
  const hasLegacyContext = ["view", "project", "episode", "shot", "review", "legacy"].some((key) => params.has(key));
  return hasLegacyContext ? <LegacyRouteBoundary /> : <AppShell />;
}

export const router = createBrowserRouter([
  { path: "/", element: <RootRouteBoundary />, errorElement: <RouteErrorBoundary />, children: [{ index: true, element: page(<HomePage />) }] },
  { path: "/projects", element: <AppShell />, errorElement: <RouteErrorBoundary />, children: [{ index: true, element: page(<ProjectsPage />) }] },
  { path: "/quick-create", element: <AppShell />, errorElement: <RouteErrorBoundary />, children: [{ index: true, element: page(<QuickCreatePage />) }] },
  {
    path: "/projects/:projectId", element: <AppShell />, errorElement: <RouteErrorBoundary />, children: [
      { index: true, element: page(<ProjectHomePage />) },
      { path: "story", element: page(<StoryWorkspacePage />) },
      { path: "story/plans", element: page(<AdaptationPlanningPage />) },
      { path: "story/plans/:planId", element: page(<AdaptationPlanWorkspacePage />) },
      { path: "assets", element: page(<FeatureFlagRoute flag="ASSET_BIBLE_V2" fallbackView="projects"><AssetBiblePage /></FeatureFlagRoute>) },
      { path: "settings", element: <ProjectSettingsShell />, children: [
        { index: true, element: <Navigate to="production" replace /> },
        { path: "production", element: page(<ProductionSettingsPage />) },
        { path: "capabilities", element: page(<ProjectCapabilitiesPage />) },
        { path: "directing", element: page(<DirectorRecipesPage />) },
        { path: "quality", element: page(<QcPoliciesPage />) },
        { path: "delivery", element: page(<ProductionSettingsPage />) },
        { path: "automation", element: page(<ProductionSettingsPage />) },
        { path: "rights", element: page(<ProductionSettingsPage />) },
        { path: "data", element: page(<ProductionSettingsPage />) },
      ] },
      { path: "labs", element: page(<VisualLabListPage />) },
      { path: "labs/:labId", element: page(<VisualLabWorkspacePage />) },
      { path: "episodes/:episodeId/plan", element: page(<EpisodePlanPage />) },
      { path: "episodes/:episodeId/studio", element: page(<FeatureFlagRoute flag="DIRECTOR_DESK_V2" fallbackView="projects"><DirectorDeskPage /></FeatureFlagRoute>) },
      { path: "episodes/:episodeId/studio/:shotId", element: page(<FeatureFlagRoute flag="DIRECTOR_DESK_V2" fallbackView="projects"><DirectorDeskPage /></FeatureFlagRoute>) },
      { path: "episodes/:episodeId/production", element: <EpisodeProductionRedirect /> },
      { path: "episodes/:episodeId/post", element: <PostShell />, children: [
        { index: true, element: <Navigate to="edit" replace /> },
        { path: "review", element: page(<EpisodeReviewPage />) },
        { path: "audio", element: page(<AudioPage />) },
        { path: "edit", element: page(<TimelinePage />) },
      ] },
      { path: "episodes/:episodeId/delivery", element: page(<DeliveryPage />) },

      { path: "qc-policies", element: <LegacyProjectRedirect target="quality" /> },
      { path: "director-recipes", element: <LegacyProjectRedirect target="directing" /> },
      { path: "production-settings", element: <LegacyProjectRedirect target="settings" /> },
      { path: "operations", element: <LegacyProjectRedirect target="settings" /> },
      { path: "models", element: page(<ModelsPage />) },
      { path: "jobs", element: <LegacyProjectRedirect target="jobs" /> },
      { path: "diagnostics", element: <LegacyProjectRedirect target="diagnostics" /> },
      { path: "lab", element: <LegacyProjectRedirect target="workflows" /> },
      { path: "canvas", element: <LegacyProjectRedirect target="labs" /> },
      { path: "episodes/:episodeId/direct", element: <LegacyEpisodeRedirect target="studio" /> },
      { path: "episodes/:episodeId/direct/:shotId", element: <LegacyEpisodeRedirect target="studio" /> },
      { path: "episodes/:episodeId/generation", element: <LegacyEpisodeRedirect target="generate" /> },
      { path: "episodes/:episodeId/generation/:shotId", element: <LegacyEpisodeRedirect target="generate" /> },
      { path: "episodes/:episodeId/run", element: <LegacyEpisodeRedirect target="production" /> },
      { path: "episodes/:episodeId/review", element: <LegacyEpisodeRedirect target="review" /> },
      { path: "episodes/:episodeId/audio", element: <LegacyEpisodeRedirect target="audio" /> },
      { path: "episodes/:episodeId/timeline", element: <LegacyEpisodeRedirect target="edit" /> },
    ],
  },
  {
    path: "/system", element: <AppShell />, errorElement: <RouteErrorBoundary />, children: [
      { element: <SystemShell />, children: [
        { index: true, element: <Navigate to="capabilities" replace /> },
        { path: "capabilities", element: page(<ModelsPage />) },
        { path: "jobs", element: page(<JobsPage />) },
        { path: "diagnostics", element: page(<DiagnosticsPage />) },
        { path: "workflows", element: page(<SystemWorkflowsPage />) },
      ] },
    ],
  },
  { path: "/models", element: <LegacyGlobalRedirect target="capabilities" /> },
  { path: "/jobs", element: <LegacyGlobalRedirect target="jobs" /> },
  { path: "/diagnostics", element: <LegacyGlobalRedirect target="diagnostics" /> },
  { path: "/lab", element: <LegacyGlobalRedirect target="workflows" /> },
  { path: "*", element: <Navigate to="/" replace /> },
]);
