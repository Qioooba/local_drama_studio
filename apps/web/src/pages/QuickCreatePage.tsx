import { useQuery } from "@tanstack/react-query";
import { QuickGenerationWorkbench } from "../features/quick-create/QuickGenerationWorkbench";
import { listProfiles } from "../generated/api";
import { queryKeys } from "../query/queryKeys";

export function QuickCreatePage() {
  const profiles = useQuery({ queryKey: queryKeys.profiles.list(), queryFn: () => listProfiles() });
  return <div className="v2-page quick-create-page">
    <header className="quick-create-page__header">
      <div><p className="eyebrow">独立创作工具</p><h1>快速生成</h1><p>输入一个画面描述，生成图片候选或视频作品。适合灵感试验和单条内容，不进入短剧项目生产线。</p></div>
      <dl aria-label="快速生成与项目的区别"><div><dt>快速生成</dt><dd>一次描述，一条独立作品</dd></div><div><dt>项目</dt><dd>故事、分镜、角色与多集生产</dd></div></dl>
    </header>
    {profiles.isPending ? <section className="panel" role="status">正在读取本机生成能力…</section> : profiles.error ? <section className="panel"><p className="inline-error" role="alert">生成能力读取失败：{String(profiles.error)}</p><button type="button" className="secondary" onClick={() => void profiles.refetch()}>重新读取</button></section> : <QuickGenerationWorkbench models={profiles.data?.models ?? []} />}
  </div>;
}
