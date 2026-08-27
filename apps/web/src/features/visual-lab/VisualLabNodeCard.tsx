import { Handle, NodeResizer, Position, type NodeProps } from "@xyflow/react";
import type { VisualLabNode } from "./client";

const kindLabels: Record<string, string> = {
  TEXT_REF: "文字参考",
  STORY_ASSET_REF: "故事资产",
  MEDIA_REF: "媒体参考",
  SHOT_REF: "镜头引用",
  GENERATION_INTENT: "生成意图",
  TRANSFORM_INTENT: "转换意图",
  COMPARE_SET: "候选比较",
  SEQUENCE_PREVIEW: "序列预览",
  OUTPUT_DRAFT: "输出草稿",
  NOTE: "便签",
  FRAME: "分组区域",
};

export type VisualLabNodeData = {
  source: VisualLabNode;
  selectedByApp: boolean;
  onResize: (nodeId: string, width: number, height: number) => void;
};

export function VisualLabNodeCard({ id, data, selected }: NodeProps) {
  const { source, selectedByApp, onResize } = data as unknown as VisualLabNodeData;
  const inputs = Object.entries(source.content.ports?.inputs ?? {});
  const outputs = Object.entries(source.content.ports?.outputs ?? {});
  const isFrame = source.node_kind === "FRAME";

  return (
    <article
      className={`visual-node-card kind-${source.node_kind.toLowerCase()}${selectedByApp || selected ? " is-selected" : ""}${source.collapsed ? " is-collapsed" : ""}`}
      aria-label={`${kindLabels[source.node_kind] ?? source.node_kind}：${source.content.title ?? "未命名"}`}
    >
      <NodeResizer
        isVisible={Boolean(selected)}
        minWidth={isFrame ? 320 : 180}
        minHeight={isFrame ? 220 : 100}
        maxWidth={2400}
        maxHeight={1800}
        color="#2d7467"
        onResizeEnd={(_, parameters) => onResize(id, parameters.width, parameters.height)}
      />
      <header>
        <span>{kindLabels[source.node_kind] ?? source.node_kind}</span>
        <small>{isFrame ? `${(source.content.member_ids as string[] | undefined)?.length ?? 0} 项` : `r${source.content_revision_no}`}</small>
      </header>
      <h3>{source.content.title || kindLabels[source.node_kind]}</h3>
      {!isFrame && !source.collapsed && source.content.body && <p>{String(source.content.body)}</p>}
      {!isFrame && !source.collapsed && source.content.reference_id && <code>{String(source.content.reference_id).slice(0, 16)}</code>}
      {!isFrame && !source.collapsed && (
        <>
          <div className="visual-node-ports visual-node-ports--inputs">
            {inputs.map(([name, type], index) => (
              <span key={name} style={{ top: 48 + index * 25 }}>
                <Handle type="target" position={Position.Left} id={name} />
                <b>{name}</b><em>{type}</em>
              </span>
            ))}
          </div>
          <div className="visual-node-ports visual-node-ports--outputs">
            {outputs.map(([name, type], index) => (
              <span key={name} style={{ top: 48 + index * 25 }}>
                <b>{name}</b><em>{type}</em>
                <Handle type="source" position={Position.Right} id={name} />
              </span>
            ))}
          </div>
        </>
      )}
    </article>
  );
}
