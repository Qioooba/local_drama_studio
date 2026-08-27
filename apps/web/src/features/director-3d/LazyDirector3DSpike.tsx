import { lazy, Suspense, useState } from "react";
import type { Director3DSpikeProps } from "./Director3DSpike";

const LazySpike = lazy(() => import("./Director3DSpike").then((module) => ({ default: module.Director3DSpike })));

/** Opt-in loader keeps the 3D renderer and its CSS out of the regular Director Desk startup path. */
export function LazyDirector3DSpike(props: Director3DSpikeProps & { buttonLabel?: string }) {
  const [enabled, setEnabled] = useState(false);
  if (!enabled) return <section className="panel"><div className="panel-heading"><div><p className="eyebrow">可选技术预演</p><h3>三维导演预演</h3></div><span className="status-pill neutral">按需加载</span></div><p className="muted">启用后才会加载三维场景模块，未使用时不会占用常规导演台的渲染资源。</p><button type="button" className="secondary" onClick={() => setEnabled(true)}>{props.buttonLabel ?? "加载三维预演"}</button></section>;
  return <Suspense fallback={<p className="empty-state" aria-live="polite">正在加载 3D 预演模块…</p>}><LazySpike {...props} /></Suspense>;
}
