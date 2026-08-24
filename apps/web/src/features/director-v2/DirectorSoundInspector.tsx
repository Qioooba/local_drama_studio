import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { listCharacterVoiceBindings, listEpisodeAudioBindings, type AudioBinding, type CharacterVoiceBinding } from "../../generated/api";

type BoundAsset = { id?: string; kind?: string; code?: string; name?: string; role_in_shot?: string };

type DirectorSoundInspectorProps = {
  projectId: string;
  episodeId: string;
  shotCode: string;
  dialogue: string;
  assets: Array<Record<string, unknown>>;
};

const trackLabels: Record<string, string> = { DIALOGUE: "对白", BGM: "BGM / 音乐", MUSIC: "音乐（旧轨道）", SFX: "音效", ENVIRONMENT: "环境音（旧轨道）" };
const seconds = (value: number) => `${(value / 1_000_000).toFixed(2)}s`;

function AudioFact({ binding }: { binding: AudioBinding }) {
  return <li>
    <div><strong>{trackLabels[binding.track_type] ?? binding.track_type}</strong><span>{seconds(binding.start_us)}–{seconds(binding.end_us)}</span></div>
    <small>{binding.gain_db.toFixed(1)} dB · {binding.loop_enabled ? "循环" : "单次"} · fade {binding.fade_in_us / 1000}/{binding.fade_out_us / 1000} ms</small>
    <span className={binding.authorization_status === "VERIFIED_EVIDENCE" ? "director-sound-ok" : "director-sound-warning"}>{binding.authorization_status === "VERIFIED_EVIDENCE" ? "授权证据已验证" : "授权证据不完整"}</span>
  </li>;
}

export function DirectorSoundInspector({ projectId, episodeId, shotCode, dialogue, assets }: DirectorSoundInspectorProps) {
  const voices = useQuery({ queryKey: ["director-shot-voices", projectId], queryFn: () => listCharacterVoiceBindings(projectId), enabled: Boolean(projectId) });
  const audio = useQuery({ queryKey: ["director-episode-audio", episodeId], queryFn: () => listEpisodeAudioBindings(episodeId), enabled: Boolean(episodeId) });
  const characters = assets.filter((asset) => asset.kind === "CHARACTER") as BoundAsset[];
  const voiceItems = voices.data?.items ?? [];
  const audioItems = audio.data?.items ?? [];
  const loading = voices.isLoading || audio.isLoading;
  const error = voices.error ?? audio.error;
  const voiceFor = (character: BoundAsset): CharacterVoiceBinding | undefined => voiceItems.find((binding) => binding.character_asset_id === character.id);

  return <section className="director-sound-inspector" aria-labelledby="director-sound-title">
    <div className="director-section-head"><strong id="director-sound-title">{shotCode} · 声音事实</strong><Link to={`/projects/${projectId}/episodes/${episodeId}/audio`}>打开声音工作区</Link></div>
    <div className="director-sound-copy"><strong>台词 / 旁白</strong><p>{dialogue || "本镜没有台词或旁白"}</p></div>
    <p className="director-help">台词来自当前镜头 revision。音色是项目级角色绑定；BGM / SFX 是分集时间范围绑定，当前没有镜头级声音写入命令。</p>
    {loading && <p className="director-sound-state" role="status">正在读取真实声音绑定…</p>}
    {error && <p className="director-sound-state error" role="alert">声音事实读取失败：{error instanceof Error ? error.message : String(error)}</p>}
    {!loading && !error && <>
      <div className="director-sound-subhead"><strong>镜头内角色音色</strong><Link to={`/projects/${projectId}/assets`}>管理角色资产</Link></div>
      {characters.length === 0 ? <p className="director-sound-state">本镜尚未绑定角色。先在“角色场景”页签选择真实故事资产。</p> : <ul className="director-sound-list">
        {characters.map((character) => {
          const binding = voiceFor(character);
          return <li key={character.id ?? character.code}><div><strong>{character.name ?? character.code ?? "未命名角色"}</strong><span>{character.role_in_shot || "main"}</span></div><small>{binding ? `${binding.voice.title} · ${binding.voice.code}` : "尚未绑定已登记音色"}</small><span className={binding ? "director-sound-ok" : "director-sound-warning"}>{binding ? "项目音色已绑定" : "需前往声音工作区绑定"}</span></li>;
        })}
      </ul>}
      <div className="director-sound-subhead"><strong>分集音频轨道</strong><span>只读 · Episode scope</span></div>
      {audioItems.length === 0 ? <p className="director-sound-state">本集还没有持久化 BGM、SFX 或对白音频绑定。</p> : <ul className="director-sound-list">{audioItems.map((binding) => <AudioFact key={binding.id} binding={binding} />)}</ul>}
    </>}
    <p className="director-help">此检查器不会请求或自动播放音频；试听、导入、轨道绑定与授权证据编辑均在声音工作区完成。</p>
  </section>;
}
