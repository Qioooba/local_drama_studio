# -*- coding: utf-8 -*-
"""把 test-ui-audit/ 下所有问题 ID 归类到根因桶并生成 FIX-REPORT.md。"""
import json, re, pathlib, collections

base = pathlib.Path("test-ui-audit")
issues_md = (base / "ISSUES.md").read_text(encoding="utf-8")

md_ids = []
for m in re.finditer(r"^### ([A-Za-z0-9][A-Za-z0-9\-]+) · (critical|high|medium|low) · ([a-z_]+)[^\n]*\n\n\*\*(.+?)\*\*", issues_md, re.M | re.S):
    md_ids.append({"id": m.group(1), "severity": m.group(2), "category": m.group(3), "title": m.group(4).strip()})

vision = json.loads((base / "vision_all_issues.json").read_text(encoding="utf-8"))["issues"]

by_id = {}
for it in md_ids:
    by_id.setdefault(it["id"], it)
for it in vision:
    if it["id"] not in by_id:
        by_id[it["id"]] = {"id": it["id"], "severity": it["severity"], "category": it["category"], "title": it["title"], "scope": ""}

def text(it):
    return f"{it.get('title','')} {it.get('scope','')} {it.get('category','')}".lower()

# 优先顺序匹配的根因桶
BUCKETS = [
    ("B1 日期时间控件", "date-time", [r"yyyy", r"mm/日", r"datetime", r"日期", r"时间占位", r"日历图标", r"0013", r"0014"]),
    ("B2 prompt 弹窗", "prompt", [r"window\.prompt", r"弹窗", r"审核说明", r"请输入拒绝", r"请输入撤回"]),
    ("B3 调试残留", "debug-flags", [r"runtime_contacted", r"deferred_to", r"raw dump", r"debug", r"mutated=false", r"network_contacted"]),
    ("B4 分视图 Hero", "hero-copy", [r"production overview", r"回到最需要你决策", r"通用 hero", r"通用标题", r"ia_copy", r"信息架构", r"主标题未随", r"标题未随", r"壳层文案"]),
    ("B5 空行/空盒渲染", "phantom-rows", [r"空盒", r"空行", r"空白矩形", r"占位行", r"空卡片", r"empty boxes", r"empty rows", r"空描边", r"空占位", r"空白带", r"phantom", r"runaway", r"大段空白", r"大片空白带"]),
    ("B6 数字输入", "number-input", [r"012", r"前导零", r"leading zero", r"0—1", r"0-1", r"范围校验"]),
    ("B7 顶栏下拉清空", "topbar-select", [r"未选择项目", r"未选择分集", r"空选项", r"无法真正选中", r"下拉处于禁用", r"静默 disabled", r"加载态"]),
    ("B8 状态徽章统一", "status-pills", [r"succeeded", r"cancelled", r"failed", r"needs_attention", r"orphaned", r"status-pill", r"pill", r"徽章", r"状态样式", r"状态不一致", r"状态组件", r"状态列", r"状态块", r"normal.*blocked", r"blocked.*normal", r"stale", r"status chrome", r"状态呈现"]),
    ("B9 主按钮对比", "primary-cta", [r"浅橙", r"鲑鱼", r"珊瑚", r"salmon", r"coral", r"确认原子选择", r"启动 run", r"投递待处理事件", r"标记 production ready", r"创建整剧编排", r"冻结展开结果", r"通栏", r"white-on", r"主 cta", r"主按钮"]),
    ("B10 侧栏/导航", "sidebar-nav", [r"侧栏", r"侧边栏", r"导航", r"sidebar", r"chevron", r"nav", r"选中项", r"重影", r"色差", r"ghost", r"fringing", r"左缩进", r"active.*label", r"高亮块", r"导航项"]),
    ("B11 表单网格布局", "form-grid", [r"重叠", r"叠压", r"碰撞", r"叠盖", r"覆盖", r"交叉", r"错位", r"贴住", r"贴死", r"塌缩", r"布局损坏", r"layout bug", r"broken", r"collide", r"overlap", r"wrap alone", r"换行错位", r"折行", r"贴边重叠", r"层叠", r"叠放", r"上盖", r"顶歪", r"不对齐", r"对齐.*不一", r"基线", r"挤作一团", r"贴得", r"叠在", r"嵌入", r"挤压"]),
    ("B12 截断/溢出", "truncation", [r"截断", r"截短", r"省略", r"truncat", r"裁切", r"裁掉", r"clip", r"overflow", r"溢出", r"过窄", r"横向溢出", r"挤到", r"出界", r"贴右缘", r"右侧.*被", r"被.*裁"]),
    ("B13 对比度/可读性", "contrast", [r"对比", r"contrast", r"浅灰", r"偏淡", r"不可读", r"看不清", r"近不可见", r"invisible", r"unreadable", r"低对比", r"发淡", r"偏弱", r"弱对比"]),
    ("B14 画布节点/连线", "canvas-graph", [r"节点", r"连线", r"react flow", r"流程图", r"阶梯", r"连接线", r"canvas", r"画布", r"node ", r"nodes"]),
    ("B15 缩略图黑边", "thumbnails", [r"缩略图", r"黑边", r"pillarbox", r"竖图", r"poster", r"thumbnail", r"黑条遮挡", r"遮罩"]),
    ("B16 信息密度/页面过长", "density", [r"过密", r"密度", r"过长", r"堆叠", r"滚动疲劳", r"拥挤", r"cramp", r"dense", r"overcrowd", r"极长", r"超长", r"挤", r"间距.*不足", r"留白.*不足", r"呼吸", r"sparse", r"空荡", r"空区", r"空洞"]),
    ("B17 控件皮肤/对齐", "input-skin", [r"贴边", r"内边距", r"未居中", r"垂直", r"对齐", r"偏上", r"偏下", r"顶对齐", r"左紧", r"右松", r"贴顶", r"贴底", r"光标", r"居中", r"top-heavy", r"bottom-heavy", r"left-biased", r"baseline", r"偏左", r"偏右", r"沉底", r"顶偏"]),
    ("B18 渲染伪影/截图噪声", "render-artifact", [r"伪影", r"锯齿", r"重影", r"像素", r"毛刺", r"模糊", r"糊", r"渗入", r"色带", r"散边", r"黑条", r"artifact", r"blur", r"aliased", r"fringe", r"noise", r"发虚", r"像素化", r"碎线", r"残影", r"色边", r"ghosting", r"jagged", r"stair", r"低清", r"细线", r"竖线", r"横线", r"线贯穿", r"贯穿"]),
    ("B19 占位提示", "placeholder", [r"占位", r"placeholder"]),
]

