import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { routes } from "../../app/routeRegistry";
import { createVideoUpscalePreset, getVideoUpscaleOptions, updateProjectVideoUpscaleSettings } from "../../generated/api";

function tileFallbackSizes(tileSize: number) {
  return tileSize === 0 ? [256, 128, 64] : [512, 256, 128, 64].filter((value) => value < tileSize).slice(0, 3);
}

function tileFallbackLabel(tileSize: number) {
  const ladder = tileFallbackSizes(tileSize);
  return ladder.length ? `${tileSize === 0 ? "自动" : tileSize} → ${ladder.join(" → ")}` : "不回退";
}

export function VideoUpscaleDefaultsPanel({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const queryKey = ["video-upscale", projectId, "options"] as const;
  const options = useQuery({ queryKey, queryFn: () => getVideoUpscaleOptions(projectId), enabled: Boolean(projectId) });
  const [presetVersionId, setPresetVersionId] = useState("");
  const [sourcePolicy, setSourcePolicy] = useState<"PREFER_FINAL_DELIVERY" | "APPROVED_COMPOSE">("PREFER_FINAL_DELIVERY");
  const [tileSize, setTileSize] = useState(0);
  const [chunkFrames, setChunkFrames] = useState(240);
  const [crf, setCrf] = useState(18);
  const [customTitle, setCustomTitle] = useState("");
  const [customCode, setCustomCode] = useState("");

  useEffect(() => {
    const data = options.data;
    if (!data) return;
    const settings = data.settings;
    const preset = data.presets.find((item) => item.version_id === settings.preset_version_id) ?? data.presets[0];
    if (!preset) return;
    const pipeline = { ...preset.pipeline_options, ...settings.overrides.pipeline };
    const model = { ...preset.model_options, ...settings.overrides.model };
    setPresetVersionId(preset.version_id);
    setSourcePolicy(String(pipeline.source_policy ?? "PREFER_FINAL_DELIVERY") as "PREFER_FINAL_DELIVERY" | "APPROVED_COMPOSE");
    setTileSize(Number(model.tile_size ?? 0));
    setChunkFrames(Number(pipeline.chunk_frames ?? 240));
    setCrf(Number(pipeline.crf ?? 18));
  }, [options.data]);

  const selectedPreset = useMemo(() => options.data?.presets.find((item) => item.version_id === presetVersionId), [options.data, presetVersionId]);
  const save = useMutation({
    mutationFn: () => {
      if (!options.data) throw new Error("超分配置尚未加载");
      return updateProjectVideoUpscaleSettings(projectId, {
        preset_version_id: presetVersionId,
        pipeline_overrides: { source_policy: sourcePolicy, chunk_frames: chunkFrames, crf },
        model_overrides: { tile_size: tileSize },
        expected_revision: options.data.settings.revision,
      });
    },
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey }); },
  });
  const savePreset = useMutation({
    mutationFn: async () => {
      if (!selectedPreset) throw new Error("请先选择基础预设");
      const response = await createVideoUpscalePreset({
        project_id: projectId,
        code: customCode.trim().toUpperCase(),
        title: customTitle.trim(),
        profile_version_id: selectedPreset.profile_version_id,
        pipeline_options: { ...selectedPreset.pipeline_options, source_policy: sourcePolicy, chunk_frames: chunkFrames, crf },
        model_options: { ...selectedPreset.model_options, tile_size: tileSize, tile_fallback_sizes: tileFallbackSizes(tileSize) },
      });
      return response.preset;
    },
    onSuccess: async (preset) => {
      await queryClient.invalidateQueries({ queryKey });
      setPresetVersionId(preset.version_id);
      setCustomTitle("");
      setCustomCode("");
    },
  });

  const choosePreset = (versionId: string) => {
    const preset = options.data?.presets.find((item) => item.version_id === versionId);
    setPresetVersionId(versionId);
    if (!preset) return;
    setSourcePolicy(String(preset.pipeline_options.source_policy ?? "PREFER_FINAL_DELIVERY") as "PREFER_FINAL_DELIVERY" | "APPROVED_COMPOSE");
    setTileSize(Number(preset.model_options.tile_size ?? 0));
    setChunkFrames(Number(preset.pipeline_options.chunk_frames ?? 240));
    setCrf(Number(preset.pipeline_options.crf ?? 18));
  };

  return <section className="upscale-defaults-panel" aria-labelledby="upscale-defaults-title">
    <div className="panel-heading">
      <div><p className="eyebrow">整剧批处理默认值</p><h4 id="upscale-defaults-title">AI 视频超分</h4></div>
      <span className={`status-pill ${selectedPreset?.available ? "success" : "warning"}`}>{selectedPreset?.available ? "执行 Profile 可用" : "需要配置引擎"}</span>
    </div>
    <p className="muted">保存后成为当前项目的一键默认值；创建批次时仍可临时覆盖。预设按版本冻结，已提交的任务不会被后续修改影响。</p>
    {options.isLoading ? <p className="loading-state" role="status">正在读取超分默认值…</p> : options.error ? <p className="inline-error" role="alert">读取失败：{options.error.message}</p> : <fieldset disabled={save.isPending || savePreset.isPending}>
      <legend className="sr-only">项目视频超分默认设置</legend>
      <div className="upscale-defaults-grid">
        <label>一键预设<select value={presetVersionId} onChange={(event) => choosePreset(event.target.value)}>{options.data?.presets.map((preset) => <option value={preset.version_id} key={preset.version_id}>{preset.title} · v{preset.version_no}</option>)}</select></label>
        <label>来源优先级<select value={sourcePolicy} onChange={(event) => setSourcePolicy(event.target.value as typeof sourcePolicy)}><option value="PREFER_FINAL_DELIVERY">优先已人工批准交付文件</option><option value="APPROVED_COMPOSE">只用已批准合成成片</option></select></label>
        <label>Tile<select aria-label="Tile" value={tileSize} onChange={(event) => setTileSize(Number(event.target.value))}><option value={0}>自动</option><option value={64}>64</option><option value={128}>128</option><option value={256}>256</option><option value={512}>512</option><option value={1024}>1024</option></select><small>仅首块明确显存不足时按冻结序列回退：{tileFallbackLabel(tileSize)}</small></label>
        <label>分块帧数<input type="number" min={48} max={480} value={chunkFrames} onChange={(event) => setChunkFrames(Number(event.target.value))} /></label>
        <label>H.264 CRF<input type="number" min={14} max={28} value={crf} onChange={(event) => setCrf(Number(event.target.value))} /></label>
      </div>
      <div className="upscale-defaults-actions"><Link className="secondary" to={routes.systemCapabilities(projectId)}>配置引擎与模型</Link><button className="primary-action" type="button" disabled={!presetVersionId || save.isPending} onClick={() => save.mutate()}>{save.isPending ? "正在保存…" : "保存为项目默认"}</button></div>
      {save.error && <p className="inline-error" role="alert">保存失败：{save.error.message}</p>}
      {save.isSuccess && <p className="status-note success" role="status">项目超分默认值已保存。</p>}
      <details className="upscale-preset-save">
        <summary>将当前参数另存为项目预设</summary>
        <p className="muted">内置预设不会被覆盖；系统会创建一个带版本号的项目专用预设。</p>
        <div className="upscale-defaults-grid"><label>预设名称<input value={customTitle} maxLength={200} onChange={(event) => setCustomTitle(event.target.value)} placeholder="例如：本剧线稿保守版" /></label><label>预设代码<input value={customCode} maxLength={80} pattern="[A-Z][A-Z0-9_]+" onChange={(event) => setCustomCode(event.target.value.toUpperCase())} placeholder="例如：DRAMA_LINE_SAFE" /></label></div>
        <button type="button" className="secondary" disabled={savePreset.isPending || customTitle.trim().length < 1 || !/^[A-Z][A-Z0-9_]{1,79}$/.test(customCode.trim())} onClick={() => savePreset.mutate()}>{savePreset.isPending ? "正在创建…" : "另存为新预设"}</button>
        {savePreset.error && <p className="inline-error" role="alert">创建失败：{savePreset.error.message}</p>}
      </details>
    </fieldset>}
  </section>;
}
