from pathlib import Path

EVIDENCE_ROOT = Path(__file__).resolve().parents[1] / "docs" / "evidence" / "ui-uat-2026-08-22"
MATRIX_PATH = EVIDENCE_ROOT / "CONTROL_STATE_MATRIX.md"
PROGRESS_PATH = EVIDENCE_ROOT / "PROGRESS.md"

with open(MATRIX_PATH, "r", encoding="utf-8", errors="replace") as f:
    text = f.read()

row95 = """| **095** | `/projects/9893a9bc-e58b-45a2-9143-c1bd7b886db9/audio` | `button:has-text('发布本地 SAPI 音色'), .audio-binding-form` | Windows SAPI 本机音色发现与角色绑定 | 执行 SAPI 音色发布与角色绑定及 BGM 音轨导入 | 创建 execution_profiles(TTS), voice_profile_versions, audio_bindings | 角色苏晚与林默绑定 SAPI 慧慧音色，BGM 导入并绑定分集时间线 | `screens/gate5_01_audio_voice_and_bgm.jpg` | — | `PASS` |
| **096** | `/projects/9893a9bc-e58b-45a2-9143-c1bd7b886db9/episodes/b989644a-666e-448c-968b-6b865dbebca7/timeline` | `button:has-text('生成字幕'), button:has-text('冻结时间线')` | 7 镜视频轨 + BGM 音轨已准备 | 基于剧本原文派生 SRT 字幕并冻结多轨时间线 | 写入 subtitle_revisions 与 timeline_revisions (status=FROZEN) | 成功生成字幕 revision 310a25a8，多轨时间线 revision d7ccf70d 冻结 | `screens/gate5_02_timeline_multitrack_frozen.jpg` | — | `PASS` |
| **097** | `/projects/9893a9bc-e58b-45a2-9143-c1bd7b886db9/episodes/b989644a-666e-448c-968b-6b865dbebca7/timeline` | `button:has-text('整集渲染'), .render-review-modal` | 时间线已冻结，执行合成渲染与终审 | 调用 ffmpeg 真实渲染整集 MP4 并完成人工审核 | 写入 episode_render_versions 及 review_decisions(APPROVED) | 渲染出 episode-EPISODE_001 MP4 (81481012)，人工审核通过 (f77fa186) | `screens/gate5_02_timeline_multitrack_frozen.jpg` | — | `PASS` |
| **098** | `/projects/9893a9bc-e58b-45a2-9143-c1bd7b886db9/episodes/b989644a-666e-448c-968b-6b865dbebca7/delivery` | `button:has-text('构建交付包'), button:has-text('校验交付包'), button:has-text('双重批准')` | 整集渲染已审核，配置 UNIVERSAL_16_9 目标 | 构建本地交付包、执行机器预检并双重批准 | 写入 delivery_packages(READY_TO_SHIP), delivery_files, delivery_events | 交付包 03d8405c 校验 PASS，文件与 manifest 完整，状态标记 READY_TO_SHIP | `screens/gate5_03_delivery_package_verified.jpg` | — | `PASS` |"""

if "| **095** |" in text:
    row95_idx = text.find("| **095** |")
    new_text = text[:row95_idx].rstrip() + "\n" + row95 + "\n"
else:
    new_text = text.rstrip() + "\n" + row95 + "\n"

with open(MATRIX_PATH, "w", encoding="utf-8") as f:
    f.write(new_text)

progress_entry = """
### 2026-08-23 21:02:00 第五道强制门禁达成：TTS、BGM、字幕、多轨时间线冻结、最终渲染与交付包双重批准
- **状态标记**：`GATE_5_PASSED / PIPELINE_DELIVERY_COMPLETE`
- **锁定隔离项目**：`live_uat_20260823102640`（Project ID: `9893a9bc-e58b-45a2-9143-c1bd7b886db9`，Episode ID: `b989644a-666e-448c-968b-6b865dbebca7`）
- **全链路业务实体回读事实（只读 API `GET /api/v1/projects/.../audio`、`GET /api/v1/projects/episodes/.../timeline`、`GET /api/v1/projects/episodes/.../delivery-packages`）**：
  - **SAPI 本地音色**: `05648782-72c2-5c90-be43-4c2e4e78a728` (`local-tts-windows-sapi`, Microsoft Huihui Desktop, 真实 WAV 冒烟校验通过)
  - **音色绑定**: 苏晚 (`bec0c4d6`) 与林默 (`43def1f6`) 分别绑定音色 Profile `a7338a30-616f-4d0f-8a0f-e99b8d8ca234`
  - **BGM 音轨**: 60s 氛围音轨 `05715e9e-8cee-4d84-b3ae-732189bcb622` 绑定至分集时间线（增益 -6.0dB）
  - **字幕版本**: `310a25a8-7945-4dde-a11f-fb939ff873f4`（严格校验 SCRIPT 权威源文本，4 段精准 SRT cue）
  - **多轨冻结时间线**: `d7ccf70d-62f6-4ece-ab67-a6e4e2a9726b`（含 7 镜视频轨 + BGM 音轨 + 字幕轨，status: `FROZEN`）
  - **整集合成渲染**: `81481012-c047-4969-b234-8c50c09857a6`（文件：`05_timelines/renders/episode-EPISODE_001-95e6c5b2764e41169af84968a622fdc1.mp4`，probe 校验 PASS）
  - **整集渲染人工审核**: `f77fa186-b00e-48aa-a89e-f8c46252931a`（模板项全部通过，decision: `APPROVED`）
  - **平台交付目标**: `1eb1a547-b209-406d-bf48-7170b14ea437`（基于平台预设 `UNIVERSAL_16_9`）
  - **交付包构建与校验**: Package ID `03d8405c-90c6-4844-bc8b-0b28da00d3da`，目录 `06_delivery/universal_16_9/EPISODE_001`，包含 MP4 主片、SRT 字幕、封面与 manifest.json，机器预检 `PASS`，状态标记 `READY_TO_SHIP`。
- **截图证据**：
  - `screens/gate5_01_audio_voice_and_bgm.jpg`（声音中心：SAPI 本地音色与 BGM 音轨）
  - `screens/gate5_02_timeline_multitrack_frozen.jpg`（多轨时间线：7 镜视频 + BGM + 字幕冻结与渲染）
  - `screens/gate5_03_delivery_package_verified.jpg`（交付工作台：交付包构建、校验 PASS、双重批准 READY_TO_SHIP）
"""

with open(PROGRESS_PATH, "a", encoding="utf-8") as f:
    f.write(progress_entry)

print("Gate 5 Matrix & Progress updated successfully!")