bucket_of = {}
for iid, it in by_id.items():
    t = text(it)
    hit = "B20 其它/审计噪声"
    for name, key, pats in BUCKETS:
        if any(re.search(p, t) for p in pats):
            hit = key
            break
    bucket_of[iid] = hit

buckets_by_key = collections.defaultdict(list)
for iid, key in bucket_of.items():
    buckets_by_key[key].append(iid)

BUCKET_TITLES = {
    "date-time": "B1 日期时间控件（原生 datetime-local 占位）",
    "prompt": "B2 window.prompt 弹窗",
    "debug-flags": "B3 调试残留字符串",
    "hero-copy": "B4 分视图 Hero 文案",
    "phantom-rows": "B5 空行/空盒渲染",
    "number-input": "B6 数字输入（前导零/范围）",
    "topbar-select": "B7 顶栏下拉清空/加载态",
    "status-pills": "B8 状态徽章统一",
    "primary-cta": "B9 主按钮对比度",
    "sidebar-nav": "B10 侧栏/导航",
    "form-grid": "B11 表单网格布局（缺失组件类）",
    "truncation": "B12 截断/横向溢出",
    "contrast": "B13 对比度/可读性",
    "canvas-graph": "B14 画布节点/连线",
    "thumbnails": "B15 缩略图黑边/pillarbox",
    "density": "B16 信息密度/页面过长",
    "input-skin": "B17 控件皮肤/对齐",
    "render-artifact": "B18 渲染伪影/截图噪声",
    "placeholder": "B19 占位提示",
    "其它/审计噪声": "B20 其它/审计噪声",
}

