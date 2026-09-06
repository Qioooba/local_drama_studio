import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  bindCharacterVoice,
  discoverLocalSapiVoices,
  listCharacterVoiceBindings,
  listVoiceProfileVersions,
  publishProjectLocalSapiVoiceProfile,
  type LocalSapiVoice,
  type VoiceProfileVersion,
} from "../../generated/api";

type CharacterVoicePanelProps = {
  projectId: string;
  assetId: string;
  assetName: string;
};

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

function isLocalTestProfile(profile: VoiceProfileVersion): boolean {
  const evidence = profile.license_evidence ?? {};
  return evidence.provenance === "LOCAL_OS_INSTALLED" && evidence.distribution_scope === "LOCAL_TEST_ONLY";
}

export function CharacterVoicePanel({ projectId, assetId, assetName }: CharacterVoicePanelProps) {
  const queryClient = useQueryClient();
  const [voices, setVoices] = useState<LocalSapiVoice[]>([]);
  const [selectedVoiceRef, setSelectedVoiceRef] = useState("");
  const [publishedProfile, setPublishedProfile] = useState<VoiceProfileVersion | null>(null);
  const [feedback, setFeedback] = useState("");
  const [failure, setFailure] = useState("");

  const profiles = useQuery({
    queryKey: ["project-voice-profiles", projectId],
    queryFn: () => listVoiceProfileVersions(projectId),
    enabled: Boolean(projectId),
  });
  const bindings = useQuery({
    queryKey: ["project-character-voice-bindings", projectId],
    queryFn: () => listCharacterVoiceBindings(projectId),
    enabled: Boolean(projectId),
  });
  const bound = useMemo(
    () => (bindings.data?.items ?? []).find((item) => item.character_asset_id === assetId) ?? null,
    [assetId, bindings.data?.items],
  );
  const localProfiles = useMemo(
    () => (profiles.data?.items ?? []).filter(isLocalTestProfile),
    [profiles.data?.items],
  );

  useEffect(() => {
    if (!selectedVoiceRef && voices.length > 0) setSelectedVoiceRef(voices[0].voice_ref);
  }, [selectedVoiceRef, voices]);
  useEffect(() => {
    if (!publishedProfile && selectedVoiceRef) {
      const existing = localProfiles.find((profile) => profile.voice_ref === selectedVoiceRef);
      if (existing) setPublishedProfile(existing);
    }
  }, [localProfiles, publishedProfile, selectedVoiceRef]);

  const discover = useMutation({
    mutationFn: () => discoverLocalSapiVoices(),
    onSuccess: (result) => {
      setVoices(result.items);
      setSelectedVoiceRef((current) => current || result.items[0]?.voice_ref || "");
      setFailure(result.message ?? "");
      setFeedback(result.items.length ? `已扫描到 ${result.items.length} 个本机 SAPI 音色；请选择一个用于本机验收。` : "本机没有可用 SAPI 音色。");
    },
    onError: (error) => setFailure("扫描本机音色失败：" + errorMessage(error)),
  });

  const publish = useMutation({
    mutationFn: () => publishProjectLocalSapiVoiceProfile(projectId, {
      voice_ref: selectedVoiceRef,
      smoke_text: "本集对白本机离线试听",
    }),
    onSuccess: async (result) => {
      setPublishedProfile(result.voice_profile);
      setFeedback("本机 SAPI 冒烟与 Profile 发布已完成；尚未绑定角色。请确认范围后继续绑定。");
      setFailure("");
      await queryClient.invalidateQueries({ queryKey: ["project-voice-profiles", projectId] });
    },
    onError: (error) => setFailure("发布本机测试音色失败：" + errorMessage(error)),
  });

  const bind = useMutation({
    mutationFn: () => bindCharacterVoice(projectId, {
      character_asset_id: assetId,
      voice_profile_version_id: publishedProfile?.id ?? "",
    }),
    onSuccess: async () => {
      setFeedback(`${assetName} 已绑定本机测试音色；生成范围仍为 LOCAL_TEST_ONLY。`);
      setFailure("");
      await queryClient.invalidateQueries({ queryKey: ["project-character-voice-bindings", projectId] });
      await queryClient.invalidateQueries({ queryKey: ["project-voice-profiles", projectId] });
    },
    onError: (error) => setFailure("绑定角色音色失败：" + errorMessage(error)),
  });

  const busy = discover.isPending || publish.isPending || bind.isPending;
  const canPublish = Boolean(selectedVoiceRef) && !busy;
  const canBind = Boolean(publishedProfile?.id) && !bound && !busy;

  return <section className="story-asset-create character-voice-panel" aria-labelledby={"character-voice-" + assetName}>
    <div><p className="eyebrow">对白声音</p><h4 id={"character-voice-" + assetName}>绑定 {assetName} 的本机音色</h4></div>
    <p className="muted">本机 SAPI 只证明当前 Windows 已安装该音色，不代表商业分发授权。来源会记录为 <strong>LOCAL_OS_INSTALLED</strong>，合成与交付范围固定为 <strong>LOCAL_TEST_ONLY</strong>。</p>
    {bound ? <p className="review-success" role="status">当前已绑定：{bound.voice.title} · {bound.voice.voice_ref}（本机验收）</p> : <>
      <div className="action-row"><button type="button" className="secondary" disabled={busy} onClick={() => discover.mutate()}>{discover.isPending ? "正在扫描…" : "扫描本机 SAPI 音色"}</button></div>
      {voices.length > 0 && <label>本机 SAPI 音色<select aria-label="本机 SAPI 音色" value={selectedVoiceRef} onChange={(event) => { setSelectedVoiceRef(event.target.value); setPublishedProfile(null); setFeedback(""); }} disabled={busy}><option value="">选择扫描到的音色</option>{voices.map((voice) => <option key={voice.voice_ref} value={voice.voice_ref}>{voice.name} · {voice.culture} · {voice.gender}</option>)}</select></label>}
      {selectedVoiceRef && <p className="muted">选中：{selectedVoiceRef} · 仅本机试听/验收，不写入商业授权。</p>}
      <div className="action-row">
        <button type="button" className="secondary" disabled={!canPublish} onClick={() => publish.mutate()}>{publish.isPending ? "冒烟并发布中…" : publishedProfile ? "重新核验并发布本机 Profile" : "冒烟并发布本机 Profile"}</button>
        <button type="button" className="primary" disabled={!canBind} onClick={() => bind.mutate()}>{bind.isPending ? "绑定中…" : "绑定到当前角色"}</button>
      </div>
      {publishedProfile && <p className="muted" role="status">已发布 Profile：{publishedProfile.code} · {publishedProfile.voice_ref} · LOCAL_TEST_ONLY；请点击“绑定到当前角色”。</p>}
      {localProfiles.length > 0 && <p className="muted">已有本机验收 Profile：{localProfiles.map((profile) => profile.voice_ref).join("、")}</p>}
      {!voices.length && !profiles.isLoading && <p className="inline-error" role="status">尚未扫描并选择本机音色。</p>}
    </>}
    {feedback && <p className="review-success" role="status">{feedback}</p>}
    {failure && <p className="inline-error" role="alert">{failure}</p>}
  </section>;
}
