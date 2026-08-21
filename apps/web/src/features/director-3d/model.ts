import type { Director3DOutput, Director3DValue, Director3DVector } from "./types";

export const DEFAULT_DIRECTOR_3D_VALUE: Director3DValue = {
  schema_version: "director-staging-3d.v1",
  scene: { label: "双人对话场景", width_m: 8, depth_m: 6 },
  participants: [
    { id: "A", label: "角色 A", position: { x: -1.25, y: 0, z: 0 }, facing_degrees: 90 },
    { id: "B", label: "角色 B", position: { x: 1.25, y: 0, z: 0 }, facing_degrees: 270 },
  ],
  camera: { position: { x: 4.8, y: 2.3, z: 5.8 }, target: { x: 0, y: 1.15, z: 0 }, fov_degrees: 48, movement: "STATIC", intensity: .35 },
};

export const cloneDirector3DValue = (value: Director3DValue): Director3DValue => JSON.parse(JSON.stringify(value)) as Director3DValue;
const fixed = (value: number) => Math.round(value * 100) / 100;
const vectorText = (value: Director3DVector) => `${fixed(value.x)}, ${fixed(value.y)}, ${fixed(value.z)}`;

function cameraDirection(value: Director3DValue) {
  const dx = value.camera.target.x - value.camera.position.x;
  const dz = value.camera.target.z - value.camera.position.z;
  if (Math.abs(dx) > Math.abs(dz)) return dx > 0 ? "RIGHT" : "LEFT";
  return dz > 0 ? "BACKWARD" : "FORWARD";
}

export function buildDirector3DOutput(value: Director3DValue): Director3DOutput {
  const [a, b] = value.participants;
  const blocking = `${a.label}位于(${vectorText(a.position)})，朝向${fixed(a.facing_degrees)}°；${b.label}位于(${vectorText(b.position)})，朝向${fixed(b.facing_degrees)}°。`;
  const prompt = `3D staging reference for ${value.scene.label}, room ${fixed(value.scene.width_m)}m × ${fixed(value.scene.depth_m)}m. ${blocking} Camera position (${vectorText(value.camera.position)}), target (${vectorText(value.camera.target)}), ${fixed(value.camera.fov_degrees)} degree field of view. Preserve two-person screen direction and the shown foreground/background depth.`;
  const cameraPlan = { movement: value.camera.movement, direction: cameraDirection(value), intensity: value.camera.intensity, prompt_text: prompt };
  return { blocking_summary: blocking, camera_plan: cameraPlan, prompt_context: prompt, staging_3d: cloneDirector3DValue(value), director_intent_patch: { composition: { depth_plan: `3D two-person staging; camera FOV ${fixed(value.camera.fov_degrees)}°` }, performance: { blocking_summary: blocking }, camera_plan: cameraPlan } };
}

