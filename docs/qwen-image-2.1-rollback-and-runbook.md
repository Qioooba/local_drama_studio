# Qwen-Image-2.1 · 回退路径与实施手册

本文对应 `docs/RTX3090Ti_Qwen_Image_2_1_配置与实施方案.md` 第 11 节的交付要求 7、8 项：
商用授权未满足时保留旧模型作为商业生产默认，以及一条不需要重新下载权重的明确回退路径。

检索基准 2026-09-21。目标机器：单张 RTX 3090 Ti 24GB、Windows、驱动 610.62。

---

## 1. 关键前提：本次接入不删除、不覆盖任何既有资产

| 既有资产 | 本次动作 |
|---|---|
| `qwen-image-2512-q5-k-m`（GGUF，13.0GiB） | 保留文件与原有已验证工作流版本 |
| `qwen-image-edit-2511-q5-k-m` | 保留已验证身份工作流 |
| 旧 Qwen2.5-VL 编码器 / 旧 VAE | 保留给旧模型，不与 2.1 混用 |
| 8188 生产 ComfyUI 运行时（v0.33.1） | **完全不改动** |
| MiniMax H3 视频链、配音、剧本 LLM | 未改动 |
| 已发布 workflow / Profile 的 `content_json` | 不就地修改，一律以新版本替代 |

2.1 的权重位于新子目录，与旧模型文件名不冲突：

```text
F:/AI_Models/LocalDramaStudio/ComfyUI/diffusion_models/Qwen-Image-2.1/qwen_image_2.1_int8_convrot.safetensors
F:/AI_Models/LocalDramaStudio/ComfyUI/text_encoders/Qwen-Image-2.1/qwen3vl_8b_int8_convrot.safetensors
F:/AI_Models/LocalDramaStudio/ComfyUI/vae/Qwen-Image-2.1/qwen_image_2.1_vae_bf16.safetensors
```

---

## 2. 许可边界：2.1 不得自动接管商业生产

2.1 官方许可为 **Qwen Research License**（`qwen-research`），非商业用途定义为研究或评估。
旧 2512 / Edit-2511 模型卡标注 Apache 2.0。

因此：

* 未记录授权前，2.1 只能进入研究/评估流程，**不改变**商业生产默认；
* 每个 2.1 工作流的 `contract.license` 都写入 `commercial_use_requires_authorization: true`；
* `config/model-licensing.json` 是授权状态的唯一记录点；`activate_qwen21.py` 在
  `environment=production` 且未记录授权时拒绝发布 2.1 Profile；
* 满足授权后**也不改写历史项目引用**：历史任务继续绑定其原有 workflow/Profile 版本。

这是部署范围决定，不是显卡能力判断。

---

## 3. 回退路径（不需要重新下载旧权重）

回退按顺序执行，任一步即可停止并验证。

### R1 · 停止 2.1 运行时（释放显存）

```powershell
# 只关闭隔离运行时；8188 生产运行时不受影响
Get-Process python -ErrorAction SilentlyContinue |
  Where-Object { $_.Path -like '*ComfyUI-Qwen21*' } |
  Stop-Process -Force
```

### R2 · 撤销 2.1 Profile 发布（保留证据，不删除行）

```powershell
.\.venv\Scripts\python.exe scripts\qwen21\rollback_qwen21.py --retire-profiles
```

该脚本调用 `ProfilePublicationService.retire(...)`，把 `mp_profile_publications.status`
置为 `RETIRED` 并记录原因。**已发布的历史版本与其 smoke 证据保持不变**，
因此历史项目仍可解释自己的产物来源。

### R3 · 撤销 2.1 工作流发布

```powershell
.\.venv\Scripts\python.exe scripts\qwen21\rollback_qwen21.py --retire-workflows --reason "回退到 2512/2511 生产链"
```

调用 `WorkflowService.revoke(...)`，把 `workflow_versions.status` 置为 `RETIRED`，
并写入 `audit_events` 的 `WORKFLOW_REVOKED`。工作流内容与 hash 不被修改。

### R4 · 恢复默认路由

默认路由由已发布 Profile 决定，因此 R2 完成后 `auto_profile()` /
`capability_resolution` 会自动回落到旧的 `local-suite-image-*` 版本，
无需手工改配置。验证：

