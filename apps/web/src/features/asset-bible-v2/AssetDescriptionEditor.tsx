import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { updateStoryAsset, type StoryAsset } from "../../generated/api";

export function AssetDescriptionEditor({ asset, onChanged }: {
  asset: Pick<StoryAsset, "id" | "name" | "description" | "revision">;
  onChanged: () => Promise<void>;
}) {
  const [draft, setDraft] = useState<{ description: string; revision: number } | null>(null);
  const [saved, setSaved] = useState(false);
  const mutation = useMutation({
    mutationFn: (value: NonNullable<typeof draft>) => updateStoryAsset(asset.id, {
      expected_revision: value.revision, description: value.description.trim(),
    }),
    onSuccess: async () => { await onChanged(); setDraft(null); setSaved(true); },
  });
  return <section className="story-asset-create" aria-label={`${asset.name} 的文字设定`}>
    {draft ? <form onSubmit={(event) => { event.preventDefault(); mutation.mutate(draft); }}>
      <label>外观与剧情设定<textarea aria-label="外观与剧情设定" rows={6} value={draft.description} disabled={mutation.isPending} onChange={(event) => setDraft({ ...draft, description: event.target.value })} /></label>
      <p className="muted">描述人物的发型、服装、年龄感和剧情身份，或场景、道具的固定特征。保存后用于新生成任务，已提交任务沿用原快照。</p>
      <button type="submit" className="primary-action" disabled={mutation.isPending || !draft.description.trim()}>{mutation.isPending ? "正在保存设定…" : "保存文字设定"}</button>
      <button type="button" disabled={mutation.isPending} onClick={() => { setDraft(null); mutation.reset(); }}>取消编辑</button>
      {mutation.error && <p role="alert" className="inline-error">{mutation.error instanceof Error ? mutation.error.message : String(mutation.error)}</p>}
    </form> : <>
      <button type="button" onClick={() => { setDraft({ description: asset.description, revision: asset.revision }); setSaved(false); mutation.reset(); }}>编辑文字设定</button>
      {saved && <p role="status">文字设定已保存，将用于后续生成。</p>}
    </>}
  </section>;
}
