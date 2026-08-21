export type Director3DVector = { x: number; y: number; z: number };
export type Director3DParticipant = { id: "A" | "B"; label: string; position: Director3DVector; facing_degrees: number };

export type Director3DValue = {
  schema_version: "director-staging-3d.v1";
  scene: { label: string; width_m: number; depth_m: number };
  participants: [Director3DParticipant, Director3DParticipant];
  camera: { position: Director3DVector; target: Director3DVector; fov_degrees: number; movement: string; intensity: number };
};

export type Director3DOutput = {
  blocking_summary: string;
  camera_plan: { movement: string; direction: string; intensity: number; prompt_text: string };
  prompt_context: string;
  staging_3d: Director3DValue;
  director_intent_patch: {
    composition: { depth_plan: string };
    performance: { blocking_summary: string };
    camera_plan: Director3DOutput["camera_plan"];
  };
};

export type Director3DReferenceExport = {
  blob: Blob;
  filename: string;
  width: number;
  height: number;
  mime_type: "image/png";
  staging_3d: Director3DValue;
  prompt_context: string;
};

