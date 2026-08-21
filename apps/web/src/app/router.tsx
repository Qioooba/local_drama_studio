import { Suspense, lazy, type ReactNode } from "react";
import { Navigate, createBrowserRouter } from "react-router-dom";
import { LegacyRouteBoundary } from "./legacyRoute";
import { AppShell } from "../layouts/AppShell";
import { FeatureFlagRoute } from "./featureFlags";
import { RouteErrorBoundary } from "../components/ui/ErrorBoundary";

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
const EpisodeRunPage = lazy(() => import("../pages/EpisodeRunPage").then((module) => ({ default: module.EpisodeRunPage })));
const ProjectsPage = lazy(() => import("../pages/ProjectsPage").then((module) => ({ default: module.ProjectsPage })));
const QcPoliciesPage = lazy(() => import("../pages/QcPoliciesPage").then((module) => ({ default: module.QcPoliciesPage })));
const DirectorRecipesPage = lazy(() => import("../pages/DirectorRecipesPage").then((module) => ({ default: module.DirectorRecipesPage })));
const ProductionSettingsPage = lazy(() => import("../pages/ProductionSettingsPage").then((module) => ({ default: module.ProductionSettingsPage })));
const StoryWorkspacePage = lazy(() => import("../pages/StoryWorkspacePage").then((module) => ({ default: module.StoryWorkspacePage })));
const MediaLabPage = lazy(() => import("../pages/MediaLabPage").then((module) => ({ default: module.MediaLabPage })));
const CanvasPage = lazy(() => import("../pages/CanvasPage").then((module) => ({ default: module.CanvasPage })));
const ProjectOperationsPage = lazy(() => import("../pages/ProjectOperationsPage").then((module) => ({ default: module.ProjectOperationsPage })));
const GenerationPage = lazy(() => import("../pages/GenerationPage").then((module) => ({ default: module.GenerationPage })));

const page = (content: ReactNode) => <Suspense fallback={<main className="route-loading" role="status">正在载入工作区…</main>}>{content}</Suspense>;

/**
 * V2 route contract (see docs/xinjihua/02_架构与前后端重构规格.md §5).
 *
 * Root and legacy query bookmarks resolve into V2 routes or an explicit V2
 * compatibility chooser. The legacy App bundle is never imported here.
 */
export const router = createBrowserRouter([
  {
    path: "/",
    element: <LegacyRouteBoundary />,
    errorElement: <RouteErrorBoundary />,
  },
  {
    path: "/projects",
    element: <AppShell />,
    errorElement: <RouteErrorBoundary />,
    children: [{ index: true, element: page(<ProjectsPage />) }],
  },
  {
    path: "/projects/:projectId",
    element: <AppShell />,
    errorElement: <RouteErrorBoundary />,
    children: [
      { index: true, element: page(<ProjectHomePage />) },
      { path: "story", element: page(<StoryWorkspacePage />) },
      { path: "assets", element: page(<FeatureFlagRoute flag="ASSET_BIBLE_V2" fallbackView="projects"><AssetBiblePage /></FeatureFlagRoute>) },
      { path: "qc-policies", element: page(<QcPoliciesPage />) },
      { path: "director-recipes", element: page(<DirectorRecipesPage />) },
      { path: "production-settings", element: page(<ProductionSettingsPage />) },
      { path: "settings", element: <Navigate to="production-settings" replace /> },
      { path: "models", element: page(<ModelsPage />) },
      { path: "jobs", element: page(<JobsPage />) },
      { path: "diagnostics", element: page(<DiagnosticsPage />) },
      { path: "lab", element: page(<MediaLabPage />) },
      { path: "canvas", element: page(<CanvasPage />) },
      { path: "operations", element: page(<ProjectOperationsPage />) },
      { path: "episodes/:episodeId/plan", element: page(<EpisodePlanPage />) },
      { path: "episodes/:episodeId/direct", element: page(<FeatureFlagRoute flag="DIRECTOR_DESK_V2" fallbackView="generation"><DirectorDeskPage /></FeatureFlagRoute>) },
      { path: "episodes/:episodeId/direct/:shotId", element: page(<FeatureFlagRoute flag="DIRECTOR_DESK_V2" fallbackView="generation"><DirectorDeskPage /></FeatureFlagRoute>) },
      { path: "episodes/:episodeId/generation", element: page(<GenerationPage />) },
      { path: "episodes/:episodeId/generation/:shotId", element: page(<GenerationPage />) },
      { path: "episodes/:episodeId/review", element: page(<EpisodeReviewPage />) },
      { path: "episodes/:episodeId/audio", element: page(<AudioPage />) },
      { path: "episodes/:episodeId/timeline", element: page(<TimelinePage />) },
      { path: "episodes/:episodeId/delivery", element: page(<DeliveryPage />) },
      { path: "episodes/:episodeId/run", element: page(<FeatureFlagRoute flag="EPISODE_AGENT_RUN_V2" fallbackView="projects"><EpisodeRunPage /></FeatureFlagRoute>) },
    ],
  },
  {
    element: <AppShell />,
    errorElement: <RouteErrorBoundary />,
    children: [
      { path: "/models", element: page(<ModelsPage />) },
      { path: "/jobs", element: page(<JobsPage />) },
      { path: "/diagnostics", element: page(<DiagnosticsPage />) },
    ],
  },
  { path: "*", element: <Navigate to="/" replace /> },
]);