```powershell
.\.venv\Scripts\python.exe scripts\qwen21\rollback_qwen21.py --verify
```

`--verify` 断言：2.1 没有任何 PUBLISHED Profile，且 IMAGE_CHARACTER / IMAGE_SCENE /
IMAGE_CONCEPT 各自仍有至少一个 PUBLISHED Profile 指向旧模型。

### R5 · 彻底移除运行时（可选）

```powershell
Remove-Item -Recurse -Force "F:\AI_Models\LocalDramaStudio\Runtimes\ComfyUI-Qwen21"
Remove-Item -Recurse -Force "F:\AI_Projects\h3\local_drama_studio\work\comfy-qwen21"
```

权重文件可以保留（它们从不参与旧链），也可以删除以回收 17.28GB。
删除权重**不影响** R1–R4 的任何一步。

### 回退不做什么

* 不重置数据库、不回滚 Alembic 版本（本次未新增迁移）；
* 不触碰 `job_resource_leases` / `gpu_runtime_leases` 的历史行；
* 不重新下载 2512 / 2511 权重（它们从未被替换）。

---

## 4. 实施手册（正向流程）

### 步骤 0 · 前置检查

```powershell
.\.venv\Scripts\python.exe scripts\qwen21\onboard_qwen21.py --phase models
.\.venv\Scripts\python.exe scripts\verify_local_models.py
```

三个文件必须 `PASS`（真实字节数 **与** SHA-256 双重校验）。

### 步骤 1 · 建隔离运行时（不改动 8188）

```powershell
pwsh -File scripts/qwen21/install_comfy_runtime.ps1
```

固定基线：ComfyUI `0f74f7fb9f83a78bf46188fd4fd53e6bc44c1ae8`、torch `2.11.0+cu130`、
torchvision `0.26.0`、torchaudio `2.11.0`、transformers `5.17.0`、
comfy-kitchen `0.2.35`、frontend `1.53.6`、Python 3.12。

torch 三件套必须成组安装；即使只出图也不能漏装 torchaudio。
安装使用该 ComfyUI 自己的 venv，不改系统 Python 或其他 AI 环境。

### 步骤 2 · 校验节点与模型下拉

```powershell
.\.venv\Scripts\python.exe scripts\qwen21\seed_qwen21_inputs.py
pwsh -File scripts/qwen21/start_comfy_qwen21.ps1     # 前台运行，Ctrl-C 结束
# 另开一个终端：
.\.venv\Scripts\python.exe scripts\qwen21\check_comfy_runtime.py --prompt-probe
```

必须看到 `TextEncodeQwenImage21` 与 `QwenImage21Cache` 存在，且模型下拉出现
`Qwen-Image-2.1/` 子目录。**只在界面能看见节点不足以宣布与基线等价**，
`check_comfy_runtime.py` 会同时记录实际 commit / torch / comfy-kitchen 版本。

### 步骤 3 · 注册工作流与 V2 登记

```powershell
.\.venv\Scripts\python.exe scripts\qwen21\onboard_qwen21.py --output work\qwen21\onboarding.json
```

新增 5 个定义（名称是建议值，`capability` 沿用既有规范值）：

| definition | capability | 说明 |
|---|---|---|
| `QWEN_IMAGE_21_T2I_CONCEPT` | `IMAGE_CONCEPT` | 文生图 |
| `QWEN_IMAGE_21_T2I_CHARACTER` | `IMAGE_CHARACTER` | 文生图 |
| `QWEN_IMAGE_21_T2I_SCENE` | `IMAGE_SCENE` | 文生图 |
| `QWEN_IMAGE_21_EDIT` | `IMAGE_EDIT` | 单参考编辑 |
| `QWEN_IMAGE_21_EDIT_2REF` | `IMAGE_EDIT` | 双参考编辑 |

固定参数（冻结进 `content_json`）：方图 1024×1024、竖图 768×1376、横图 1376×768
（约 1.06MP）、40 步、CFG 1、euler/simple、denoise 1、batch 1、negative_prompt 空、
`weight_dtype=default`、CLIP `qwen_image/default`。高分母图 2048×1152 或 1152×2048、50 步。
所有尺寸对齐 32 像素网格。编辑任务的采样 latent 取自 `TextEncodeQwenImage21` 的第 2 号输出。

