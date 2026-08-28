import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getProfileVersion, type GenerationModel, type GenerationModelRoute } from "../../generated/api";
import { ProfileExecutionDetailButton } from "../model-config/ProfileExecutionDetailButton";
import { ProfileOverrideFields } from "../model-config/ProfileOverrideFields";
import {
  createQuickGenerationPreset,
  deleteQuickGenerationPreset,
  listQuickGenerationPresets,
  updateQuickGenerationPreset,
  type QuickGenerationParameters,
  type QuickGenerationPreset,
} from "./quickGenerationClient";

type Props = {
  title: string;
  description: string;
  action: GenerationModelRoute["action"];
  models: GenerationModel[];
  profileVersionId: string;
  parameters: QuickGenerationParameters;
  disabled?: boolean;
  onProfileChange: (profileVersionId: string) => void;
  onParametersChange: (parameters: QuickGenerationParameters) => void;
};

export function QuickGenerationModelSettings({
  title,
  description,
  action,
  models,
  profileVersionId,
  parameters,
  disabled = false,
  onProfileChange,
  onParametersChange,
}: Props) {
  const [presets, setPresets] = useState<QuickGenerationPreset[]>([]);
  const [presetId, setPresetId] = useState("");
  const [presetName, setPresetName] = useState("");
  const [showSave, setShowSave] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const profile = useQuery({
    queryKey: ["quick-generation-model-contract", profileVersionId],
    queryFn: () => getProfileVersion(profileVersionId),
    enabled: Boolean(profileVersionId),
    staleTime: 60_000,
  });
  const choices = useMemo(() => models.flatMap((model) => {
    const routes = model.routes.filter((route) => route.action === action);
    if (!routes.length) return [];
    const route = routes.find((item) => item.executable) ?? routes[0];
    return [{ model, route }];
  }), [action, models]);
  const selectedChoice = choices.find(({ route }) => route.profile_version_id === profileVersionId);
  const selectedExecutable = selectedChoice?.route.executable === true;
  const capability = selectedChoice?.route.capability ?? "";
  const selectedPreset = presets.find((item) => item.id === presetId);
  const schema = profile.data?.profile_version.execution?.override_schema;
  const fieldCount = useMemo(() => {
    const fields = schema?.fields;
    return fields && typeof fields === "object" && !Array.isArray(fields) ? Object.keys(fields).length : 0;
  }, [schema]);

  async function reloadPresets() {
    try {
      if (!capability) { setPresets([]); return; }
      const result = await listQuickGenerationPresets(capability);
      setPresets(result.items);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  useEffect(() => { void reloadPresets(); }, [capability]);

  function applyPreset(nextId: string) {
    setPresetId(nextId);
    const preset = presets.find((item) => item.id === nextId);
    if (!preset) return;
    onProfileChange(preset.execution_profile_version_id);
    onParametersChange({ ...preset.parameters });
    setPresetName(preset.name);
    setError("");
  }

  async function savePreset() {
    if (!presetName.trim() || !profileVersionId || !selectedExecutable) return;
    setBusy("save"); setError("");
    try {
      const result = await createQuickGenerationPreset({
        name: presetName.trim(), capability, execution_profile_version_id: profileVersionId, parameters, favorite: true,
      });
      await reloadPresets();
      setPresetId(result.preset.id);
      setPresetName(result.preset.name);
      setShowSave(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally { setBusy(""); }
  }

  async function overwritePreset() {
    if (!selectedPreset) return;
    setBusy("overwrite"); setError("");
    try {
      const result = await updateQuickGenerationPreset(selectedPreset.id, {
        name: selectedPreset.name,
        execution_profile_version_id: profileVersionId,
        parameters,
        favorite: selectedPreset.favorite,
        expected_revision: selectedPreset.revision,
      });
      await reloadPresets();
      setPresetId(result.preset.id);
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(""); }
  }

  async function toggleFavorite() {
    if (!selectedPreset) return;
    setBusy("favorite"); setError("");
    try {
      const result = await updateQuickGenerationPreset(selectedPreset.id, {
        name: selectedPreset.name,
        execution_profile_version_id: selectedPreset.execution_profile_version_id,
        parameters: selectedPreset.parameters,
        favorite: !selectedPreset.favorite,
        expected_revision: selectedPreset.revision,
      });
      await reloadPresets();
      setPresetId(result.preset.id);
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(""); }
  }

  async function removePreset() {
    if (!selectedPreset) return;
    setBusy("delete"); setError("");
    try {
      await deleteQuickGenerationPreset(selectedPreset.id);
      setPresetId(""); setPresetName("");
      await reloadPresets();
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(""); }
  }

  return <section className="quick-model-stage" aria-labelledby={`quick-model-${action}`}>
    <div className="quick-model-stage__heading">
      <div><h4 id={`quick-model-${action}`}>{title}</h4><p>{description}</p></div>
      <span className="status-pill neutral">{selectedChoice && !selectedExecutable ? "路线未就绪" : Object.keys(parameters).length ? `已改 ${Object.keys(parameters).length} 项` : "模型默认"}</span>
    </div>
    <div className="quick-model-stage__selectors">
      <label>模型
        <select value={profileVersionId} disabled={disabled} onChange={(event) => { setPresetId(""); setPresetName(""); onProfileChange(event.target.value); }}>
          <option value="">请选择支持当前动作的模型</option>
          {choices.map(({ model, route }) => <option key={route.profile_version_id} value={route.profile_version_id}>{model.name} · {model.actions.map((item) => item === "TEXT_TO_VIDEO" ? "文生视频" : item === "IMAGE_TO_VIDEO" ? "图生视频" : item === "TEXT_TO_IMAGE" ? "文生图" : "文字规划").join(" / ")}{route.executable ? "" : ` · 路线未就绪（${route.status}）`}</option>)}
        </select>
      </label>
      {profileVersionId ? <ProfileExecutionDetailButton profileVersionId={profileVersionId} /> : null}
      <label>常用参数
        <select aria-label={`${title}常用参数`} value={presetId} disabled={disabled || busy !== "" || !capability} onChange={(event) => applyPreset(event.target.value)}>
          <option value="">不使用预设</option>
          {presets.map((item) => <option key={item.id} value={item.id}>{item.favorite ? "常用 · " : ""}{item.name} · {item.model_title}</option>)}
        </select>
      </label>
    </div>
    {selectedChoice && !selectedExecutable ? <p className="inline-warning" role="status">这个模型支持当前动作，但对应工作流尚不能执行。你仍可查看模型和参数；完成路线维护后即可生成。</p> : null}
    <details className="quick-model-stage__parameters">
      <summary>调整本次参数 <span>{fieldCount ? `${fieldCount} 个可配置项` : "由模型决定"}</span></summary>
      {profile.isPending ? <p className="muted" role="status">正在读取模型参数…</p> : profile.error ? <p className="inline-error" role="alert">参数契约读取失败：{String(profile.error)}</p> : <ProfileOverrideFields schema={schema} value={parameters} scope="RUN" disabled={disabled} onChange={(next) => onParametersChange(next as QuickGenerationParameters)} />}
      <div className="quick-model-stage__preset-actions">
        {selectedPreset ? <button type="button" className="secondary" disabled={Boolean(busy) || disabled} onClick={() => void overwritePreset()}>{busy === "overwrite" ? "覆盖中…" : "覆盖当前预设"}</button> : null}
        <button type="button" className="secondary" disabled={disabled || !selectedExecutable} onClick={() => { setShowSave((value) => !value); setPresetName(""); }}>另存为新的常用参数</button>
        {selectedPreset ? <><button type="button" className="secondary" disabled={Boolean(busy)} onClick={() => void toggleFavorite()}>{selectedPreset.favorite ? "取消置顶" : "设为常用"}</button><button type="button" className="secondary danger" disabled={Boolean(busy)} onClick={() => void removePreset()}>删除预设</button></> : null}
      </div>
      {showSave ? <div className="quick-model-stage__save"><label>新预设名称<input value={presetName} maxLength={120} placeholder="例如：竖屏快速预览" onChange={(event) => setPresetName(event.target.value)} /></label><button type="button" disabled={!presetName.trim() || Boolean(busy) || !selectedExecutable} onClick={() => void savePreset()}>{busy === "save" ? "保存中…" : "添加到常用"}</button></div> : null}
      {error ? <p className="inline-error" role="alert">{error}</p> : null}
    </details>
  </section>;
}
