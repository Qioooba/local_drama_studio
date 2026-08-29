import { useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { cloneCharacterVoice } from "../../generated/api";
import { uploadProjectMediaFile } from "../media-picker/mediaPickerClient";
import { mediaContentUrl } from "../shared/mediaPlaybackPolicy";

type VoiceClonePanelProps = {
  projectId: string;
  assetId: string;
  assetName: string;
  onChanged?: () => Promise<void> | void;
};

function message(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

/** §6.1.2: upload a ~10s reference, clone the voice, bind it to the character. */
export function VoiceClonePanel({ projectId, assetId, assetName, onChanged }: VoiceClonePanelProps) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [referenceId, setReferenceId] = useState("");
  const [title, setTitle] = useState("");
  const [transcript, setTranscript] = useState("");
  const [consent, setConsent] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");

  const clone = useMutation({
    mutationFn: async () => {
      return cloneCharacterVoice(projectId, assetId, {
        media_version_id: referenceId,
        title: title.trim(),
        transcript: transcript.trim(),
        consent,
      });
    },
    onSuccess: async (result) => {
      setDone(`已创建克隆声线「${String((result.voice as { title?: string }).title ?? "")}」并绑定到 ${assetName}。`);
      setReferenceId("");
      setTitle("");
      setTranscript("");
      setConsent(false);
      if (fileRef.current) fileRef.current.value = "";
      await onChanged?.();
    },
  });

  async function upload(file: File | undefined) {
    if (!file) return;
    setUploading(true);
    setError("");
    try {
      setReferenceId(await uploadProjectMediaFile(projectId, file));
    } catch (reason) {
      setError(message(reason));
    } finally {
      setUploading(false);
    }
  }

  return <section className="voice-clone-panel" aria-labelledby={`voice-clone-${assetId}`}>
    <div className="adaptation-section-heading">
      <span>声线克隆</span>
      <div><h3 id={`voice-clone-${assetId}`}>上传 10 秒参考音频，克隆 {assetName} 的声音</h3>
        <p>参考音频只留在本机项目内；克隆声线通过本机 VoxCPM2 在 TTS 时合成。生成对白候选即可试听克隆效果。</p></div>
    </div>
    <label>参考音频（2—15 秒，wav/mp3）<input ref={fileRef} type="file" accept="audio/*" disabled={uploading || clone.isPending} onChange={(event) => void upload(event.target.files?.[0])} /></label>
    {referenceId && <audio controls preload="metadata" src={mediaContentUrl(referenceId)} aria-label="参考音频试听" />}
    <label>声线名称<input value={title} maxLength={80} placeholder={`例如：${assetName}·平静`} disabled={clone.isPending} onChange={(event) => setTitle(event.target.value)} /></label>
    <label>参考音频的文字内容（可选，提高克隆相似度）<textarea value={transcript} rows={2} maxLength={200} placeholder="逐字写下音频里说的话" disabled={clone.isPending} onChange={(event) => setTranscript(event.target.value)} /></label>
    <label className="voice-clone-consent"><input type="checkbox" checked={consent} disabled={clone.isPending} onChange={(event) => setConsent(event.target.checked)} />我确认拥有该声音的克隆授权，仅用于本人项目。</label>
    <button type="button" className="primary" disabled={!referenceId || !consent || uploading || clone.isPending} onClick={() => clone.mutate()}>
      {clone.isPending ? "创建克隆声线…" : "创建克隆声线并绑定角色"}
    </button>
    {error && <p className="inline-error" role="alert">{error}</p>}
    {clone.error && <p className="inline-error" role="alert">{message(clone.error)}</p>}
    {done && <p className="muted" role="status">{done}</p>}
  </section>;
}
