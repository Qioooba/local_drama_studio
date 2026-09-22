import { useCallback, useEffect, useState } from "react";

/**
 * A cascade scope field distinguishes "not initialised yet" from "the user
 * explicitly chose the empty option". Without that distinction an
 * `if (!episodeId) selectFirst()` effect silently overrides a deliberate
 * "project level only" / "episode level only" choice, so the inherited policy at
 * the upper scope can never actually be inspected.
 */
export type CascadeOption = { id: string };

export type CascadeState = {
  selectedId: string;
  /** True once the user (or a deep link) has decided this field, empty or not. */
  decided: boolean;
};

export type CascadeSelection = {
  season: CascadeState;
  episode: CascadeState;
  shot: CascadeState;
};

export const initialCascadeSelection = (initialEpisodeId = "", initialShotId = ""): CascadeSelection => ({
  season: { selectedId: "", decided: false },
  episode: { selectedId: initialEpisodeId, decided: Boolean(initialEpisodeId) },
  shot: { selectedId: initialShotId, decided: Boolean(initialShotId) },
});

const firstId = (options: readonly CascadeOption[] | undefined): string => options?.[0]?.id ?? "";

/** Runs on every render: default-select once per level, never re-select after the user cleared the level. */
export function autoSelectCascade(
  selection: CascadeSelection,
  options: { seasons?: readonly CascadeOption[]; episodes?: readonly CascadeOption[]; shots?: readonly CascadeOption[] },
): CascadeSelection {
  let next = selection;
  const decide = (state: CascadeState, option: string): CascadeState =>
    !state.decided && !state.selectedId && option ? { selectedId: option, decided: true } : state;

  const season = decide(next.season, firstId(options.seasons));
  if (season !== next.season) next = { ...next, season };
  const episode = decide(next.episode, firstId(options.episodes));
  if (episode !== next.episode) next = { ...next, episode };
  const shot = decide(next.shot, firstId(options.shots));
  if (shot !== next.shot) next = { ...next, shot };
  return next;
}

/** Changes one level and clears only the descendants that belong to it. */
export function chooseCascadeLevel(
  selection: CascadeSelection,
  level: "season" | "episode",
  value: string,
): CascadeSelection {
  if (level === "season") {
    return {
      season: { selectedId: value, decided: true },
      episode: { selectedId: "", decided: value === "" },
      shot: { selectedId: "", decided: false },
    };
  }
  return {
    ...selection,
    episode: { selectedId: value, decided: true },
    shot: { selectedId: "", decided: value === "" },
  };
}

/**
 * Cascade scope state for the QC policy manager. `episodeId`/`shotId` are empty
 * strings for an explicit upper-scope choice and are never auto-refilled once the
 * user has decided the level.
 */
export function useCascadeScope(initialEpisodeId = "", initialShotId = "") {
  const [selection, setSelection] = useState<CascadeSelection>(() => initialCascadeSelection(initialEpisodeId, initialShotId));
  const [seasons, setSeasons] = useState<readonly CascadeOption[] | undefined>(undefined);
  const [episodes, setEpisodes] = useState<readonly CascadeOption[] | undefined>(undefined);
  const [shots, setShots] = useState<readonly CascadeOption[] | undefined>(undefined);

  useEffect(() => {
    setSelection((current) => autoSelectCascade(current, { seasons, episodes, shots }));
  }, [seasons, episodes, shots]);

  const chooseSeason = useCallback((value: string) => setSelection((current) => chooseCascadeLevel(current, "season", value)), []);
  const chooseEpisode = useCallback((value: string) => setSelection((current) => chooseCascadeLevel(current, "episode", value)), []);
  const chooseShot = useCallback((value: string) => setSelection((current) => ({ ...current, shot: { selectedId: value, decided: true } })), []);

  return {
    seasonId: selection.season.selectedId,
    episodeId: selection.episode.selectedId,
    shotId: selection.shot.selectedId,
    setSeasons,
    setEpisodes,
    setShots,
    chooseSeason,
    chooseEpisode,
    chooseShot,
  };
}