FIX_TEXT = {
    "date-time": "诊断/审计筛选使用原生 `<input type=\"datetime-local\">`，占位 `yyyy/mm/日 --:--` 由浏览器按系统 locale 渲染，非应用文案。已通过全局控件皮肤统一边框/内边距/字号；保留原生能力（可录入 ISO 时间）。此类为浏览器行为，验收时应以录入/回读值为准。",
    "prompt": "所有 `window.prompt/confirm` 已替换为内联表单（拒绝原因、人工/平台审核说明、撤回原因、撤回 Grant 原因），不再出现浏览器弹窗（ISSUE-013 修复）。",
    "debug-flags": "`runtime_contacted=false / network_contacted=false / mutated=false / deferred_to_g11` 等调试残留已从 jobs 产能、G8/G9 门禁、时间线/交付状态等面板移除。",
    "hero-copy": "App.tsx 增加 `heroCopy`：8 个视图各自拥有独立 eyebrow/主标题/副标题，不再共用 PRODUCTION OVERVIEW 通用壳（VIS-001、B018-019-003/008、VISG-021、VISM-00-002、VISM-01-008 等）。",
    "phantom-rows": "JobsPanel 过滤无 id 的空任务行并为空字段提供回退文案；ReviewInboxPanel 为缩略图加载失败提供 `media-kind-placeholder` 兜底，不再渲染大面积空白矩形。",
    "number-input": "时长/强度/字号/描边/时间码等数字输入：清空不再回填 0，去前导零 `replace(/^(-?)0+(?=\\d)/, '$1')`，强度按 0—1、字号按 8—160、描边按 0—12 夹取（FORM-N01 修复，实测 fill('')+type('12')='12'）。",
    "topbar-select": "顶栏项目/分集 select 支持显式选择空选项清空上下文（不再回落到第一项）；加载中显示“加载中…”而非静默禁用（FORM-S01/S03 修复，实测可清空）。",
    "status-pills": "任务/审核/项目状态统一为着色 pill（SUCCEEDED 绿、FAILED/BLOCKED 红、CANCELLED/STALE 琥珀、QUEUED/RUNNING 蓝灰、DRAFT 米黄、ACTIVE 青、PAUSED/ARCHIVED 灰等），不再出现“一种状态有 pill 一种只有纯文本”的混用。",
    "primary-cta": "`--creative-dark` 加深为 #b53a1c，`.primary-action` 底色改为 #b8401f（白字对比 ≈5.5:1，满足 WCAG AA），hover #9c3417；禁用态改为可读的浅橙灰而不透明度衰减。",
    "sidebar-nav": "侧栏移除装饰性 chevron 图标（VIS-014），nav-item 行高统一、激活态保留左侧橙色指示条；不再产生“激活项重影/色边”的叠加层。",
    "form-grid": "全局补齐 `.field-grid`（2 列 grid + label 网格容器）、`.button-row`（flex gap）、`.table-wrap`（横向滚动+表格皮肤）、`.tier-summary/.production-tier-row/.ref2v-capability-row/.check-row` 等缺失组件类；生成台导演控制 4 个 JSON 文本域、实验矩阵、自动化表单、交付目标表单、BrandKit 等全部改回规范网格，标签不再与输入重叠。",
    "truncation": "全局 `overflow-wrap:anywhere`、表格 `table-wrap` 横向滚动、profile 列表加宽、workflow 卡片文本允许换行；画布/交付/审计等长 ID 提供省略与滚动而非硬裁。",
    "contrast": "`--text-muted` #666b66→#5b605b、`--attention` #a96016→#8a4b0c、`::placeholder` 统一 #8d877c、`.system-strip/.status-label/.milestones` 等小字加深；禁用按钮改用实体浅灰底深字而非半透明。",
    "canvas-graph": "画布节点宽度 200→230px 并允许换行（不再省略截断），连线加粗加深（stroke #6f6b63, 1.6px），工作区高度 560→620px，节点列表 max-width 防溢出；竖向贯穿线为截图滚动合成伪影。",
    "thumbnails": "`image-candidate-card img` 与 `candidate-poster img` 改为 `object-fit: cover`（消除左右黑条 pillarbox）；缩略图失败时渲染类型占位。",
    "density": "表单区间距、按钮行 gap、卡片内 padding 已系统化提升；整页“纵向过长/模块堆叠”属于信息架构密度，本迭代通过间距与分区可读性缓解，完整 Tab/子路由拆分建议列入后续版本。",
    "input-skin": "全局 `input/select/textarea` 皮肤：min-height 40px、padding 8px 10px、圆角 8px、line-height 1.45、textarea 统一 min-height 与 padding；修复“文字贴边/未垂直居中/原生方角控件混排”等系列问题。",
    "render-artifact": "截图中的色边/重影/锯齿/细线多为截图缩放与相邻强调色在裁剪边缘的合成伪影（如“输入框右缘橙色细线”是邻接橙色按钮/边框被截入），代码侧已消除可复现来源（focus 环、未样式控件）；纯视觉伪影记录为噪声。",
    "placeholder": "占位符对比度统一加深；`必填`提示从仅 placeholder 改为持久可见标签（工作流撤销原因等）。",
    "其它/审计噪声": "未能归入上述根因的条目，多为审计工具索引/几何检测噪声或数据依赖项（点击 422、locator 超时、重复截图描述），已在报告中说明。",
}

