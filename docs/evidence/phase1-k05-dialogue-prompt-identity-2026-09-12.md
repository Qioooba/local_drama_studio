# Phase 1 / K05 对白提示词身份验收（2026-09-12）

## 结论

K05 已完成。镜头生成提示词以 `dialogue_lines` 的稳定对白身份及其最新
`dialogue_text_revisions` 为权威来源，同时保留说话人和逐字文本；镜头修订中残留的
旧对白不再进入新的视频或关键帧计划。缺少可确认说话人的权威对白会以
`DIALOGUE_SPEAKER_CONFIRMATION_REQUIRED` 阻塞，不猜测角色。

## 实施范围

- `domain/shot_prompt.py`：结构化对白编译为 `speaker：text`，不重复已有前缀；保留引号、
  冒号和换行；空对白不生成文字。
- `application/dialogue_facts.py`：读取镜头当前对白稳定 line ID 与最新不可变 text revision。
- `application/episode_worker_actions.py`：视频预检及提交共享权威对白；未确认说话人闭锁。
- `application/shot_keyframe_generation.py`：关键帧计划使用同一对白事实和同一提示词编译器。
- 历史 Prompt/Variant 未修改；角色—音色绑定表和 TTS 选择命令未改动。

## 验收证据

执行：

```text
python -m pytest -q \
  apps/api/tests/test_dialogue_prompt_facts.py \
  apps/api/tests/test_shot_prompt.py \
  apps/api/tests/test_episode_worker_actions.py \
  apps/api/tests/test_shot_keyframe_generation.py \
  apps/api/tests/test_shot_dialogue_v2.py
```

结果：31 passed。覆盖 A/B 轮流、同一显示名下不同稳定 line ID、旁白、无对白、已有前缀、
冒号/引号/换行、最新文本 revision、陈旧镜头字段排除、视频/关键帧入口一致和待确认闭锁。

执行：`python -m pytest -q apps/api/tests/test_character_voice_batch.py`

结果：5 passed。角色资产到音色版本的独立绑定及逐说话人解析未回归。

执行 Ruff（所有 K05 改动文件）与 `git diff --check`：通过。换行提示为现有 Windows
工作区的 LF/CRLF 转换提醒，不是空白错误。

## 未扩大范围

没有新增第二份对白存储、没有改写历史提示词、没有自动润色台词、没有引入模型专属
九分节语法，也没有改变声音路线。
