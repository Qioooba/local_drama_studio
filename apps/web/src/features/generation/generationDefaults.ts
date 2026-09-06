/** Stable per-shot seed derivation for reproducible first candidates. */
export function deriveShotSeed(shotId: string | null, fallback = 42): number {
  if (!shotId) return fallback;
  let hash = 2166136261;
  for (let index = 0; index < shotId.length; index += 1) {
    hash ^= shotId.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  const seed = (hash >>> 0) % 900_000 + 100_000;
  return Number.isFinite(seed) ? seed : fallback;
}

/** Compose a useful starting prompt from the typed Shot director intent. */
export function composePromptFromIntent(fields: Record<string, unknown>, shotCode?: string | null): string {
  const camera = fields.camera_plan && typeof fields.camera_plan === "object" ? fields.camera_plan as Record<string, unknown> : {};
  const performance = fields.performance && typeof fields.performance === "object" ? fields.performance as Record<string, unknown> : {};
  const parts: string[] = [];
  if (shotCode) parts.push(`镜头 ${shotCode}`);
  const action = typeof fields.subject_action === "string" ? fields.subject_action : "";
  if (action) parts.push(action);
  const intent = typeof fields.creative_intent === "string" ? fields.creative_intent : "";
  if (intent) parts.push(`情绪基调：${intent}`);
  const shotType = typeof camera.shot_type === "string" && camera.shot_type
    ? camera.shot_type
    : typeof fields.shot_type === "string" && fields.shot_type ? fields.shot_type : "";
  if (shotType) parts.push(`景别 ${shotType}`);
  const movement = typeof camera.movement === "string" && camera.movement && camera.movement !== "STATIC" ? camera.movement : "";
  if (movement) parts.push(`运镜 ${movement}`);
  const emotion = typeof performance.emotion === "string" && performance.emotion ? performance.emotion : "";
  if (emotion) parts.push(`情绪 ${emotion}`);
  const dialogue = Array.isArray(fields.dialogue)
    ? fields.dialogue.map((line) => typeof line === "string"
      ? line
      : typeof line === "object" && line
        ? String((line as Record<string, unknown>).text ?? (line as Record<string, unknown>).speaker ?? "")
        : "").filter(Boolean).join("；")
    : typeof fields.dialogue === "string" ? fields.dialogue : "";
  if (dialogue) parts.push(`对白：${dialogue}`);
  const modifiers = Array.isArray(fields.prompt_modifiers)
    ? fields.prompt_modifiers.map((item) => typeof item === "string" ? item.trim() : "").filter(Boolean)
    : [];
  if (modifiers.length) parts.push(`统一视觉修饰：${modifiers.join("，")}`);
  return parts.filter(Boolean).join("，");
}
