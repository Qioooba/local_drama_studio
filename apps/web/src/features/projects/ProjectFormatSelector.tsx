export type ProjectFormatMode = "LANDSCAPE" | "PORTRAIT" | "CUSTOM";

export type ProjectFormatSelection = {
  mode: ProjectFormatMode;
  landscapeId: string;
  portraitId: string;
  customWidth: number;
  customHeight: number;
  fps: number;
};

type CommonResolution = {
  id: string;
  label: string;
  width: number;
  height: number;
  ratio: "16:9" | "9:16";
};

const LANDSCAPE_RESOLUTIONS: CommonResolution[] = [
  { id: "854x480", label: "480P", width: 854, height: 480, ratio: "16:9" },
  { id: "1280x720", label: "720P · HD", width: 1280, height: 720, ratio: "16:9" },
  { id: "1920x1080", label: "1080P · Full HD", width: 1920, height: 1080, ratio: "16:9" },
  { id: "2560x1440", label: "1440P · 2K", width: 2560, height: 1440, ratio: "16:9" },
  { id: "3840x2160", label: "2160P · 4K", width: 3840, height: 2160, ratio: "16:9" },
];

const PORTRAIT_RESOLUTIONS: CommonResolution[] = [
  { id: "480x854", label: "480P", width: 480, height: 854, ratio: "9:16" },
  { id: "720x1280", label: "720P · HD", width: 720, height: 1280, ratio: "9:16" },
  { id: "1080x1920", label: "1080P · Full HD", width: 1080, height: 1920, ratio: "9:16" },
  { id: "1440x2560", label: "1440P · 2K", width: 1440, height: 2560, ratio: "9:16" },
  { id: "2160x3840", label: "2160P · 4K", width: 2160, height: 3840, ratio: "9:16" },
];

export const DEFAULT_PROJECT_FORMAT: ProjectFormatSelection = {
  mode: "PORTRAIT",
  landscapeId: "1920x1080",
  portraitId: "1080x1920",
  customWidth: 1080,
  customHeight: 1920,
  fps: 24,
};

function greatestCommonDivisor(left: number, right: number): number {
  let a = Math.abs(Math.round(left));
  let b = Math.abs(Math.round(right));
  while (b) [a, b] = [b, a % b];
  return a || 1;
}

function customRatio(width: number, height: number): string {
  const divisor = greatestCommonDivisor(width, height);
  return `${Math.round(width) / divisor}:${Math.round(height) / divisor}`;
}

export function resolveProjectFormat(selection: ProjectFormatSelection): { width: number; height: number; ratio: string; title: string } {
  if (selection.mode === "CUSTOM") {
    return {
      width: selection.customWidth,
      height: selection.customHeight,
      ratio: customRatio(selection.customWidth, selection.customHeight),
      title: "自定义画幅",
    };
  }
  const options = selection.mode === "LANDSCAPE" ? LANDSCAPE_RESOLUTIONS : PORTRAIT_RESOLUTIONS;
  const selectedId = selection.mode === "LANDSCAPE" ? selection.landscapeId : selection.portraitId;
  const resolution = options.find((item) => item.id === selectedId) ?? options[2];
  return { width: resolution.width, height: resolution.height, ratio: resolution.ratio, title: `${selection.mode === "LANDSCAPE" ? "横屏" : "竖屏"} ${resolution.label.split(" · ")[0]}` };
}

export function projectFormatIsValid(selection: ProjectFormatSelection): boolean {
  if (![24, 25, 30, 50, 60].includes(selection.fps)) return false;
  if (selection.mode !== "CUSTOM") return true;
  return [selection.customWidth, selection.customHeight].every((value) => Number.isInteger(value) && value >= 64 && value <= 16384 && value % 2 === 0);
}

