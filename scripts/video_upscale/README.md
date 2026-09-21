# NCNN 视频超分本机验证

`smoke.py` 对用户明确提供的 Real-ESRGAN NCNN/Vulkan 程序和模型做真实两帧推理，校验输出数量与原生倍率，并生成带程序/模型哈希的脱敏 JSON 证据。脚本不联网、不下载、不写项目目录，也不会把 Profile 自动标成已发布。

```powershell
& ./.venv/Scripts/python.exe scripts/video_upscale/smoke.py `
  --executable "D:\Tools\realesrgan-ncnn-vulkan.exe" `
  --model-dir "D:\Models\realesrgan-ncnn-vulkan\models" `
  --model-name realesr-animevideov3 `
  --scale 2 --gpu-device 0 `
  --output "work\reports\ncnn-video-upscale-smoke.json"
```

输出 `PASS` 只证明这组本机程序、权重、Vulkan 设备和参数完成了独立 smoke，不会直接发布。正式配置请进入“系统 / 能力与模型 / 视频超分引擎”，使用“验证并发布超分 Profile”；该入口会自行逐倍率运行真实 smoke，并在全部通过后原子化地推进 Runtime/Model/Offering/Profile 生命周期。未完成真实推理不得手写 PASS。

## 运行与恢复语义

- 页面选定的 Tile 会冻结到批次。首块只有在 NCNN 明确报告显存分配失败时才按页面显示的有限递减序列回退；模型损坏、参数错误等失败不会伪装成 OOM。首块选定的实际 Tile 会由后续块与恢复 attempt 继续使用。
- SDR 源的色彩矩阵、范围、primaries 和 transfer 会进入冻结计划；缺失字段会在预检中显示推断警告并要求确认。PNG 推理输出编码时实际转换为 BT.709 limited，QC 同时检查色彩四元组、SAR 1:1、rotation 0 和 progressive，不能仅靠修改 metadata 冒充转换。
- 每块开始和最终发布前都会重新检查 work/project 所在卷。磁盘低于安全阈值或 Tile 回退耗尽时任务进入“需要处理”，不会后台反复重试；释放空间或调整并重新预检后，在队列中点“重试失败项”。
- “暂停全部”会终止当前任务拥有的完整包装器进程树。立即点恢复只记录恢复意图，必须等旧 attempt 退出并释放 lease 后才会产生新 attempt；不要手工结束其他 Comfy/Ollama/FFmpeg 进程。
- 已完成块按 source/snapshot/hash 校验后复用。最终 MP4 rename 后会先持久化发布回执；若 DB 登记前崩溃，恢复会重做 source/output hash、probe、帧数和 QC 后唯一登记，不调用模型、不覆盖 MP4。若成片已经登记而 Job 在完成前崩溃，恢复只校验既有输出并补 Job 回执，不会创建重复成片。
- 正式交付同样使用稳定 Job 身份恢复：失败项可在“版本与交付”中单独重试；已经登记的交付包不会重复构建，已经原子发布但尚未登记的本操作目录会按所有权标记安全恢复，既有历史交付包不会覆盖。
- 交付包源中已经烧录的字幕/水印会作为 `applied_effects` 随派生成片传递；相同效果打包时复用像素、不重复烧录，要求去除或替换时会阻塞并提示从干净 COMPOSE 重新超分。
- 横竖混合项目会按每集实际 1080p 像素匹配对应交付目标。历史目标只有在该集已显式采用、且宽高完全一致时才允许由超分批次复用；普通交付入口仍要求当前 ACTIVE 目标。
- 样片对比可进入“100% 像素裁切”，使用键盘调节两侧同步横纵区域。界面明确区分原文件、5 秒样片和正式交付成片。
- “版本与交付”的批量审核按分集分别展示 `episode_upscale` 检查表，不提供一次性全勾。先冻结计划，再原子批准；任一集漏项、版本/合成根变化、机器 QC 失效、模板更新或文件 hash 变化都会让整批零写入。通过审核后仍须执行独立的批量采用，审核不会自动替换交付版本。
- 低空间时进入项目“整剧交付 → 超分队列”，先点“预览过期中间文件”，检查默认 7 天保留期的项目数和字节数，再显式确认。清理会再次核对计划哈希、活动 attempt/lease 和路径；symlink/junction 会跳过，并且源片、正式成片、交付包、运行回执都不在清理范围。

## 两阶段 Windows/GPU 验收

`windows_uat.py` 只在一个**全新、绝对路径、从未存在过**的隔离目录中建库和建项目，不连接生产数据库，也不删除或复用目录。`prepare` 会真实发布 Profile、生成一横一竖两个带音频的 2 秒低清 fixture，执行样片、幂等重放、暂停待处理项、两集整批超分，并停在人工检查门前。配置文件示例：

```json
{
  "schema_version": "localdrama.video-upscale-windows-uat.v1",
  "confirm_isolated_test": true,
  "isolation_root": "D:\\LocalDrama-UAT\\video-upscale-run-20260921-001",
  "executable": "D:\\Tools\\realesrgan-ncnn-vulkan.exe",
  "model_dir": "D:\\Models\\realesrgan-ncnn-vulkan\\models",
  "model_name": "realesr-animevideov3",
  "gpu_device": 0,
  "tile_size": 0,
  "load_threads": 1,
  "proc_threads": 1,
  "save_threads": 2,
  "ffmpeg": "D:\\Tools\\ffmpeg.exe",
  "ffprobe": "D:\\Tools\\ffprobe.exe"
}
```

```powershell
& ./.venv/Scripts/python.exe scripts/video_upscale/windows_uat.py prepare --config "D:\LocalDrama-UAT\uat-config.json"
```

成功后逐个打开回执 `renders_for_review[].absolute_review_path`，检查细线、文字、运动、暗部、闪烁和音画同步。只有真人确实看完全部输出，才运行第二阶段：

```powershell
& ./.venv/Scripts/python.exe scripts/video_upscale/windows_uat.py finalize `
  --session "D:\LocalDrama-UAT\video-upscale-run-20260921-001\evidence\uat-session.json" `
  --reviewer "实际审核人" `
  --confirm I_REVIEWED_EVERY_OUTPUT
```

`finalize` 会复核隔离标记及输出 hash，记录人工声明，再批准、批量采用、生成横竖 1080p 正式交付包并保存 probe/hash 证据到 `evidence/uat-final.json`。脚本的合成 fixture 用于验证真实引擎和全链路，不替代用户认可的 5–10 秒真实漫剧片主观画质验收，也不覆盖运行中进程树取消、断电/重启恢复等 UAT-B/C 场景。
