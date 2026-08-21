# ADR-0101：Director Desk 是单镜生产主界面

- 状态：Accepted
- 日期：2026-08-20
- 对应：xinjihua 02 §80 ADR-001；03 P5～P8

## Context

旧界面把镜头意图、生成、候选、审核和连续性拆散在多个全局 Panel。事实仍以 shot revision、variant、selection、frame anchor 和 job 为准，但用户需要记忆技术模块，且容易脱离当前镜头操作。

## Decision

以 `/projects/:projectId/episodes/:episodeId/direct/:shotId?` 的 Director Desk 作为单镜精修主界面。页面由项目/分集上下文、镜头导航、媒体舞台、Inspector、Takes/Filmstrip 五区组成；后端提供有界 aggregate read model。写操作继续调用既有 revision、generation、selection、review、frame bridge 命令，不在 Director Desk 建第二套事实。

## Rejected alternatives

- 继续扩展全局 generation/review Panel：拒绝，因为当前镜头上下文和跨事实阻塞不清晰。
- 为 Director Desk 建专用 candidate/selection 表：拒绝，因为会制造双重权威。
- 一次删除旧 UI：拒绝，因为 P12 要求新路径完成 UAT 后才清理。

## Consequences

单镜问题可就地处理，深链接与刷新恢复明确；aggregate 必须避免 N+1，并排除 archived split parent。旧 Panel 暂时保留兼容，新增能力优先进入 Director Desk 或稳定 facade。

## 事实来源

`application/director_desk.py`、`api/routes/director_desk.py`、`pages/DirectorDeskPage.tsx`、`features/director-v2/`、`test_director_desk.py`。
