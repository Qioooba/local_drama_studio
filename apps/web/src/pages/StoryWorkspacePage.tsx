import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { EntityRail, ThreePaneLayout, type EntityRailItem } from "../components/ui";
import { AssetProposalReviewPanel } from "../features/episode-plan-v2/AssetProposalReviewPanel";
import { AIDraftReviewPanel } from "../features/projects/AIDraftReviewPanel";
import { CreativeLibrary } from "../features/projects/CreativeLibrary";
import { getProject } from "../features/projects/projectClient";
import { ScriptImportPanel } from "../features/projects/ScriptImportPanel";
import { queryKeys } from "../query/queryKeys";
import "./story-workspace.css";

export type StoryStage = "import" | "review" | "assets" | "bible";

const STAGES: Array<{ id: StoryStage; marker: string; title: string; desc: string; anchor: string }> = [
  { id: "import", marker: "1", title: "导入原稿", desc: "选择小说、剧本或正文范围", anchor: "#story-import" },
  { id: "review", marker: "2", title: "审核拆解", desc: "校对场次、镜头和对白", anchor: "#story-review" },
  { id: "assets", marker: "3", title: "角色建档", desc: "处理 AI 提取的身份建议", anchor: "#story-assets" },
  { id: "bible", marker: "资料", title: "故事圣经", desc: "持续维护世界观与创作设定", anchor: "#story-bible" },
];

function stageFromHash(hash: string): StoryStage {
  return STAGES.find((stage) => stage.anchor === hash)?.id ?? "import";
}

