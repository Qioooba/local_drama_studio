# LocalDramaStudio 总体 Go / No-Go（DRAFT）

release_status: DRAFT

当前决策：**NO-GO / IN PROGRESS — 总需求闭环尚未完成**。

## 发布范围

- 平台不捆绑、不上传、不分发用户选择的模型、音色或媒体。
- 用户在页面选择电脑中的模型绝对路径；平台仅记录路径、hash、格式、量化和兼容性。
- 缺少用户素材许可证记录会显示风险提示，但不阻塞平台本身交付；平台不会伪造授权结论。
- 正式支持 LOCAL_ONLY、Windows x64、本地源码安装，不包含 G11 或远程 Provider。

G7、G8、G9、数据库、迁移、备份、升级/恢复、SBOM、规模和安全等局部门禁已有通过证据，但它们不能替代总设计要求闭环。正式发布还必须逐项验证 84 个 P0/P1 FR、15 个 P0/P1 NFR 和 85 个命名 TC，并完成完整本地一条龙 UAT；当前机器账本明确为 `IN_PROGRESS`。

## 通过条件

只有 `MASTER_REQUIREMENTS_CLOSURE`、其余发布门禁和发布工件同时 PASS/FINAL 后才能改为 GO。若未来把第三方模型或素材装入安装包、启用 REMOTE transport 或扩展到 G11，仍必须重新执行许可证、安全和发布评审。