function ResolutionColumn({
  mode,
  title,
  description,
  previewClass,
  options,
  selectedId,
  active,
  onActivate,
  onResolutionChange,
}: {
  mode: Exclude<ProjectFormatMode, "CUSTOM">;
  title: string;
  description: string;
  previewClass: string;
  options: CommonResolution[];
  selectedId: string;
  active: boolean;
  onActivate: () => void;
  onResolutionChange: (id: string) => void;
}) {
  const selectId = `project-format-${mode.toLowerCase()}`;
  return <section className={`creator-format-column${active ? " selected" : ""}`}>
    <label className="creator-format-orientation">
      <input type="radio" name="project-format-mode" value={mode} checked={active} onChange={onActivate} />
      <span className={`creator-format-preview ${previewClass}`} aria-hidden="true" />
      <span><strong>{title}</strong><small>{description}</small></span>
    </label>
    <label className="creator-resolution-select" htmlFor={selectId}>常用分辨率
      <select id={selectId} value={selectedId} onFocus={onActivate} onChange={(event) => { onActivate(); onResolutionChange(event.target.value); }}>
        {options.map((option) => <option key={option.id} value={option.id}>{option.label} · {option.width} × {option.height}</option>)}
      </select>
    </label>
  </section>;
}

export function ProjectFormatSelector({ value, onChange }: { value: ProjectFormatSelection; onChange: (next: ProjectFormatSelection) => void }) {
  const resolved = resolveProjectFormat(value);
  const customValid = projectFormatIsValid(value);
  return <fieldset className="creator-format-fieldset">
    <legend>发布画幅</legend>
    <p className="creator-format-intro">先选横竖方向，再选清晰度。这里只决定制作画面，具体发布平台可在交付阶段配置。</p>
    <div className="creator-format-grid">
      <ResolutionColumn mode="LANDSCAPE" title="横屏 16:9" description="长视频、桌面端与电视" previewClass="landscape" options={LANDSCAPE_RESOLUTIONS} selectedId={value.landscapeId} active={value.mode === "LANDSCAPE"} onActivate={() => onChange({ ...value, mode: "LANDSCAPE" })} onResolutionChange={(landscapeId) => onChange({ ...value, mode: "LANDSCAPE", landscapeId })} />
      <ResolutionColumn mode="PORTRAIT" title="竖屏 9:16" description="短剧、短视频与移动端" previewClass="portrait" options={PORTRAIT_RESOLUTIONS} selectedId={value.portraitId} active={value.mode === "PORTRAIT"} onActivate={() => onChange({ ...value, mode: "PORTRAIT" })} onResolutionChange={(portraitId) => onChange({ ...value, mode: "PORTRAIT", portraitId })} />
    </div>
    <section className={`creator-custom-format${value.mode === "CUSTOM" ? " selected" : ""}`}>
      <label className="creator-custom-format-toggle"><input type="radio" name="project-format-mode" value="CUSTOM" checked={value.mode === "CUSTOM"} onChange={() => onChange({ ...value, mode: "CUSTOM" })} /><span><strong>自定义尺寸</strong><small>用于方形、超宽屏或特殊交付画布</small></span></label>
      {value.mode === "CUSTOM" && <div className="creator-custom-format-fields">
        <label htmlFor="project-custom-width">宽度（像素）<input id="project-custom-width" type="number" inputMode="numeric" min={64} max={16384} step={2} value={value.customWidth} onChange={(event) => onChange({ ...value, customWidth: Number(event.target.value) })} /></label>
        <span aria-hidden="true">×</span>
        <label htmlFor="project-custom-height">高度（像素）<input id="project-custom-height" type="number" inputMode="numeric" min={64} max={16384} step={2} value={value.customHeight} onChange={(event) => onChange({ ...value, customHeight: Number(event.target.value) })} /></label>
        {!customValid && <p className="inline-error" role="alert">宽高必须是 64–16384 之间的偶数，才能兼容常用视频编码器。</p>}
      </div>}
    </section>
    <div className="creator-format-footer">
      <label htmlFor="project-format-fps">项目帧率<select id="project-format-fps" value={value.fps} onChange={(event) => onChange({ ...value, fps: Number(event.target.value) })}><option value={24}>24 fps · 剧情推荐</option><option value={25}>25 fps</option><option value={30}>30 fps · 短视频常用</option><option value={50}>50 fps</option><option value={60}>60 fps · 高动态</option></select></label>
      <div className="creator-format-result" aria-live="polite"><small>当前制作规格</small><strong>{resolved.title}</strong><span>{resolved.width} × {resolved.height} · {value.fps} fps · {resolved.ratio}</span></div>
    </div>
  </fieldset>;
}
