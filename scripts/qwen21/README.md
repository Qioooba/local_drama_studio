# Qwen-Image-2.1 工具链

把 Qwen-Image-2.1（官方 INT8 ConvRot DiT + INT8 ConvRot Qwen3-VL 8B 编码器 + BF16 VAE）
接入 Local Drama Studio 的全部脚本。方案原文见
[`docs/RTX3090Ti_Qwen_Image_2_1_配置与实施方案.md`](../../docs/RTX3090Ti_Qwen_Image_2_1_配置与实施方案.md)，
回退路径与实施手册见
[`docs/qwen-image-2.1-rollback-and-runbook.md`](../../docs/qwen-image-2.1-rollback-and-runbook.md)。

所有脚本都在仓库根目录用项目 venv 执行：

```powershell
.\.venv\Scripts\python.exe scripts\qwen21\<script>.py ...
```

---

## 执行顺序

| # | 命令 | 作用 |
|---|---|---|
| 1 | `python scripts/fetch_model_lock_files.py --model qwen-image-2.1-int8-convrot` | 下载 17.28GB 权重，校验真实字节数 **与** SHA-256 |
| 2 | `python scripts/qwen21/onboard_qwen21.py --phase models` | 只复核权重（不下载） |
| 3 | `pwsh -File scripts/qwen21/install_comfy_runtime.ps1` | 建隔离 ComfyUI 运行时（v0.37+ 节点） |
| 4 | `python scripts/qwen21/seed_qwen21_inputs.py` | 生成冒烟用的确定性参考图 |
| 5 | `pwsh -File scripts/qwen21/start_comfy_qwen21.ps1` | 在 8189 前台启动运行时 |
| 6 | `python scripts/qwen21/check_comfy_runtime.py --prompt-probe` | 校验节点、模型下拉与一次真实出图 |
| 7 | `python scripts/qwen21/onboard_qwen21.py` | 注册→验证→发布工作流，登记 V2 模型，绑定 |
| 8 | `python scripts/qwen21/activate_qwen21.py --all` | 真实冒烟 Job + Profile 发布 |
| 9 | `python scripts/qwen21/export_workflow_json.py` | 导出 UI/API JSON 与语义绑定表 |
| 10 | `python scripts/qwen21/run_qwen21_acceptance.py` | 验收并写出证据 |
| 11 | `python scripts/qwen21/rollback_qwen21.py --verify` | 回退路径自检 |

---

## 脚本说明

### `onboard_qwen21.py`

按阶段执行，`--phase` 可重复；默认全跑。

| 阶段 | 行为 |
|---|---|
| `models` | 按 `config/model-lock.json` 复核三个文件的字节数与 SHA-256 |
| `runtime` | 读取 `/object_info`，确认 2.1 原生节点存在 |
| `workflows` | 5 个 `QWEN_IMAGE_21_*` 定义：`instantiate` → `register_package` → `validate_against_comfy` → `publish` |
| `registry` | 建 `mp_compute_nodes` / `mp_runtime_installations` / `mp_model_families` / `mp_model_releases` / `mp_model_artifacts` / `mp_runtime_model_installations` / `mp_capability_offerings` |
| `bind` | 每个 offering 绑定到已发布且 schema 校验通过的工作流版本 |
| `handoff` | 输出后续命令与待办 |

幂等：已存在的同内容工作流版本会被复用；登记行使用确定性 `uuid5`。
**不会**手写 `VERIFIED` / `PUBLISHED`；冒烟证据只能由真实 Worker 产生。

### `activate_qwen21.py`

| 子命令 | 行为 |
|---|---|
| `submit` | 为每个绑定提交一个持久化 `MODEL_PLATFORM_COMFY_SMOKE` Job |
| `run` | 驱动真实 `LocalMediaWorker` 直到 Job 结束（会占用 `GPU:0:EXCLUSIVE` 租约） |
| `publish` | `provision` → `smoke` → `publish`，只在冒烟带 artifact 时通过 |
| `all` | 依次执行上面三步 |
| `status` | 只读汇总 |

许可门：2.1 是 Qwen Research License。`environment=production` 且未记录商用授权时会
拒绝发布；`--acknowledge-research-only` 表示明确接受研究/评估范围（不放宽许可）。

### `run_qwen21_acceptance.py`

按方案第 9 节执行固定验收项并写出 `work/qwen21/acceptance.json`：

* 计时取自 ComfyUI `/history` 的 `execution_start`→`execution_success`，
  不把 HTTP 提交返回时刻当作完成；
* 冷启动与后续任务分开统计；
* 每个任务使用不同 seed，避免命中图缓存后误记为推理速度；
* 记录 GPU 占用、输出 PNG 尺寸与 SHA-256、workflow content hash。

原链回归（2512 / 2511 / 视频）必须在 8188 生产运行时单独执行，
脚本只用 `NOT_RUN_BY_THIS_SCRIPT` 显式登记该欠账。

### `export_workflow_json.py`

从**已发布**的工作流版本导出两份人工可读表示到 `docs/qwen-image-2.1-workflows/`：

* `api/<CODE>.json` —— `/prompt` 实际执行的图；
* `ui/<CODE>.json` —— ComfyUI 前端可拖入的 litegraph 文档；
* `README.md` —— 五个定义的语义绑定与输入槽表格。

UI 文档是**派生**的，不是手写的，因此不会与 Worker 真正执行的图产生漂移。
导出时会做结构自检（连线必须指向真实节点与槽位），无法表示的字面量会直接报错而不是静默丢弃。

### `rollback_qwen21.py`

| 选项 | 行为 |
|---|---|
| `--retire-profiles` | `ProfilePublicationService.retire(...)`，保留版本行与 smoke 证据 |
| `--retire-workflows` | `WorkflowService.revoke(...)`，写入 `audit_events` |
| `--verify` | 断言 2.1 已离开默认路径，且旧链仍有 PUBLISHED Profile |

不删行、不改 `content_json`、不重新下载旧权重。

### `check_comfy_runtime.py`

对运行中的 ComfyUI 校验：单一 CUDA 设备、必需节点、模型下拉的
`Qwen-Image-2.1/` 子目录，并可选跑一次真实 768×768/4 步文生图。
同时记录实际 `comfyui_version` / `pytorch_version` / `comfy-kitchen` / `frontend`，
便于与 `config/comfyui-qwen21-runtime.json` 的固定基线对照。

---

## 相关配置

| 文件 | 内容 |
|---|---|
| `config/model-lock.json` | 三个权重的路径、字节数、SHA-256、上游 revision 与许可 |
| `config/comfyui-qwen21-runtime.json` | ComfyUI commit、torch/transformers/comfy-kitchen 版本、8189 端点、启动参数 |
| `config/model-licensing.json` | 每个模型的许可与商用授权状态 |

## 相关代码

| 文件 | 内容 |
|---|---|
| `apps/api/local_drama/application/qwen_image21_workflows.py` | 2.1 原生图构造（T2I / 单参考 / 双参考） |
| `apps/api/local_drama/application/workflow_definitions.py` | `QWEN_IMAGE_21_*` 定义、语义绑定、标量槽位、smoke 合同 |
| `apps/api/local_drama/application/model_licensing.py` | 许可门 |
| `apps/api/local_drama/model_platform/application/comfy_workflow_execution.py` | 按冻结快照的 `runtime_configuration` 解析 ComfyUI 端点（多运行时路由） |

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest apps/api/tests/test_qwen_image21_workflows.py apps/api/tests/test_model_licensing.py -q
```
