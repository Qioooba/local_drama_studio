# LocalDramaStudio 总体 Go / No-Go（FINAL）

release_status: FINAL

当前决策：**GO — Windows x64 LOCAL_ONLY 本地源码发行版放行**。
（本文件状态已冻结；该决策由产品负责人 2026-08-17 指示放行，覆盖此前 2026-08-14 冻结的阶段性 NO-GO。上一版 NO-GO 保留为历史记录，见本文件下方“历史决策”一节。）

## 发布范围

- 平台不捆绑、不上传、不分发用户选择的模型、音色或媒体；用户素材许可证缺失显示风险提示但不阻塞平台本身交付。
- 正式支持 LOCAL_ONLY、Windows x64、本地源码安装；不包含 G11 legacy 迁移或远程 Provider（REMOTE transport 保持硬禁用）。
- 发布件不包含任何用户模型权重、音色或媒体文件；`LOCAL_ONLY` 运行期零公网出站。

## 放行依据（2026-08-17 复核）

- `scripts/master_requirements_audit.py`：`status=PASS`，`release_fr=84/84`、`nfr=15/15`、`tc=85/85`，`missing` 为空，`problems=[]`。
- `scripts/release_audit.py`：`status=PASS`；`DATABASE_INTEGRITY`、`MIGRATION_HEAD=0039_automation_task_jobs`、`BACKUP_INTEGRITY`、`ORDERED_G7/G8/G9`、`UPGRADE_ROLLBACK_REHEARSAL`、`SBOM_INVENTORY`、`LOCAL_UAT_READONLY_BASELINE`、`METADATA_SCALE_UAT`、`SECURITY_UAT`、`CLEAN_ROOT_RECOVERY_UAT`、`STALE_JOB_MAINTENANCE`、`MASTER_REQUIREMENTS_CLOSURE`、`RELEASE_ARTIFACTS` 全部 PASS/FINAL。
- 完整门禁：API `288 passed / 4 Comfy live deselected`；Web `36 files / 100 tests`；Ruff、mypy、maintainability、production build 全绿。
- 正式三视口只读生产快照 UAT：core-chain 六视图（projects/generation/reviews/jobs/diagnostics + timeline/delivery）在 1440×900、1280×800、1024×768 三档 PASS，零写入/零公网/零原媒体/零错误/零溢出；证据 `docs/evidence/g10/core-chain-browser-readonly-uat-2026-08-16.json`。

## 放行边界（保持现状，不伪造证据）

- H3/Comfy 平台 Job：产品负责人自行运行 ComfyUI；`docs/evidence/g10/h3-comfy-job-uat-2026-08-16.json` 保持 `BLOCKED`（RH `load_h3_model` WindowsAccessViolation，外部运行时问题），平台 Job→Artifact 的真实闭环在 runtime 恢复后另行推进，不阻塞本发行版。
- FR-AUD-001/002 正式数据链、FR-IMG-002 真实用户 Profile 批量生成、交付包最终签字：需要真实生产内容/授权/Profile 数据，属于使用期闭环事项，不阻塞源码发行。

## 历史决策

- 2026-08-14（FINAL，被本决策覆盖）：NO-GO / IN PROGRESS — 总需求闭环尚未完成；当时映射层计数为 24/84 FR、2/15 NFR、10/85 TC。
- 2026-08-15（DRAFT）：总设计复核纠正后总体状态一度为 `IN_PROGRESS / NO-GO`。
- 2026-08-16：映射层与发布审计先后转 PASS，本文件更新为 FINAL 但保留 NO-GO 作为阶段冻结。

## 后续触发重新评审的条件

若未来把第三方模型或素材装入安装包、启用 REMOTE transport、扩展到 G11，或生产数据链（真实 TTS Profile、真实用户模型批量生成、正式交付包签字）需要纳入发行验收，则必须重新执行许可证、安全和发布评审并更新本决策。

## G11 增强批次附注（2026-08-17）

G11（增强批次，非 legacy 迁移）已按 `docs/plan/gap-closure-development-plan.md` 实施完毕并归档（`docs/plan/G11-completion-report.md`）。本附注确认：

- **发行边界不变**：仍为 Windows x64 LOCAL_ONLY 源码发行；不捆绑用户模型/音色/媒体；REMOTE transport 保持禁用；多租户/云 Provider/G11 legacy 迁移仍不实施。
- **链头更新**：正式库迁移至 `0041_character_voice_bindings`（0040/0041 为纯增量表；preflight 备份在 `backups/`）；`release_audit` 保持 PASS/GO，exit_decision 不变。
- **交付范围**：P0 五项（故事资产库/拆解草稿应用/多角色 TTS/整剧一键编排/锚点注入）+ P1 七项（交付预设/生产档位/Ref2V 能力位/长镜头分段/剪映导出/BGM 轨/字幕样式模板）+ P2 四项设计文档；API 382 passed、Web 126 tests、7 个 e2e PASS。
- **留待事项不阻塞发行**：Ref2V 真机跑通、档位落地实际生成、剪映真机导入验证、使用期人工数据链（真实音色/素材/交付签字）——均不改变本发行验收结论。
- **重新评审触发条件不变**：若未来装入第三方模型/素材、启用 REMOTE、或把使用期数据链纳入发行验收，仍须重新执行许可证、安全和发布评审。
