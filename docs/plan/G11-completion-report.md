# G11 增强批次完成报告（2026-08-17）

> 依据 `docs/plan/gap-closure-development-plan.md` 的调研结论与开发计划，G11 增强批次全部交付。本报告为最终归档：交付物、门禁、证据、边界与后续建议。

## 1. 范围回顾

调研（2026-08-17 独立检索商业平台与开源社区后确定）确认的差距共 14 项，其中 12 项纳入开发（P0 五项、P1 七项），2 项明确排除（云协作、多供应商云引擎等与 LOCAL_ONLY 冲突）。另交付 P2 四项设计文档（本批只设计不实施）。

## 2. 交付物清单

### P0 短剧完整闭环（提交 `82e23e5`）
| 编号 | 功能 | 关键实现 |
|---|---|---|
| P0-1/2 | 故事资产库 | `story_assets`/`shot_asset_bindings`（迁移 0040）；四类资产卡 CRUD+乐观锁+归档+canonical 参考图+镜头绑定；StoryAssetLibraryPanel / DirectorShotEditor 绑定区 / ContinuityPanel 绑定资产区 |
| P0-1 | 角色锚点提示词注入 | `prompt_anchors.py`；submit 时追加进执行 PROMPT、job 快照冻结 anchor+sha256、独立审计；预览端点与执行锚点逐字节一致；EXACT_REPLAY/plan_hash 语义保持 |
| P0-3 | 拆解草稿落地应用 | `breakdown_apply.py` 单事务落地（场/镜头/对白）；幂等状态翻转；AIDraftReviewPanel 应用到成片 |
| P0-5 | 多角色 TTS 编排 | `character_voice_bindings`（迁移 0041）；`submit_episode_tts_batch` 批量（镜头绑定优先、speaker 兜底、逐行隔离）；DialogueTTSPanel 编排区 |
| P0-4 | 整剧一键编排 | WHOLE_DRAMA 模板 + `AUTOMATION_WORKFLOW_TASK` 执行器（KEYFRAME_CHECK/TTS_BATCH/RENDER/DELIVERY/SUBTITLE）+ BATCH_AUTOMATED 启动预置 + 终态上下文持久化 + 面板 3s 轮询 |

### P1 显著增强（提交 `d34f217`）
| 编号 | 功能 | 关键实现 |
|---|---|---|
| P1-6 | 交付规格预设库 | 7 个不可变平台预设；from-preset 创建复用既有校验/审计；ReadinessPanels 预设区 |
| P1-7 | 生产档位 | 5 档（FAST..MASTER，17k+5 帧数网格 107/107/124/175/209）；build_t2va/fl2va tier 覆盖；档位下拉+参数摘要；tier 冻结元数据 |
| P1-8 | Ref2V 能力位 | 原生 `MiniMaxH3ReferenceToVideo` 链（真实 0.31 节点核实）；能力门禁+端点+UI 置灰；TRUSTED_COMFY_BUILTINS 扩展 |
| P1-9 | 长镜头分段 | `plan_segments` 纯函数规划 + `render_segmented_episode` 真实 ffmpeg 拼接登记 |
| P1-10 | 剪映草稿导出 | `draft_content.json` best-effort 包 + 媒体随包复制；format 参数向后兼容；面板按钮 |
| P1-11 | 音效/BGM 轨 | track_type 约束+旧值兼容；三步 ffmpeg 混音（无绑定零改动）；AudioTrackPanel BGM |
| P1-12 | 字幕样式模板 | creative_entries 承载项目模板（不建表）；ASS [V4+ Styles] 块；cue.style_json 持久化；面板样式编辑器 |

### P2 设计文档（提交 `d34f217`）
`docs/plan/timeline-editor-design.md`：P2-A 可视化剪辑器（四阶段：只读轨道/拖拽/局部重剪/转场预览，复用现有数据模型不建表）、P2-B 封面合成、P2-C 敏感内容预检、P2-D 多模态画布——各含设计与验收标准。

## 3. 门禁与验证

| 门禁 | 结果 |
|---|---|
| API 单测 | **382 passed**（基线 290 + 92 新增） |
| Web 测试 | **39 文件 / 126 tests** |
| `pnpm check` | Ruff / mypy / build / G5 全绿 |
| e2e（真实数据+页面点击） | **7 个 spec 全 PASS**：story-asset、breakdown-apply、character-voice（真实 SAPI 合成）、whole-drama（worker 驱动两轮 HITL）、delivery-presets、jianying-export、subtitle-styles |
| 迁移 | 正式库 0039 → 0041（0040/0041 纯增量表，preflight 备份在 `backups/`）；`release_rehearsal.py`/`verify_release.py`/`release_audit.py` 链头同步 |
| `release_audit` | PASS / GO（exit_decision 不变） |
| 登记 | `docs/requirements-traceability.md` 第七轮（P0）+ 第八轮（P1+P2） |

证据文件：`docs/evidence/g10/{story-asset,breakdown-apply,character-voice,whole-drama,delivery-presets,jianying-export,subtitle-styles}-windows-uat-2026-08-17.json`（7 份，均为隔离快照、零公网、零生产库写入）。

## 4. 边界与留待事项

1. **Ref2V 真机跑通**：能力位/编译链已交付并经真实节点核实；未在 GPU 上真实生成一条 ref2v（受控真机验证属后续事项，出证据即可闭环）。
2. **档位落地实际生成**：tier 冻结为元数据，profiles/execution 层接入 `resolve_tier` 后档位才影响真实生成参数。
3. **剪映草稿真机导入**：best-effort 逆向格式，需真机验证"可被剪映打开"后按实测微调字段。
4. **使用期人工事项**（非 agent 范围）：正式项目真实授权音色/素材数据链、交付包最终签字。
5. **排除项保持排除**：云协作、多供应商云引擎、市场分析/选题洞察、数字人/口型、外部音色商城、正版素材商城、音乐生成模型、3D 导演台、多租户、G11 legacy 迁移。

## 5. 后续建议（优先级）

1. Ref2V 真机证据（GPU 在线时约 1 小时）
2. 档位落地实际生成（纯代码，约半天）
3. 剪映真机导入验证（需本机剪映）
4. P2-A 剪辑器阶段一（设计已就绪，约 1 迭代）
5. P2-B 封面合成（独立小项，可并行）