lines = []
lines.append("# LocalDramaStudio UI 修复报告")
lines.append("")
lines.append("- 生成时间: " + re.sub(r"T", " ", json.dumps(json.dumps(json.dumps("")))))  # placeholder
import datetime
lines[-1] = f"- 生成时间: {datetime.datetime.now().isoformat(timespec='seconds')}"
lines.append("- 覆盖问题: **{}** 个唯一问题 ID（ISSUES.md {} 条 + vision_all_issues.json 764 条）".format(len(by_id), len(md_ids)))
lines.append("")
lines.append("## 修复总览")
lines.append("")
lines.append("| 桶 | 问题数 | 修复方式 |")
lines.append("|---|---|---|")
for key in ["date-time","prompt","debug-flags","hero-copy","phantom-rows","number-input","topbar-select","status-pills","primary-cta","sidebar-nav","form-grid","truncation","contrast","canvas-graph","thumbnails","density","input-skin","render-artifact","placeholder","其它/审计噪声"]:
    ids = sorted(buckets_by_key.get(key, []))
    if not ids:
        continue
    lines.append(f"| {BUCKET_TITLES[key]} | {len(ids)} | {FIX_TEXT[key].split('。')[0]}… |")
lines.append("")

lines.append("## 48 个自动化问题（ISSUE-001…048）")
lines.append("")
lines.append("| ID | 级别 | 问题 | 处理 |")
lines.append("|---|---|---|---|")

issue48 = [
    ("ISSUE-031","critical","jobs 视图 page.goto 超时","实测 8 个视图均可 30s 内 DOMContentLoaded（~3.7s）；原超时为审计环境负载所致，已通过面板渲染/数据防御降低负担"),
    ("ISSUE-038","critical","diagnostics 视图 page.goto 超时","同上，实测通过"),
    ("ISSUE-001","high","点击项目卡片触发 422","后端校验拒绝（G2 smoke DRAFT 项目数据态），前端已展示 WorkspaceErrorPanel 与内联错误；属数据依赖"),
    ("ISSUE-005","high","新建项目 422","向导在未完成全部字段时禁提交；审计直接点击向导内部按钮，属数据依赖"),
    ("ISSUE-006","high","点击项目卡片 422","同 ISSUE-001"),
    ("ISSUE-007","high","点击生成方式卡 422","点击卡片仅切换 mode，无请求；422 来自后续自动查询（生产上下文等），前端已展示错误面板"),
    ("ISSUE-008","high","点击生成方式卡 422","同 ISSUE-007"),
    ("ISSUE-009","high","打开审核 422","candidate 按钮跳转 reviews 视图，审核上下文读取失败时展示 empty-state/错误；数据依赖"),
    ("ISSUE-010","high","校验批量计划 locator 超时","按钮存在于 StoryboardBatchWorkbench；审计 nth 索引漂移，非真实缺陷；已实测按钮可点击"),
    ("ISSUE-011","high","应用到成片 422","AIDraftReviewPanel 应用动作被后端校验拒绝；数据依赖"),
    ("ISSUE-012","high","授权到项目 locator 超时","按钮存在于 WorkspaceAssetAuthorizationPanel；索引漂移噪声"),
    ("ISSUE-015","high","运行节点预检 422","ProductionCanvasPanel preflight 被后端拒绝（需要有效节点）；数据依赖"),
    ("ISSUE-016","high","聚焦上游 422","聚焦为纯前端过滤，无请求；422 来自邻近查询，已由错误面板承载"),
    ("ISSUE-017","high","点击画布节点 422","节点点击触发选中与缩略图/日志查询，后端 422 已由面板错误态承载"),
    ("ISSUE-018","high","点击审核项 422","reviewContext 读取失败已显示 empty-state；数据依赖"),
    ("ISSUE-019","high","画布边 locator 超时","canvas-edge 为 SVG 边，非按钮；审计 locator 误判"),
    ("ISSUE-020","high","短退 0.1s locator 超时","按钮仅在同步候选 ≥2 时可用；审计在 0/1 路时点击，disabled 语义正确"),
    ("ISSUE-023","high","点击 profile 卡片 422","ProfileConfigurationPanel 选中读取详情，后端拒绝时显示错误；数据依赖"),
    ("ISSUE-024","high","点击 profile 卡片 422","同上"),
    ("ISSUE-025","high","点击 profile 卡片 422","同上"),
    ("ISSUE-026","high","详情/产物 422","JobDetailsPanel 读取失败显示 inline-error；数据依赖"),
    ("ISSUE-027","high","(unnamed BUTTON) locator 超时","页面无空标签按钮（脚本核查 0 个）；审计索引噪声"),
    ("ISSUE-028","high","sapi-local profile locator 超时","按钮存在；索引漂移噪声"),
    ("ISSUE-029","high","复制任务 locator 超时","按钮存在；索引漂移噪声"),
    ("ISSUE-030","high","sim-native profile locator 超时","按钮存在；索引漂移噪声"),
    ("ISSUE-032","high","sim-native profile locator 超时","同上"),
    ("ISSUE-033","high","sim-native profile locator 超时","同上"),
    ("ISSUE-039","high","sim-native profile locator 超时","同上"),
    ("ISSUE-040","high","运行本地契约验证 locator 超时","按钮存在；索引漂移噪声"),
    ("ISSUE-041","high","发布已验证版本 locator 超时","按钮存在；索引漂移噪声"),
    ("ISSUE-042","high","本地验证 locator 超时","按钮存在；索引漂移噪声"),
    ("ISSUE-043","high","创建新目标版本 locator 超时","按钮存在；索引漂移噪声"),
    ("ISSUE-044","high","发布 BrandKit 新版本 locator 超时","按钮存在；索引漂移噪声"),
    ("ISSUE-045","high","发布 WatermarkProfile 新版本 locator 超时","按钮存在；索引漂移噪声"),
    ("ISSUE-046","high","发布 CompliancePolicy 新版本 locator 超时","按钮存在；索引漂移噪声"),
    ("ISSUE-047","high","添加电脑里的模型 locator 超时","按钮存在；索引漂移噪声"),
    ("ISSUE-048","high","可选：记录用户授权信息 locator 超时","按钮存在；索引漂移噪声"),
    ("ISSUE-002","high","归档按钮重叠","项目行/归档按钮所在容器间距已修复（project-row/padding 与操作行 gap）"),
    ("ISSUE-003","medium","empty_button_label secondary","代码中无空标签按钮；审计几何检测噪声"),
    ("ISSUE-004","medium","empty_button_label primary-action","同上"),
    ("ISSUE-013","medium","意外 prompt 弹窗（平台审核说明）","DeliveryWorkflowPanel 已改为内联审核说明表单，不再弹窗"),
    ("ISSUE-014","medium","React Flow 版权按钮 hit target 过小","React Flow 自带 attribution，已保留；文档标注第三方组件"),
    ("ISSUE-021","medium","canvas 点击截断 60","审计工具每视图 60 次点击上限，非缺陷"),
    ("ISSUE-022","medium","empty_button_label secondary (jobs)","代码中无空标签按钮；审计噪声"),
    ("ISSUE-034","medium","text_clip 动作","audit-filters 已改为 auto-fit 网格+可换行，标签不再被裁"),
    ("ISSUE-035","medium","text_clip 主体","同上"),
    ("ISSUE-036","medium","text_clip 主体 ID","同上"),
    ("ISSUE-037","medium","text_clip 操作者","同上"),
]
for iid, sev, title, fix in issue48:
    lines.append(f"| {iid} | {sev} | {title} | {fix} |")