### 步骤 4 · 真实冒烟与 Profile 发布

```powershell
.\.venv\Scripts\python.exe scripts\qwen21\activate_qwen21.py --all
```

冒烟 Job 由真实 Worker 执行并登记 artifact；Profile 只能建立在该证据之上。
不手工写 `SMOKE_PASSED`。

### 步骤 5 · 导出 UI / API JSON

```powershell
.\.venv\Scripts\python.exe scripts\qwen21\export_workflow_json.py
```

产出 `docs/qwen-image-2.1-workflows/{api,ui}/<CODE>.json` 与语义绑定表。
UI 文档从已发布版本派生并做结构自检；不得手工编辑，否则会与 Worker 执行的图产生漂移。

### 步骤 6 · 唯一必要的接入验收

```powershell
.\.venv\Scripts\python.exe scripts\qwen21\run_qwen21_acceptance.py --base-url http://127.0.0.1:8189
```

验收项与判据见方案第 9 节；证据写入 `work/qwen21/acceptance.json`，
包含任务 ID、服务端耗时、GPU 占用、输出 PNG 尺寸与 SHA-256。

原链回归（2512 一张、2511 一张、视频一段）必须在 8188 上单独执行，
本脚本只用 `NOT_RUN_BY_THIS_SCRIPT` 显式登记这笔欠账，不会假装已通过。

---

## 5. 创建页 / 批量页可见性（已实施）

方案第 11 节要求"纯 T2I 能出现在用户现有创建页和批量页"。接入时核对代码后确认：
**这两处读取的是旧 V1 Profile 链，不是 V2 `mp_*` 链。**

| 界面 | 数据源 | 代码 |
|---|---|---|
| 创建页（快速生成） | `GET /api/v1/profiles` → `execution_profile_versions` | `api/routes/profiles.py` → `application/profiles.py::list_profiles` → `generation_model_catalog.py` |
| 批量页（资产图） | `/capability-options` + 全局 AUTO 选择 | `application/capability_options.py` |
| V2 直连图像（Quick Create V2） | `mp_execution_profile_versions ⋈ mp_profile_publications` | `model_platform/application/capability_resolution.py` |

因此需要两件事，二者都已实施：

1. **旧执行链支持按 Profile 记录的多运行时端点。** `ComfyGenerationService` 现在从 Job 快照的
   Profile 解析其记录的 ComfyUI 运行时（`execution_profile_versions.runtime_version_id` →
   `mp_runtime_installation_versions.configuration_json.base_url`），未记录时**完全回落到**
   进程级 `comfy_base_url`，所以既有生产路由行为不变。校验失败会 fail closed
   （`COMFY_PROFILE_RUNTIME_NOT_LOOPBACK` / `COMFY_PROFILE_RUNTIME_OUTPUT_OUTSIDE_WORK_ROOT`）。
   没有这一步，2.1 图会被发到缺少新节点的 8188。
2. **legacy 2.1 Profile 已发布**（`scripts/qwen21/publish_qwen21_legacy_profiles.py`），
   走 `derive → 重新绑定 → validate_contract_version → validate_compatibility →
   真实证据任务 → promote_job_artifact → publish_from_evidence`，即方案第 9 节点名的同一条链。

当前结果（每个都有真实证据任务与 media_version）：

| capability | legacy profile | 绑定的 2.1 工作流 |
|---|---|---|
| `IMAGE_CONCEPT` | `local-suite-image-concept` | `QWEN_IMAGE_21_T2I_CONCEPT` |
| `IMAGE_CHARACTER` | `local-suite-image-character` | `QWEN_IMAGE_21_T2I_CHARACTER` |
| `IMAGE_SCENE` | `local-suite-image-scene` | `QWEN_IMAGE_21_T2I_SCENE` |
| `IMAGE_EDIT` | `local-suite-image-edit` | `QWEN_IMAGE_21_EDIT` |

