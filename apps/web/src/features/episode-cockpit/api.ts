export type EpisodeCockpit = {
  episode: { id: string; code: string; title: string; project_id: string };
  shots: { total: number; directed: number; with_candidates: number; remaining_generation: number; selected: number; approved: number; failed: number; stale: number };
  jobs: { failed: number };
  bridges: { total: number; ready: number; stale: number };
  audio: { bindings: number; verified: number };
  qc: { candidate_versions: number; checked: number; passed: number; failed: number };
  blockers: Array<{ code: string; count: number; label: string }>;
  observed_at: string;
  read_only: true;
  mutated: false;
};

export async function getEpisodeCockpit(episodeId: string): Promise<EpisodeCockpit> {
  const response = await fetch(`/api/v1/episodes/${encodeURIComponent(episodeId)}/cockpit`);
  const body = await response.json().catch(() => null) as { cockpit?: EpisodeCockpit; error?: { message?: string } } | null;
  if (!response.ok || !body?.cockpit) throw new Error(body?.error?.message ?? `无法读取分集驾驶舱（${response.status}）`);
  return body.cockpit;
}