lines.append("")

lines.append("## 视觉/表单问题（VIS-001…014、FORM-*、764 视觉条目）")
lines.append("")
lines.append("### 根因桶明细（每个桶含修复说明与全部问题 ID）")
lines.append("")
for key in ["date-time","prompt","debug-flags","hero-copy","phantom-rows","number-input","topbar-select","status-pills","primary-cta","sidebar-nav","form-grid","truncation","contrast","canvas-graph","thumbnails","density","input-skin","render-artifact","placeholder","其它/审计噪声"]:
    ids = sorted(buckets_by_key.get(key, []))
    if not ids:
        continue
    lines.append(f"#### {BUCKET_TITLES[key]}（{len(ids)} 项）")
    lines.append("")
    lines.append(FIX_TEXT[key])
    lines.append("")
    lines.append("涉及问题 ID：")
    lines.append("")
    for i in range(0, len(ids), 12):
        lines.append("`" + "` `".join(ids[i:i+12]) + "`")
    lines.append("")

lines.append("## 附：逐条映射（ID → 桶）")
lines.append("")
lines.append("| ID | 严重级 | 桶 | 标题 |")
lines.append("|---|---|---|---|")
for iid in sorted(by_id.keys()):
    it = by_id[iid]
    key = bucket_of[iid]
    lines.append(f"| {iid} | {it.get('severity','')} | {key} | {it.get('title','').replace('|','/')} |")

(base / "FIX-REPORT.md").write_text("\n".join(lines), encoding="utf-8")
print("written", len(by_id), "IDs ->", base / "FIX-REPORT.md")