**许可边界如何被保持**：这些 Profile 在 `model_bundle` 记录 `model_code = qwen-image-2.1-int8-convrot`；
`auto_profile()` 会跳过未记录商用授权的模型。因此 2.1 是**可选**路由而不会自动成为默认——
复核结果：`IMAGE_CONCEPT → v13`、`IMAGE_CHARACTER → qwen-image-2512-character-1mp`、
`IMAGE_SCENE → v4`，均非 2.1。

---

## 6. 角色生产路由（已修正）

审计发现 `local-suite-image-character` 唯一 PUBLISHED 版本（v4）绑定 `qwen-image-2512-q5-smoke`
（256×256 / 1 步）。修正分两层：

* **代码**：图事实判定（步数 / 潜空间尺寸 / 绑定覆盖）拒绝把 smoke 规模图当作生产路由，
  与 workflow code 字符串无关。
* **数据**：`scripts/qwen21/publish_legacy_image_profiles.py` 发布
  `qwen-image-2512-character-1mp`（768×1376 ≈ 1.06MP、20 步、完整语义绑定）并据此发布
  `local-suite-image-character` 生产版本。`auto_profile("IMAGE_CHARACTER")` 从 `None` 恢复为可用路由。

---

## 7. 原链回归（已执行）

```powershell
.\.venv\Scripts\python.exe scripts\qwen21\run_legacy_regression.py
```

在 **8188 生产运行时**重跑三个既有家族，证据写入 `work/qwen21/legacy-regression.json`：

| 任务 | 工作流 | 服务端耗时 | 产物 |
|---|---|---|---|
| 2512 文生图 | `qwen-image-2512-production` | 137.4s | PNG 480×832 |
| 2511 编辑 | `qwen-image-edit-2511-q5-smoke` | 44.0s | PNG 1024×1024 |
| H3 图生视频 | `h3-i2v-16x9-silent-final` | 163.7s | MP4 |

三条全部 SUCCESS，说明 2.1 接入没有破坏既有节点、编码或 artifact 提升。
计时取自 ComfyUI 自身记录；各任务使用其**已发布**参数（含已发布步数），因此这是回归检查，
不是画质或吞吐基准。

---

## 8. 运行期资源约束

单卡互斥由既有两层机制保证，2.1 不新增第三层：

1. `job_resource_leases` 的 `GPU_H3_HEAVY` —— 准入期全局单槽；
2. `gpu_runtime_leases` 的 `GPU:0:EXCLUSIVE` —— 执行期跨进程租约，由
   `GpuRuntimeCoordinator` 负责换模型与显存回收。

因此"每个 Profile 各写 concurrency=1"不再是唯一保障：文生图与视频各跑一个任务时，
第二个任务会在同一物理 GPU 上排队，而不是并排执行。

8188 与 8189 在同一张卡上验收时同样不能同时执行 GPU 重任务。

日常队列应集中处理同类图像，减少 2512 / 2511 / 2.1 / 视频模型交替加载。
若同卡还驻留较大的语言模型，先批量完成剧本与提示词、释放 LLM 显存，再进入图像阶段。

---

## 9. 已知边界（不要外推）

* 上游完整安装验证在 **Linux + RTX 3090** 完成；本机 Windows 结果必须单独记录，
  不得直接引用方案第 6 节的秒数。表中数字是作者 2026-09-21 的测量，不是本机承诺值。
* ComfyUI 仍有未关闭的 2.1 编辑噪点报告（#16435）与采样 shift/sigma 对齐报告（#16447）。
  不同报告的设备、精度、尺寸、步数、commit 不一致，既不能据此宣布本组合必然有问题，
  也不能宣布全部图片已免疫。
* 不引入 SageAttention、CK attention、TeaCache、Nunchaku/QuantFunc 或旧 Lightning LoRA。
  2.1 不挂旧 LoRA，步数仍是 40，不用 1/4/8 步假装蒸馏生效。
* RGBA 未纳入本次发布范围；如要开放，需验证 PNG 确有非恒定 alpha 通道，
  且缩略图、素材库、合成链不会丢弃透明度。
* 多参考扩容（>2 张）需作为独立功能验收，原生节点的 16 个槽位不等于 16 张效果保证。
* 一次验收失败时先查版本 / 模型 hash / 节点 / 绑定 / 显存共用问题，
  不要自动开始遍历 sampler、GGUF 精度和参考分辨率。
