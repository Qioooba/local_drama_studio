import { useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { AIDraftReviewPanel } from "../features/projects/AIDraftReviewPanel";
import { CreativeLibrary } from "../features/projects/CreativeLibrary";
import { ScriptImportPanel } from "../features/projects/ScriptImportPanel";
import { AssetProposalReviewPanel } from "../features/episode-plan-v2/AssetProposalReviewPanel";
import { ThreePaneLayout, Inspector, EntityRail, type EntityRailItem } from "../components/ui";
import "./story-workspace.css";

export type StoryStage = "bible" | "import" | "review" | "assets";

const STAGES: Array<{ id: StoryStage; num: string; title: string; desc: string; anchor: string }> = [
  { id: "bible", num: "1", title: "故事圣经", desc: "结构化创作资料库与设定", anchor: "#story-bible" },
  { id: "import", num: "2", title: "导入长文", desc: "剧本长文解析与提交", anchor: "#story-import" },
  { id: "review", num: "3", title: "审核拆解", desc: "AI 分镜预览与目标分集应用", anchor: "#story-review" },
  { id: "assets", num: "4", title: "裁决身份", desc: "AI 提取角色身份建议与建档", anchor: "#story-assets" },
];

function stageFromHash(hash: string): StoryStage {
  return STAGES.find((stage) => stage.anchor === hash)?.id ?? "bible";
}

/** Project-level source-of-truth workflow: source → preview → explicit apply. */
export function StoryWorkspacePage() {
  const { projectId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const activeStage = stageFromHash(location.hash);
  const [navOpen, setNavOpen] = useState(() => typeof window === "undefined" || window.innerWidth > 960);
  const [inspectorOpen, setInspectorOpen] = useState(() => typeof window === "undefined" || window.innerWidth > 1280);

  if (!projectId) return <p className="inline-error" role="alert">缺少项目上下文。</p>;

  const selectStage = (stage: StoryStage) => {
    const target = STAGES.find((item) => item.id === stage) ?? STAGES[0];
    navigate(
      { pathname: location.pathname, search: location.search, hash: target.anchor },
      { replace: true },
    );
    if (typeof window !== "undefined" && window.innerWidth <= 960) setNavOpen(false);
  };

  const stageRailItems: EntityRailItem[] = STAGES.map((s) => ({
    id: s.id,
    title: `${s.num}. ${s.title}`,
    subtitle: s.desc,
  }));

  return (
    <div className="v2-page story-workspace">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">项目级故事工作区</p>
          <h2>从长文原稿到可审核的生产事实</h2>
        </div>
        <div className="story-header-actions">
          <button
            type="button"
            className="secondary btn-sm"
            onClick={() => setInspectorOpen((prev) => !prev)}
            aria-label={inspectorOpen ? "收起信息栏" : "展开信息栏"}
          >
            {inspectorOpen ? "收起详情" : "展开详情"}
          </button>
          <Link className="secondary v2-inline-link" to={`/projects/${projectId}`}>
            返回项目总览
          </Link>
        </div>
      </div>
      <p className="muted">
        源文档先生成解析预览；AI 拆解只保存草稿。只有明确选择目标分集并点击应用，才会创建场次、镜头与对白。
      </p>

      {/* Stage Navigation */}
      <nav className="story-stage-nav" aria-label="故事工作流阶段">
        {STAGES.map((s) => (
          <button
            key={s.id}
            type="button"
            className={activeStage === s.id ? "active-stage" : ""}
            onClick={() => selectStage(s.id)}
            aria-current={activeStage === s.id ? "step" : undefined}
          >
            <span>{s.num}</span>
            <strong>{s.title}</strong>
            <small>{s.desc}</small>
          </button>
        ))}
      </nav>

      {/* Workspace Three-Pane Layout */}
      <ThreePaneLayout
        navRail={
          <EntityRail
            title="工作流步骤"
            items={stageRailItems}
            selectedId={activeStage}
            onSelect={(id) => selectStage(id as StoryStage)}
          />
        }
        mainStage={
          <div className="story-main-stage">
            {activeStage === "bible" ? <section
              id="story-bible"
              className="story-stage-section"
              aria-labelledby="story-bible-heading"
            >
              <div className="story-stage-heading">
                <span>1</span>
                <div>
                  <h3 id="story-bible-heading">故事圣经与创作资料</h3>
                  <p>结构化资料的每次修改都会派生 revision，可比较、可回退。</p>
                </div>
              </div>
              <CreativeLibrary projectId={projectId} />
            </section> : null}

            {activeStage === "import" ? <section
              id="story-import"
              className="story-stage-section"
              aria-labelledby="story-import-heading"
            >
              <div className="story-stage-heading">
                <span>2</span>
                <div>
                  <h3 id="story-import-heading">导入小说、剧本或长文</h3>
                  <p>先读取并展示段落与字符统计，再显式 commit；不修改原文件。</p>
                </div>
              </div>
              <ScriptImportPanel projectId={projectId} />
            </section> : null}

            {activeStage === "review" ? <section
              id="story-review"
              className="story-stage-section"
              aria-labelledby="story-review-heading"
            >
              <div className="story-stage-heading">
                <span>3</span>
                <div>
                  <h3 id="story-review-heading">预览 AI 拆解并人工应用</h3>
                  <p>草稿不会自动落地。确认内容后选择真实目标分集，再执行应用。</p>
                </div>
              </div>
              <AIDraftReviewPanel projectId={projectId} />
            </section> : null}

            {activeStage === "assets" ? <section
              id="story-assets"
              className="story-stage-section"
              aria-labelledby="story-assets-heading"
            >
              <div className="story-stage-heading">
                <span>4</span>
                <div>
                  <h3 id="story-assets-heading">处理提取出的角色身份建议</h3>
                  <p>合并、独立建档和拒绝都需要人工动作，不做破坏性自动合并。</p>
                </div>
              </div>
              <AssetProposalReviewPanel projectId={projectId} />
            </section> : null}
          </div>
        }
        inspector={
          inspectorOpen ? (
            <Inspector title="故事工作区证据" onClose={() => setInspectorOpen(false)}>
              <div className="story-inspector-content">
                <div>
                  <strong className="story-inspector-label">当前阶段</strong>
                  <div className="story-inspector-value">
                    {STAGES.find((s) => s.id === activeStage)?.title}
                  </div>
                </div>
                <div className="story-inspector-section">
                  <strong className="story-inspector-label">工作流保障</strong>
                  <ul className="story-inspector-list">
                    <li>不可变源文档存储</li>
                    <li>AI 拆解草稿隔离</li>
                    <li>人工指定目标分集应用</li>
                    <li>角色建议显式合并/建档</li>
                  </ul>
                </div>
                <div className="story-inspector-section story-inspector-links">
                  <strong className="story-inspector-label">快速跳转</strong>
                  <Link to={`/projects/${projectId}/assets`}>
                    打开资产圣经 →
                  </Link>
                  <Link to={`/projects/${projectId}/production-settings`}>
                    管理生产设置 →
                  </Link>
                </div>
              </div>
            </Inspector>
          ) : null
        }
        navOpen={navOpen}
        inspectorOpen={inspectorOpen}
        onToggleNav={() => setNavOpen((previous) => !previous)}
        onToggleInspector={() => setInspectorOpen((previous) => !previous)}
        navTitle="故事工作流步骤"
        inspectorTitle="故事工作区证据"
      />
    </div>
  );
}
