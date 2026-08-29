import type { RefObject } from "react";
import { Link } from "react-router-dom";
import { routes } from "../app/routeRegistry";
import { StudioIcon } from "../components/icons";
import { CommandPalette } from "../features/commands/CommandPalette";
import type { CommandContext, StudioCommand } from "../features/commands/commandRegistry";
import { LocalRuntimeIndicator } from "../features/status-v2/LocalRuntimeIndicator";
import "./shell-toolbar.css";

type ProjectOption = { id: string; title: string };
type EpisodeOption = { id: string; title: string };
type SeasonOption = { id: string; title: string; episodes: EpisodeOption[] };

type ShellToolbarProps = {
  baseCommands: StudioCommand[];
  commandContext: CommandContext;
  episodeCatalogPending: boolean;
  episodeId?: string;
  mobileNavOpen: boolean;
  mobileNavTriggerRef: RefObject<HTMLButtonElement | null>;
  onEpisodeChange: (episodeId: string) => void;
  onProjectChange: (projectId: string) => void;
  onToggleMobileNav: () => void;
  projectId?: string;
  projects: ProjectOption[];
  seasons: SeasonOption[];
};

/**
 * The top bar has one alignment owner. Search, production context and machine
 * actions stay as three semantic groups instead of styling unrelated controls
 * until they happen to look like a row.
 */
export function ShellToolbar({
  baseCommands,
  commandContext,
  episodeCatalogPending,
  episodeId,
  mobileNavOpen,
  mobileNavTriggerRef,
  onEpisodeChange,
  onProjectChange,
  onToggleMobileNav,
  projectId,
  projects,
  seasons,
}: ShellToolbarProps) {
  return <div className="shell-toolbar" role="group" aria-label="全局工具">
    <button
      ref={mobileNavTriggerRef}
      type="button"
      className="mobile-nav-toggle"
      aria-label="打开主导航"
      aria-expanded={mobileNavOpen}
      onClick={onToggleMobileNav}
    >
      <StudioIcon name="menu" />
      <span>菜单</span>
    </button>

    <div className="shell-toolbar__search">
      <CommandPalette context={commandContext} baseCommands={baseCommands} />
    </div>

    <div className="shell-context-switcher" role="group" aria-label="制作上下文">
      <label className="shell-context-field">
        <span>项目</span>
        <select
          aria-label="当前项目"
          value={projectId ?? ""}
          onChange={(event) => onProjectChange(event.target.value)}
        >
          <option value="">全部项目</option>
          {projects.map((project) => <option key={project.id} value={project.id}>{project.title}</option>)}
        </select>
      </label>
      {projectId && <label className="shell-context-field shell-context-field--episode">
        <span>分集</span>
        <select
          aria-label="当前分集"
          value={episodeId ?? ""}
          disabled={episodeCatalogPending || seasons.length === 0}
          onChange={(event) => onEpisodeChange(event.target.value)}
        >
          {!episodeId && <option value="">选择分集</option>}
          {seasons.map((season) => <optgroup key={season.id} label={season.title}>
            {season.episodes.map((episode) => <option key={episode.id} value={episode.id}>{episode.title}</option>)}
          </optgroup>)}
        </select>
      </label>}
    </div>

    <div className="shell-system-tools" role="group" aria-label="系统状态与任务">
      <Link className="shell-system-action" to={routes.systemJobs(projectId)} aria-label="打开任务中心">
        <StudioIcon name="activity" />
        <span>任务中心</span>
      </Link>
      <LocalRuntimeIndicator />
    </div>
  </div>;
}
