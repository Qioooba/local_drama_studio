# ADR-0107：Frame Bridge 映射既有 anchors 与 transition constraints

- 状态：Accepted
- 日期：2026-08-20
- 对应：xinjihua 02 §80 ADR-007；03 P8

## Context

首尾帧和镜间连续性已经分别存在于 frame anchors、variant input bindings、selection/approval 和 shot transition constraints。新增导演 UI 需要 previous end/current start/current end/next start 视图，但不能建立 `frame_bridges` 第二事实表。

## Decision

Frame Bridge 是 aggregate/read model 与命令 facade。inherit、choose、extract、lock/unlock 继续写既有 anchor/constraint/linkage；生成输入用 FIRST_FRAME/LAST_FRAME role 绑定 exact media version。来源 selection/approval 改变时投影为 stale，重新继承创建新 linkage，历史锚点保留。

## Rejected alternatives

- 新建 frame_bridge 状态表：拒绝，因为会与 anchors/constraints 失同步。
- selection 改变后自动破坏性替换：拒绝，因为历史生成输入和人工意图会丢失。
- generic I2V 代替 first/last capability：拒绝，因为执行契约不同。

## Consequences

用户获得统一连续性界面且历史仍可追溯；read model 必须显式报告 compatibility/stale。任何自动刷新只能派生新事实，不能改写旧 variant binding。

## 事实来源

`application/frame_bridges.py`、`features/director-v2/FrameBridgeControls.tsx`、`test_frame_bridge_source_frame.py`、`test_continuity_context.py`、migrations 0009/0010/0012。
