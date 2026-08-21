import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { TabPanel, Tabs, type TabItem } from "../components/ui";
import { listDialogueLines, listEpisodeAudioBindings, listProfiles, listVoiceProfileVersions, reviewInbox } from "../generated/api";
import { AudioEpisodeOverview } from "../features/audio-v2/AudioEpisodeOverview";
import { AudioTrackPanel } from "../features/status/AudioTrackPanel";
import { DialogueTTSPanel } from "../features/status/DialogueTTSPanel";
import "./creative-workspaces.css";

type AudioTask = "dialogue" | "tracks" | "evidence";

const AUDIO_TASKS = new Set<AudioTask>(["dialogue", "tracks", "evidence"]);
const AUDIO_TABS: TabItem[] = [
  { id: "dialogue", label: "台词与 TTS" },
  { id: "tracks", label: "音效与配乐" },
  { id: "evidence", label: "缺口与证据" },
];

export function AudioPage() {
  const { projectId, episodeId } = useParams();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedTask = searchParams.get("view") as AudioTask | null;
  const activeTask: AudioTask = requestedTask && AUDIO_TASKS.has(requestedTask) ? requestedTask : "dialogue";
  const selectTask = (task: string) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      if (task === "dialogue") next.delete("view");
      else next.set("view", task);
      return next;
    }, { replace: true });
  };
  const dialogue = useQuery({ queryKey: ["episode", episodeId, "dialogue-lines"], queryFn: () => listDialogueLines(episodeId as string), enabled: Boolean(episodeId) && activeTask !== "tracks" });
  const voices = useQuery({ queryKey: ["project", projectId, "voice-profiles"], queryFn: () => listVoiceProfileVersions(projectId as string), enabled: Boolean(projectId) && activeTask !== "tracks" });
  const profiles = useQuery({ queryKey: ["profiles"], queryFn: () => listProfiles(), enabled: Boolean(projectId) && activeTask === "dialogue" });
  const audio = useQuery({
    queryKey: ["episode", episodeId, "audio-bindings"],
    queryFn: () => listEpisodeAudioBindings(episodeId as string),
    enabled: Boolean(episodeId) && activeTask !== "dialogue",
  });
  const audioReview = useQuery({
    queryKey: ["review-inbox", projectId, episodeId, "AUDIO"],
    queryFn: () => reviewInbox(projectId, "", { episode_id: episodeId, media_kind: "AUDIO" }),
    enabled: Boolean(projectId && episodeId) && activeTask === "evidence",
  });
  if (!projectId || !episodeId) return <p className="inline-error" role="alert">缺少项目或分集上下文。</p>;
  const refreshDialogue = () => {
    void dialogue.refetch();
    void voices.refetch();
    void profiles.refetch();
    void audioReview.refetch();
    void queryClient.invalidateQueries({ queryKey: ["jobs"] });
  };
  const activeQueries = activeTask === "dialogue"
    ? [dialogue, voices, profiles]
    : activeTask === "tracks"
      ? [audio]
      : [dialogue, voices, audio, audioReview];
  return <div className="v2-page creative-task-page audio-workspace-v2">
    <div className="panel-heading"><div><p className="eyebrow">本集声音 · Audio Workspace</p><h2>台词、角色声音与音轨编排</h2></div><div className="action-row"><Link className="secondary v2-inline-link" to={`/projects/${projectId}/episodes/${episodeId}/review`}>音频审核</Link><Link className="primary-action v2-inline-link" to={`/projects/${projectId}/episodes/${episodeId}/timeline`}>进入时间线</Link></div></div>
    <p className="muted">从台词 revision 到 speaker / voice、TTS takes、selected audio、BGM 与 SFX 的单一工作台；所有写入复用既有领域动作，试听默认不预加载。</p>
    {activeQueries.some((query) => query.isPending) && <p className="empty-state creative-task-loading" aria-live="polite">正在读取当前声音任务…</p>}
    {activeQueries.some((query) => query.isError) && <p className="inline-error" role="alert">当前声音任务读取失败：{activeQueries.map((query) => query.error).filter(Boolean).map(String).join("；")}</p>}
    <Tabs items={AUDIO_TABS} selectedId={activeTask} onChange={selectTask} ariaLabel="本集声音任务">
      <TabPanel id="dialogue" selectedId={activeTask}>
        <DialogueTTSPanel lines={dialogue.data?.items ?? []} voices={voices.data?.items ?? []} profiles={profiles.data?.items ?? []} projectId={projectId} episodeId={episodeId} onChanged={refreshDialogue} />
      </TabPanel>
      <TabPanel id="tracks" selectedId={activeTask}>
        <AudioTrackPanel bindings={audio.data?.items ?? []} projectId={projectId} episodeId={episodeId} onBound={() => { void audio.refetch(); void audioReview.refetch(); }} />
      </TabPanel>
      <TabPanel id="evidence" selectedId={activeTask}>
        <AudioEpisodeOverview lines={dialogue.data?.items ?? []} voices={voices.data?.items ?? []} bindings={audio.data?.items ?? []} reviewItems={audioReview.data?.items ?? []} />
      </TabPanel>
    </Tabs>
  </div>;
}
