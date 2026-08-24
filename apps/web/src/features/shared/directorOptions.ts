export const SHOT_TYPES = [
  ["ESTABLISHING", "大全景"], ["WIDE", "全景"], ["MEDIUM", "中景"], ["MEDIUM_CLOSE", "中近景"],
  ["CLOSEUP", "近景"], ["EXTREME_CLOSEUP", "特写"], ["POV", "主观镜头"], ["INSERT", "插入镜头"], ["OTHER", "其他"],
] as const;

export const COMPOSITIONS = [
  ["CENTER", "居中"], ["LEFT_THIRD", "左三分"], ["RIGHT_THIRD", "右三分"], ["SYMMETRY", "对称"],
  ["OVER_SHOULDER", "过肩"], ["TWO_SHOT", "双人"], ["LOW_ANGLE", "低机位"], ["HIGH_ANGLE", "高机位"],
] as const;

export const CAMERA_MOVEMENTS = [
  ["STATIC", "固定"], ["PUSH_IN", "推进"], ["PULL_OUT", "拉远"], ["PAN", "摇摄"], ["TILT", "俯仰"],
  ["TRUCK", "横移"], ["PEDESTAL", "升降"], ["ZOOM", "变焦"], ["ORBIT", "环绕"], ["ROLL", "滚转"],
] as const;

export const CAMERA_DIRECTIONS = ["FORWARD", "BACKWARD", "LEFT", "RIGHT", "UP", "DOWN", "CLOCKWISE", "COUNTERCLOCKWISE"] as const;

export const CAMERA_DIRECTION_LABELS: Record<(typeof CAMERA_DIRECTIONS)[number], string> = {
  FORWARD: "向前",
  BACKWARD: "向后",
  LEFT: "向左",
  RIGHT: "向右",
  UP: "向上",
  DOWN: "向下",
  CLOCKWISE: "顺时针",
  COUNTERCLOCKWISE: "逆时针",
};

export const CAMERA_CURVES = [
  ["LINEAR", "匀速"],
  ["EASE_IN", "缓慢起步"],
  ["EASE_OUT", "缓慢停止"],
  ["EASE_IN_OUT", "平滑起止"],
] as const;

export const CAMERA_MOVEMENT_LABELS = Object.fromEntries(CAMERA_MOVEMENTS) as Record<(typeof CAMERA_MOVEMENTS)[number][0], string>;

export const SHOT_ASSET_ROLES = [
  ["main", "主要主体"], ["secondary", "次要主体"], ["background", "背景主体"], ["location", "场景 / 地点"],
  ["prop", "道具"], ["costume", "服装"], ["atmosphere", "氛围元素"],
] as const;