/** Creator-first story flow: one navigation rail, one content stage, no passive inspector. */
export function StoryWorkspacePage() {
  const { projectId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const activeStage = stageFromHash(location.hash);
  const [navOpen, setNavOpen] = useState(() => typeof window === "undefined" || window.innerWidth > 960);
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const project = useQuery({
    queryKey: queryKeys.projects.detail(projectId ?? ""),
    queryFn: () => getProject(projectId!),
    enabled: Boolean(projectId),
  });

  const absoluteRootPath = project.data?.project.absolute_root_path;
  useEffect(() => setCopyState("idle"), [absoluteRootPath]);

  if (!projectId) return <p className="inline-error" role="alert">缺少项目上下文。</p>;

  const copyProjectPath = async () => {
    if (!absoluteRootPath) return;
    try {
      await navigator.clipboard.writeText(absoluteRootPath);
      setCopyState("copied");
    } catch {
      setCopyState("failed");
    }
  };

  const selectStage = (stage: StoryStage) => {
    const target = STAGES.find((item) => item.id === stage) ?? STAGES[0];
    navigate({ pathname: location.pathname, search: location.search, hash: target.anchor });
    if (typeof window !== "undefined" && window.innerWidth <= 960) setNavOpen(false);
  };

  const activeDefinition = STAGES.find((stage) => stage.id === activeStage) ?? STAGES[0];
  const railItems: EntityRailItem[] = STAGES.map((stage) => ({
    id: stage.id,
    title: stage.marker === "资料" ? stage.title : `${stage.marker}. ${stage.title}`,
    subtitle: stage.desc,
    badge: stage.marker === "资料" ? <span className="status-pill neutral">随时维护</span> : undefined,
  }));

  return <div className="v2-page story-workspace">
    <header className="story-workspace-header">
      <div><p className="eyebrow">故事工作区</p><h2>从原稿到可生产的故事事实</h2><p className="muted">系统负责解析、编号和上下文关联；你只审核内容与决定哪些事实进入生产。</p></div>
      <div className="story-header-actions"><button type="button" className="secondary story-flow-toggle" onClick={() => setNavOpen((value) => !value)} aria-expanded={navOpen}>选择阶段</button><Link className="secondary v2-inline-link" to={`/projects/${projectId}`}>返回项目总览</Link></div>
    </header>

    <section className="story-project-location" aria-labelledby="story-project-location-label">
      <div className="story-project-location__value">
        <span id="story-project-location-label">服务器项目目录</span>
        {absoluteRootPath
          ? <code title={absoluteRootPath}>{absoluteRootPath}</code>
          : project.isError
            ? <span className="inline-error" role="alert">项目目录读取失败。</span>
            : <span className="muted" role="status">正在读取绝对路径…</span>}
      </div>
      {project.isError
        ? <button type="button" className="secondary" onClick={() => void project.refetch()}>重新读取</button>
        : <button type="button" className="secondary" disabled={!absoluteRootPath} onClick={() => void copyProjectPath()}>
            {copyState === "copied" ? "已复制" : copyState === "failed" ? "复制失败，请手动选择" : "复制路径"}
          </button>}
    </section>

    <div className="story-current-stage" role="status"><span>{activeDefinition.marker}</span><div><small>当前工作</small><strong>{activeDefinition.title}</strong><p>{activeDefinition.desc}</p></div></div>

    <ThreePaneLayout
      navRail={<EntityRail title="故事工作流" items={railItems} selectedId={activeStage} onSelect={(id) => selectStage(id as StoryStage)} />}
      mainStage={<div className="story-main-stage">
        {activeStage === "import" && <section id="story-import" className="story-stage-section" aria-labelledby="story-import-heading">
          <div className="story-stage-heading"><span>1</span><div><h3 id="story-import-heading">导入小说、剧本或长文</h3><p>先由服务端解析章节和段落，确认正文范围入库后，再选择分集生成待审核草稿。</p></div></div>
          <ScriptImportPanel projectId={projectId} onDraftReady={() => selectStage("review")} />
          <footer className="story-stage-next"><span>已有可审阅草稿？</span><button type="button" className="secondary" onClick={() => selectStage("review")}>查看拆解草稿</button></footer>
        </section>}

        {activeStage === "review" && <section id="story-review" className="story-stage-section" aria-labelledby="story-review-heading">
          <div className="story-stage-heading"><span>2</span><div><h3 id="story-review-heading">校对 AI 拆解</h3><p>修改草稿不会覆盖模型原稿；确认后再把选定场次应用到真实分集。</p></div></div>
          <AIDraftReviewPanel projectId={projectId} />
          <footer className="story-stage-next"><span>拆解应用后，继续处理新识别的角色。</span><button type="button" className="secondary" onClick={() => selectStage("assets")}>处理角色建议</button></footer>
        </section>}

        {activeStage === "assets" && <section id="story-assets" className="story-stage-section" aria-labelledby="story-assets-heading">
          <div className="story-stage-heading"><span>3</span><div><h3 id="story-assets-heading">确认角色身份建议</h3><p>合并、独立建档和拒绝仍由你决定；系统自动生成代码并报告批量处理结果。</p></div></div>
          <AssetProposalReviewPanel projectId={projectId} />
          <footer className="story-stage-next"><span>角色建档完成后，在资产圣经补齐形象参考。</span><Link className="secondary v2-inline-link" to={`/projects/${projectId}/assets`}>进入资产圣经</Link></footer>
        </section>}

        {activeStage === "bible" && <section id="story-bible" className="story-stage-section" aria-labelledby="story-bible-heading">
          <div className="story-stage-heading"><span>资料</span><div><h3 id="story-bible-heading">故事圣经与创作资料</h3><p>这是贯穿全程的资料库，不是开始创作前必须填完的门槛。</p></div></div>
          <CreativeLibrary projectId={projectId} />
          <footer className="story-stage-next"><span>资料可以随时回来完善。</span><button type="button" className="secondary" onClick={() => selectStage("import")}>开始导入原稿</button></footer>
        </section>}
      </div>}
      inspector={null}
      navOpen={navOpen}
      inspectorOpen={false}
      onToggleNav={() => setNavOpen((value) => !value)}
      navTitle="故事工作流"
    />
  </div>;
}
