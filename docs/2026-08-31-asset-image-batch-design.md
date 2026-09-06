# 资产页面批量文生图设计与实现

## 目标

让创作者在“资产”页的角色、场景、道具、服装分类中，选择一个已发布的文生图配置，一次补齐当前分类所有缺失的主参考图。每张图片必须可独立执行、失败、重试和审计；成功结果自动进入资产圣经，但不得覆盖生成期间由用户选定的新主参考。

## 页面边界

### 实现批量主图生成

- 项目 → 资产 → 角色：`IMAGE_CHARACTER`
- 项目 → 资产 → 场景：`IMAGE_SCENE`
- 项目 → 资产 → 道具：`IMAGE_CONCEPT`
- 项目 → 资产 → 服装：`IMAGE_CONCEPT`

四类资产共享一个批量工作台和一份后端协议。类别差异集中在后端 `ASSET_IMAGE_SPECS` 中，后续新增生物、载具、建筑等类别时不需要复制页面或队列实现。

### 不重复增加按钮

- 故事草案 / 资产建议审核：候选身份尚未被人工确认，不能消耗 GPU 或写入正式媒体。审核完成后进入资产页生成。
- 镜头工作台：已有独立的分镜批量生成，输入、输出和审核门不同，不与资产主图批次合并。
- 快速生成：结果是项目外独立媒体，不应静默变成正式资产。
- 项目首页：只适合展示缺图数量和跳转，不作为第二个写入口。

## 用户流程

1. 切换角色 / 场景 / 道具 / 服装分类。
2. 页面统计当前分类缺少 `HERO` 的启用资产。
3. 打开“配置并生成”区域，选择自动项目偏好或固定的已发布文生图版本。
4. 默认全选缺图项，也可逐项取消。
5. 执行只读预检：冻结资产 revision、主图存在状态、Profile revision、提示词与计划 hash。
6. 确认提交；每个资产创建独立 `GenerationIntent → GenerationVariant → Job`。
7. 页面轮询批次投影，逐项显示排队、运行、成功、失败和被新参考取代。
8. Worker 成功后把图片 artifact 提升为项目 `MediaVersion`；如果仍无主参考，则自动创建锁定的 `HERO` reference 并同步 canonical 投影。
9. 失败项可一键重新选中并建立新批次，历史批次与失败证据保留。

## 架构

批次表只保存跨资产编排事实，不创建新的执行队列：

```text
AssetImageGenerationBatch
  ├─ BatchItem(asset A) ─ GenerationIntent ─ Variant ─ Job ─ Artifact ─ MediaVersion ─ HERO Reference
  ├─ BatchItem(asset B) ─ GenerationIntent ─ Variant ─ Job ─ Artifact ─ MediaVersion ─ HERO Reference
  └─ BatchItem(asset C) ─ GenerationIntent ─ Variant ─ Job ─ failure (独立可见、可重试)
```

关键约束：

- 单批最多 100 项，资产 ID 去重。
- 第一版只允许 `MISSING_ONLY`，不提供无审核的批量覆盖。
- Profile 必须 Published、能力精确匹配并绑定 Published Workflow。
- 预检不联系运行时、不写数据库。
- 提交使用 plan hash 防止资产或模型配置变更，并使用项目级幂等键防止重复点击。
- 每个 Job 使用稳定的按资产 seed，生成可复现。
- 媒体提升和 `HERO` 绑定幂等；用户的新 `HERO` 优先于后台旧计划。
- 部分失败不会回滚已成功图片，也不会把一个批次伪装成全成功。

## 扩展点

- 新资产类别：在 `ASSET_IMAGE_SPECS` 和前端 `KIND_META` 增加类别到 capability / prompt policy 的映射。
- 多候选评审：在 batch item 下增加候选集合，保持 `HERO` 写入仍通过同一命令服务。
- 重新生成既有主图：新增独立 `REVIEW_REPLACEMENT` 模式，输出先进入候选评审，人工确认后才切换 `HERO`。
- 状态图生成：以 `asset_state_id` 扩展 item authority，不改变批次和 Job 链。
- 项目首页提醒：读取资产圣经缺图统计，仅提供深链到对应分类。

## 验证范围

- 迁移图与发布迁移合同包含 `0091_asset_image_generation_batches`。
- 后端覆盖类别提示词、已有主图跳过、无 Profile 阻塞、只读预检、独立 Job、幂等重放、artifact 提升与自动 HERO 绑定。
- 前端覆盖缺图筛选、显式 Profile 选择、预检和确认提交。
- 资产页原有角色多视图、表情和细节生成保持不变；它们属于有主参考后的图像编辑阶段。

## 真机页面与生成验收

2026-08-31 在真实 Vite、FastAPI、Worker、ComfyUI 与 Qwen Image 2512 Q5_K_M 环境完成页面操作：

1. 打开项目资产页并切换到“道具”。
2. 点击“配置并生成”，保留“照骨古剑”一项。
3. 页面执行预检并显示 `1 张可生成`，随后点击确认生成。
4. 批次 `304d8c2d-4218-4b6d-8016-c3365d96c78c` 创建真实 Job `cd12ace5-fa27-40c6-8a98-252fc9863304`。
5. ComfyUI prompt `a21646b1-28e9-4a03-86e3-52c2310623b9` 完成 20 步采样。
6. 生成 PNG 被提升为 MediaVersion `164baa58-2d22-4939-8466-1f6e7a70f97a`，并创建锁定 HERO reference `4d390e8c-8527-4725-8b7e-be61f3067b51`。
7. 标准缩略图派生完成，资产列表、资产详情和媒体选择器均可显示真实生成图。

真机验收同时发现并修复两项仅靠组件测试无法发现的问题：

- Published Profile 的 `input_slots` 同时包含标量工作流槽和媒体槽；GenerationVariant 现在只对媒体槽执行 MediaVersion cardinality 校验，`PROMPT/SEED/OUTPUT_PREFIX` 等继续由 parameter set 注入。
- 自动提升的图片过去没有排入标准缩略图派生；完成服务现在在发布 HERO reference 前提交幂等的默认 derivative Job，保持 GET 接口只读并确保页面最终可见。

证据保存在 `output/playwright/.playwright-cli/`。最终稳定服务会话在 1440×1100 视口下无浏览器 console error 或 warning。
