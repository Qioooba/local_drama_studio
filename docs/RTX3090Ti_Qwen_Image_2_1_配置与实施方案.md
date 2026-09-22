# RTX 3090 Ti：Qwen-Image-2.1 最终配置与 Local Drama Studio 接入方案

检索基准：2026-09-21。目标：单张 RTX 3090 Ti 24GB、Windows、现有 ComfyUI / Local Drama Studio，以图片质量、角色编辑、吞吐和维护成本综合取舍。

项目现状以用户本次贴出的审计为依据；本次没有直连用户 F 盘或 SQLite 库。以下“社区实测”均指来源作者的测量，“方案决定”是针对本项目的工程判断。本文没有把 RTX 3090、4090 或 Linux 的成绩说成用户 Windows 3090 Ti 的实测。

## 1. 最终决定

**Qwen-Image-2.1 的硬件配置固定为：官方 INT8 ConvRot DiT + 官方 INT8 ConvRot Qwen3-VL 8B 编码器 + 新版 BF16 VAE，使用 ComfyUI 原生节点，常规 40 步、CFG 1、Euler/simple、约 1MP、batch 1、GPU 重任务并发 1。**

不把 GGUF Q4/Q5、BF16 全套、额外 SageAttention、TeaCache、旧版 Lightning 或新的 Nunchaku 插件列为此次必装项。这个选择有完整单卡 RTX 3090 安装和任务记录支持，而不只是文件大小推算。[单卡运行说明](https://github.com/alesha-pro/tools/blob/main/qwen-image-2.1/README.md)

模型去留决定：

| 模型或部件 | 决定 | 用途 |
|---|---|---|
| Qwen-Image-2.1 INT8 双件套 + 新 VAE | 新增，固定本文配置 | 获准用途下的文生图、常规编辑、透明素材 |
| 现有 Qwen-Image-2512 Q5_K_M | 保留文件与原有已验证版本 | 当前生产、历史复现和回退 |
| 现有 Qwen-Image-Edit-2511 | 保留已验证身份工作流 | 当前角色一致性任务和 2.1 编辑不合格时的回退 |
| 旧 Qwen2.5-VL 编码器 / 旧 VAE | 保留给旧模型 | 不与 2.1 混用 |
| SDXL Turbo | 继续维持已退役状态 | 不为此次升级重新启用 |
| FLUX / Z-Image | 本次不新增 | 缺少证明应替换现有角色生产链的同条件证据 |
| 视频模型、配音模型、剧本 LLM | 本次不更换 | 当前审计不足以支持一起替换 |

### 许可会影响默认生产路由

2.1 官方许可证是 **Qwen Research License**，其中非商业用途定义为研究或评估；商业使用需要单独许可。因此，未有相应授权时，2.1 只能进入许可允许的研究评估流程，不能自动接管商业短剧生产。旧 2512 与 Edit-2511 模型卡标注 Apache 2.0。这里决定的是当前部署范围，而不是显卡能否运行。[2.1 官方许可证](https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE)、[2512 模型卡](https://huggingface.co/Qwen/Qwen-Image-2512)、[Edit-2511 模型卡](https://huggingface.co/Qwen/Qwen-Image-Edit-2511)

满足用途许可且完成一次接入验收后，将 2.1 设为新任务默认；保留旧工作流版本与历史任务绑定。没有必要为了启用新默认而删除旧权重。

## 2. 为什么采用这套量化

Qwen-Image-2.1 的 7B 指视觉生成 DiT，不包括文本/视觉编码器。它使用新的 Qwen3-VL 8B 编码器和 64 通道、16 倍空间压缩的 RGBA VAE，生成与编辑共用同一个 DiT。旧 Qwen2.5-VL 与旧 VAE 不兼容这套架构。[Qwen 官方架构说明](https://github.com/QwenLM/Qwen-Image-2.1)

官方另有 Qwen3.5-VL 9B 的 PE-T2I / PE-I2I 提示词增强模型，它们是可选的前置改写器，不能替代这里的 Qwen3-VL 8B 编码器。本项目已有提示词编排能力，此次不额外下载、常驻两套 PE 模型。[官方 PE-T2I](https://huggingface.co/Qwen/Qwen-Image-2.1-PE-T2I)、[官方 PE-I2I](https://huggingface.co/Qwen/Qwen-Image-2.1-PE-I2I)

官方 BF16 文件中，DiT 约 14.2GB，编码器约 17.5GB；两者相加已超过 24GB，且还未计算 VAE、激活、缓存。这不意味着 BF16 不能通过卸载运行，但它不适合作为本项目单卡默认组合。[官方 Comfy 权重目录](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/tree/main)

INT8 ConvRot 的选择兼顾三点：正式发布的兼容权重、RTX 30 系可用的整数计算路径、已有完整单卡运行证据。Comfy Kitchen 的 INT8 运算支持范围覆盖 Ampere；它的 FP8 Tensor Core 路径要求 Ada 或更新架构，NVFP4 则面向 Blackwell。不能把 RTX 50 系的 FP4 加速成绩移植为 3090 Ti 的预期。[Kitchen CUDA 后端](https://github.com/Comfy-Org/comfy-kitchen/blob/main/comfy_kitchen/backends/cuda/__init__.py)、[Kitchen 硬件与安装说明](https://github.com/Comfy-Org/comfy-kitchen)

| 方案 | 本次结论 | 理由 |
|---|---|---|
| INT8 ConvRot 主模型 + INT8 编码器 | 采用 | 官方文件、较合适的显存预算、有单卡完整记录 |
| BF16 主模型 + BF16 编码器 | 不作默认 | 权重合计超过显存，增加切换和卸载压力 |
| GGUF Q4/Q5 | 不作默认 | 已有 3090 Q4 实测；本次选择官方 INT8 原生链，尚无覆盖画质与缓存冷热的完整对照可宣布量化全面胜出 |
| 编码器 W4A8 | 不作默认 | 为本卡进一步压缩编码器没有充分必要，增加质量变量 |
| FP8 / NVFP4 | 不作本次默认 | 应区分存储精度、原生计算支持与实际端到端收益 |
| Nunchaku / QuantFunc 新运行链 | 不新增 | 没有目标机器完整优势证据，增加另一套节点和二进制依赖 |

此处并不声称 INT8 图像逐像素等同 BF16，也不声称已经完成了所有量化方式的同卡盲测。

## 3. 下载清单、目录与校验

只新增以下三个文件。保留文件原名。

| 部件 | 文件名 | 官方页面 |
|---|---|---|
| DiT | `qwen_image_2.1_int8_convrot.safetensors` | [7.26GB 主模型](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/blob/main/diffusion_models/qwen_image_2.1_int8_convrot.safetensors) |
| 文本/视觉编码器 | `qwen3vl_8b_int8_convrot.safetensors` | [9.35GB 编码器](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/blob/main/text_encoders/qwen3vl_8b_int8_convrot.safetensors) |
| VAE | `qwen_image_2.1_vae_bf16.safetensors` | [VAE 文件目录](https://huggingface.co/Comfy-Org/Qwen-Image-2.1/tree/main/vae) |

建议放入用户现有的模型根目录，新增子目录，避免与旧版混淆：

```text
F:/AI_Models/LocalDramaStudio/ComfyUI/diffusion_models/Qwen-Image-2.1/qwen_image_2.1_int8_convrot.safetensors
F:/AI_Models/LocalDramaStudio/ComfyUI/text_encoders/Qwen-Image-2.1/qwen3vl_8b_int8_convrot.safetensors
F:/AI_Models/LocalDramaStudio/ComfyUI/vae/Qwen-Image-2.1/qwen_image_2.1_vae_bf16.safetensors
```

相应的 ComfyUI 模型下拉值应包含 `Qwen-Image-2.1/` 子目录。以 `/object_info` 实际返回值为准，不把 F 盘绝对路径直接填进模型名称字段。

可复用的额外模型目录配置：

```yaml
local_drama:
  base_path: F:/AI_Models/LocalDramaStudio/ComfyUI
  diffusion_models: diffusion_models
  text_encoders: text_encoders
  vae: vae
```

上述结构应合并到已有 `extra_model_paths.yaml`，保留原有其他目录条目。

已核验的固定 HF revision 为 `b8abad01e16a50633160da778bec582b58761463`。大小和 SHA-256 如下，来自作者锁定清单；主模型与编码器 SHA 亦与官方文件页吻合。[锁定清单](https://github.com/alesha-pro/tools/blob/main/qwen-image-2.1/install/models.json)

| 文件 | bytes | SHA-256 |
|---|---:|---|
| DiT | 7256783064 | `cb74113cb03faecd79611b01fd7fd642f0aa60d6f0b95086abee214d75eaa57d` |
| 编码器 | 9350798360 | `8bfd0f6e12abf2d2d697ecc888e5e90b0d6741d6708f05799f53afa560452e8f` |
| VAE | 675509688 | `bb21f7473051e1ac368515dd3f2e15cd44d7a11748ee8823e1ddca3e4876b7c9` |

三文件合计 17,283,091,112 bytes，约 17.28GB / 16.10GiB。这是磁盘权重体积，不是显存峰值。VAE 文件大小不能直接当作 VAE 执行时占用。安装目录建议预留至少 45GB，已有文件通过目录映射复用。

系统内存按 64GB 以上留余量；若现有机器为 128GB，不需要为这次模型升级增加内存。这里沿用运行说明的容量建议，不能把 128GB 测试机结果解释为 32GB 环境也已同样验证。

## 4. 固定运行环境

采用已有运行记录的基线，而不是把“最新 main”当作可复现版本。该基线是社区安装验证版本，不是声称它包含所有后续修复。

| 组件 | 基线 |
|---|---|
| ComfyUI commit | `0f74f7fb9f83a78bf46188fd4fd53e6bc44c1ae8` |
| PyTorch | `2.11.0+cu130` |
| torchvision | `0.26.0+cu130` |
| torchaudio | `2.11.0+cu130` |
| transformers | `5.17.0` |
| comfy-kitchen | `0.2.35` |
| Comfy 前端验证版本 | `1.53.6` |
| Python | Windows 部署选 3.12；完整 Linux 记录使用 3.10.12 |

[版本锁定文件](https://github.com/alesha-pro/tools/blob/main/qwen-image-2.1/install/versions.json)、[完整环境记录](https://github.com/alesha-pro/tools/blob/main/qwen-image-2.1/docs/validation.md)

Torch、torchvision、torchaudio 必须成组安装；不要因为本工作流只出图就漏装 torchaudio。安装操作必须使用该 ComfyUI 的 Python 环境，不要改到系统 Python、现有视频运行环境或其他 AI 环境。

Windows 驱动应满足所用 cu130 轮子的要求；Kitchen 文档说明预编译 CUDA 包要求 r580+。社区记录的 `610.43.02` 是 Linux 驱动记录，不是让 Windows 用户寻找同名安装包。[Kitchen 安装要求](https://github.com/Comfy-Org/comfy-kitchen)

启动基线（在新环境目录中使用该环境 Python 执行）：

```powershell
python main.py --listen 127.0.0.1 --port 8188 --disable-auto-launch --disable-dynamic-vram --reserve-vram 1
```

参数依据为社区已验证启动器。[启动器代码](https://github.com/alesha-pro/tools/blob/main/qwen-image-2.1/install/setup.py)

说明：

- 单卡，batch 1，GPU 重任务并发 1。
- 不额外加 Sage、CK attention 强制选项、TeaCache 或编译选项。
- `--disable-dynamic-vram` 对应该验证环境的管理模式，仍有常规模型卸载机制。
- 不强制 `--highvram`、`--gpu-only` 或长期 `--lowvram`。
- 对当前含视频节点的环境，先建隔离副本、在 8189 验收。最终通过同一 8188 接入，或者沿项目已经存在的多运行时路由接入；不要假装当前全局 `comfy_base_url` 已经支持每 Profile 单独地址。
- 8188 与 8189 在同一张卡上验收时也不能同时执行 GPU 重任务。

如果现有版本已经支持 2.1，也必须保存它的 commit、依赖锁与验收结果。不能只依据界面能看到节点就宣布与此基线等价。

## 5. 参数定案

### 常规图片任务

| 参数 | 固定值 |
|---|---|
| 方图 | `1024 × 1024` |
| 竖图 | `768 × 1376`，约 1.06MP |
| 横图 | `1376 × 768`，约 1.06MP |
| steps | `40` |
| cfg | `1.0` |
| sampler | `euler` |
| scheduler | `simple` |
| denoise | `1.0` |
| batch_size | `1` |
| seed | 每任务显式传递、保存实际值 |
| negative_prompt | 空 |
| UNET weight_dtype | `default` |
| CLIP type / device | `qwen_image / default` |

40 步与 Qwen 参考实现一致，且有本次采用的单卡完整记录。Comfy 教程提供 25 步，这是较快的示例设置；本方案为主资产固定采用 40 步，不要求用户在 25/30/40 步间反复比较。[Qwen 参考用法](https://huggingface.co/Qwen/Qwen-Image-2.1)、[Comfy 官方设置说明](https://docs.comfy.org/tutorials/image/qwen/qwen-image-2-1)

1376×768 并非严格 16:9；它是对齐 32 像素网格的约 1MP 镜头画布。进入视频链路时，再根据项目目标尺寸做统一裁切和缩放，不拉伸人物。若工程要求严格 16:9，使用单独固定的 `1536×864` 模板，不能把其速度沿用 1MP 成绩。人物全身图、特写、场景图可用不同提示词，不要让角色 Profile 回落到 256² 冒烟配置。

### 高分辨率文生母图

只给需要细节的角色主参考、封面或关键帧使用：横版 `2048×1152`，竖版 `1152×2048`，50 步，其他参数不变。这是约 2.36MP，不是 2048² 的 4.19MP。已存在的高分辨率时间记录对应横版文生图，不扩展为同分辨率多参考编辑保证。[母图示例与像素口径](https://github.com/alesha-pro/tools/blob/main/qwen-image-2.1/README.md)

### 编辑任务

| 项目 | 固定方案 |
|---|---|
| 常规参考图数量 | 1–2 张 |
| TextEncodeQwenImage21.resolution | `1024`，约 1MP 参考预算 |
| 编辑输出尺寸 | 初版跟随 image_1 比例，采用编码节点返回的 latent |
| QwenImage21Cache | `device=auto, dtype=default` |
| steps / cfg / sampler / scheduler | `40 / 1 / euler / simple` |
| denoise | `1.0` |
| image_1 | 主构图或需要修改的底图 |
| image_2 | 人物身份、服装或指定对象参考，按任务固定角色 |

`resolution=1024` 不是强制每图变为 1024²，也不是保留原尺寸。`resolution=0` 会保留较大的参考图尺寸，因此不得无意继承外部模板中的 0。编辑时必须使用 `TextEncodeQwenImage21` 的 latent 输出，不要随意另外创建尺寸不同的空 latent。[编辑分辨率与缓存规则](https://docs.comfy.org/tutorials/image/qwen/qwen-image-2-1)

官方模型说明支持最多 10 张参考图；这不是本卡默认要同时输入 10 张的理由。原生节点暴露 16 个槽位也不构成 16 张效果保证。此项目首先发布已有完整单卡依据的 1–2 参考流程；多参考扩容需作为独立功能验收。

指令例子：以 image_1 提供镜头布局，以 image_2 提供人物身份，明确需要改变的动作/衣着/背景，以及必须保留的脸部特征。不得把“固定 seed”当作身份保持算法。持续生成镜头时应锚定审核通过的角色主参考，避免仅把上一张生成图递归传给下一张而积累漂移。

## 6. 可期待的速度，以及证据边界

以下均是 **作者 2026-09-21、Linux、单张 RTX 3090 24GB、128GB 系统内存、观察到 290W 功率上限** 的已完成任务记录，不是本次助手实跑，也不是用户 3090 Ti 的承诺值。

| 任务 | 尺寸 | 步数 | 完整任务时间 |
|---|---|---:|---:|
| 文生图 | 1024×1024 | 40 | 37.581 秒 |
| 高分辨率文生图 | 2048×1152 | 50 | 84.295 秒 |
| 单参考编辑 | 1024×1024 | 40 | 37.106 秒 |
| 双参考编辑 | 1024×1024 | 40 | 44.554 秒 |
| RGBA | 1024×1024 | 40 | 21.823 秒 |

时间来自 ComfyUI 的 execution_start 到 execution_success，包括编码、采样、解码、保存。测试顺序、模型与缓存冷热不同，因此不能得出“RGBA 一定比 RGB 快”之类比较结论；没有逐阶段性能分解。[机器可读结果](https://github.com/alesha-pro/tools/blob/main/qwen-image-2.1/docs/validation-results.json)

对用户机器的判断：同属 24GB Ampere 卡，采用这套组件和约 1MP 任务是有依据的部署选择。但没有找到同等完整的 Windows 3090 Ti 记录，不能承诺它等于表中秒数或按算力比例必快多少。合理预期是参考同级卡的几十秒工作量，而不是宣传单张 3–5 秒。

同作者的 GGUF Q4_K_M 方案也有记录，可补充理解这次为何没有选择 GGUF：

| 任务 | 官方 INT8 记录 | GGUF Q4_K_M 记录 |
|---|---:|---:|
| 1024² / 40步文生图 | 37.581秒 | 60.35秒 |
| 2048×1152 / 50步文生图 | 84.295秒 | 153.31秒 |
| 1024² / 40步双参考编辑 | 44.554秒 | 79.03秒 |

这些是两套实际工作流记录，环境与缓存并未完全受控，不能算作孤立量化精度的公平竞赛；但它们不支持“文件更小就一定更快”。“3.05GiB 可以跑”的极限配置则面向低显存，不能作为本张 24GB 卡的速度推荐。[GGUF 实际验证记录](https://github.com/alesha-pro/tools/blob/main/qwen-image-2.1-gguf/docs/validation.md)

## 7. 不增加的模型和加速方案

### FLUX.2 klein 4B 与 Z-Image-Turbo

FLUX.2 klein 4B 确实支持文生、编辑与多参考，官方给出约 13GB 显存并明确提及 RTX 3090；它是值得关注的快速模型，但这不证明它对本项目中文指令、角色身份与已验证资产链全面更优。[FLUX.2 klein 4B 官方模型卡](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B)

Z-Image-Turbo 的 6B 与少步推理适合快速草图，但不能仅凭 T2I 吞吐就替代现有多参考身份链。此次目标是形成固定、可维护的图片生产组合，因此不再增加第三套模型路由。[Z-Image-Turbo 官方模型卡](https://huggingface.co/Tongyi-MAI/Z-Image-Turbo)

### SageAttention、缓存、LoRA

2.1 新架构需要特定的条件注意力与缓存语义。已有单卡基线没有额外启用 Sage/CK attention 节点或启动开关；无需先编译更多注意力依赖。保留原生条件前缀缓存 `auto/default`，不把它与 TeaCache 的近似跳步混为一谈。其他运行时对近似缓存的支持边界各有不同，不能跨框架照搬开关。[原生运行示例](https://github.com/alesha-pro/tools/blob/main/qwen-image-2.1/workflows/api/05-two-reference-edit.json)、[vLLM 2.1 限制说明](https://recipes.vllm.ai/Qwen/Qwen-Image-2.1)

现有 Qwen-Image-2512 / Edit-2511 的 Lightning 不兼容 2.1 DiT。2.1 不挂旧 LoRA，不把步数改成 1/4/8 来假装蒸馏已生效。

### 旧链若继续承担商业生产

固定保留当前已验证主模型。角色生产模板先改正为约 1MP，不再使用 256² 单步。2512 标准质量配置可参考官方 50 步、true CFG 4 的示例，不能把当前 20 步 CFG 1 称为官方最佳设置。[2512 官方参考参数](https://huggingface.co/Qwen/Qwen-Image-2512)

旧链需要提速时，采用对应基模的 **8 步 BF16 Lightning LoRA**，分别是：

- [Qwen-Image-2512-Lightning-8steps-V1.0-bf16.safetensors](https://huggingface.co/lightx2v/Qwen-Image-2512-Lightning/blob/main/Qwen-Image-2512-Lightning-8steps-V1.0-bf16.safetensors)
- [Qwen-Image-Edit-2511-Lightning-8steps-V1.0-bf16.safetensors](https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning/blob/main/Qwen-Image-Edit-2511-Lightning-8steps-V1.0-bf16.safetensors)

这个后续旧链加速版本采用 8 步、CFG 1、LoRA strength 1、Euler/simple、batch 1；保留原已验证的加载链，建立新版本，不覆盖旧证据。8 步是本方案为速度与生成余量作出的工程选择，不是声称对所有提示词质量都优于 4 步。GGUF 的 LoRA 集成仍需一次实际兼容验收。[GGUF 项目说明](https://github.com/city96/ComfyUI-GGUF)

此次不追加 QuantFunc/Nunchaku 新运行时。社区确有独立 2512/2511 量化包，但它不等于官方 Nunchaku 已验证的 3090 Ti 全流程方案。[社区包作者说明](https://huggingface.co/QuantFunc/Nunchaku-Qwen-Image-2512)

## 8. Local Drama Studio 接入设计

本节根据用户提供的源文件路径与行为设计，实施 AI 必须以实际仓库 schema 校验，不把下面的新 code 误认为已经存在的枚举。

### Phase A：清理生产默认和保留回退

1. 实时库定位 `data/local_drama.sqlite3`。备份数据库时使用 SQLite backup API 或停服务后完整备份，不能复制正在写入且忽略 WAL 的单一文件；根目录 0 字节 `local_drama.db` 不参与迁移。
2. 导出目前生效的 Profile 版本、workflow content、bindings、模型锁和节点版本。
3. 将 `qwen-image-2512-q5-smoke` 的 256² 单步流程从生产默认中移出；保留为验证用途。
4. 任何已发布 workflow/Profile 均以新版本替代，不就地修改已发布的 `content_json`。

### Phase B：新增模型与原生工作流

1. 按第 3 节下载到 canonical_root，校验真实 bytes 和 SHA，加入 `config/model-lock.json`。
2. 沿既有 manifest → artifact → Profile candidate 流程同步，不直接手写 VERIFIED/PUBLISHED 状态。
3. capability 仍使用已有规范值 `IMAGE_CONCEPT / IMAGE_CHARACTER / IMAGE_SCENE`；模型 code 可新增为 `qwen-image-2.1-int8-convrot`。
4. 新建 2.1 专用 workflow definition，例如 `QWEN_IMAGE_21_T2I` 与 `QWEN_IMAGE_21_EDIT`。这些是建议名称，具体声明遵循现有定义类型；它们不能被误写成新的业务 capability。
5. 不复用旧 `QWEN_IMAGE_CONCEPT` 图然后仅替换文件名。

文生图核心节点与参数：

| 职责 | 原生节点 | 关键配置 |
|---|---|---|
| 模型加载 | UNETLoader | 新 INT8 文件，weight_dtype=default |
| 编码器加载 | CLIPLoader | 新 INT8 编码器，type=qwen_image，device=default |
| VAE 加载 | VAELoader | 新 2.1 BF16 VAE |
| 正负条件 | TextEncodeQwenImage21 | prompt 显式，negative_prompt 空 |
| 文生图画布 | EmptyLatentImage | 本文固定尺寸与 batch 1 |
| 采样 | KSampler | 本文 40/1/euler/simple/1 |
| 解码/落图 | VAEDecode + SaveImage | 普通图片为 PNG；保存实际任务 metadata |

编辑新增 `LoadImage` 与 `QwenImage21Cache`，把图像/vae 接入 `TextEncodeQwenImage21`，使用其第 2 号输出索引的 latent。参考连接在已核验 API 图中是 `images.image_1 / images.image_2`，不是根据 UI 标签猜成普通 `image` 字段。[T2I API 图](https://github.com/alesha-pro/tools/blob/main/qwen-image-2.1/workflows/api/01-text-to-image.json)、[双参考 API 图](https://github.com/alesha-pro/tools/blob/main/qwen-image-2.1/workflows/api/05-two-reference-edit.json)

不保留旧 `ModelSamplingAuraFlow(shift=3)` 或 `EmptySD3LatentImage`。使用该版本原生 2.1 采样路径，不声称它与 Diffusers 所有分辨率逐像素对齐。

### Phase C：能力契约与语义参数

纯文生 Profile 必须有必填 PROMPT、显式 seed；不添加必填 REFERENCE_IMAGE 或 FIRST_FRAME。编辑 Profile 则设 REFERENCE_IMAGE_1 为必填，并按单双参考模板要求绑定第 2 张。

需要审查/补全的语义映射：

| 语义角色 | 文生图目标 | 编辑目标 |
|---|---|---|
| PROMPT | TextEncodeQwenImage21.prompt | 同左 |
| SEED | KSampler.seed | 同左 |
| WIDTH / HEIGHT | EmptyLatentImage.width / height | 首版采用参考比例 latent；若暴露自定义尺寸，另做一致的尺寸构造 |
| STEPS | KSampler.steps | 同左 |
| CFG | KSampler.cfg | 同左 |
| SAMPLER / SCHEDULER | KSampler 对应字段 | 同左 |
| OUTPUT_PREFIX | SaveImage.filename_prefix | 同左 |
| REFERENCE_IMAGE_1/2 | 不出现必填槽 | LoadImage 文件输入，再进入编码节点 |

用户贴出的现状说明资产链主要覆盖 PROMPT+SEED，因此第一版务必把可生产的尺寸、40 步与 CFG 1 冻结进 workflow 内容。若仅在 UI/schema 添加范围而未补 bindings 和调用参数，运行时仍会使用旧值。

2.1 的尺寸校验采用 32 像素步进；前台提供确定的预设。不能继续让新模型无约束沿用 256–4096、step16 的通用范围，然后在 24GB 卡上默许批量 4096²。

每个任务至少保存：输入提示词、最终提示词、seed、模型 revision/hash、workflow/Profile 版本、尺寸、steps、cfg、sampler、scheduler、参考图 ID 与顺序、实际执行耗时。保留现有显式 seed 机制即可，但 seed 不是身份一致保证。

### Phase D：发布与调度

沿已有 validate_against_comfy → publish workflow → derive_contract_version → validate_contract_version + validate_compatibility → 真实证据任务 → promote artifact → publish_from_evidence 链路发布。

资源约束在整个 GPU 执行层生效，而不仅每 Profile 各自写 concurrency=1：如果文生图和视频 Profile 各跑一个，仍可能总计两个重任务。对同一块物理 GPU 建立共享占用约束，任务结束或阶段切换时释放对应模型驻留。

如果同一张卡还驻留较大的语言模型，先批量完成剧本/提示词，再释放 LLM 显存，生成图像后释放图像模型，再进入视频阶段。日常队列尽量集中处理同类图像，减少 2512、2511、2.1、视频模型交替加载。这是调度决定，不需要再换剧本模型。

## 9. 唯一必要的接入验收

用户不需要轮流选量化与参数。下面由实施 AI 按固定方案执行，失败时定位部署问题；它不是开放式模型选型实验。

| 验收项 | 固定任务 | 通过要求 |
|---|---|---|
| 新 T2I | 1 张角色、1 张场景，约 1MP/40步 | 输出可解码，人物/场景正常，尺寸与实际参数相符 |
| 单参考编辑 | 保持主体，替换一个背景或衣物属性 | 指令生效且主体无明显身份漂移/噪点 |
| 双参考编辑 | 构图底图 + 角色参考 | 两参考顺序正确，人物来源正确，输出比例正确 |
| 高分文生图 | 2048×1152/50步一张 | 完成且无 OOM；不据此宣布高分编辑同样通过 |
| 原链回归 | 2512 一张、2511 一张、现有视频流程一段 | 升级后的环境没有破坏已有节点、编码或 artifact 提升 |
| 队列与证据 | 连续 5 个不同 seed 的常规图片任务 | 不重复提交、不串结果、显存不持续累积，全部保存 provenance |

RGBA 如果要在本次发布中开放，还需验证 PNG 确有非恒定 alpha 通道，且缩略图、素材库、合成链不会把透明度丢掉；不要把画面中的棋盘格当作透明证明。不开放 RGBA 时，不需为普通首帧扩大此次验收范围。

计时分开记录冷启动与后续任务。不同任务用不同 seed，防止命中 Comfy 图缓存后误记成模型推理速度。读取服务器任务完成记录，不将 HTTP 提交返回时刻视为出图完成。

一次验收失败时，先检查版本/模型 hash/节点/绑定/显存共用问题；不要自动开始遍历所有 sampler、GGUF 精度和参考分辨率。

## 10. 已知问题与不要外推的地方

截至检索，ComfyUI 有仍未关闭的 2.1 编辑噪点问题报告。不同报告使用的设备、精度、尺寸、步数和 commit 不一致；不能据此宣称本文 INT8 1MP40步组合必然有问题，也不能宣布全部图片已免疫。现有 3090 记录足以支持一套可执行基线，无法代替真实角色素材验收。[编辑噪点报告 #16435](https://github.com/Comfy-Org/ComfyUI/issues/16435)

另有关于 Comfy 与参考实现采样 shift/sigma 差异的开放报告，尤其涉及分辨率变化。此次保持已有实际出图的原生基线，不引入尚未核验的其他模型采样节点“修复”，也不声称和 Diffusers 参数完全等价。[采样对齐报告 #16447](https://github.com/Comfy-Org/ComfyUI/issues/16447)

早期多参考图展示中有作者明确使用 ModelScope API，因此那些结果不能当作本地 INT8 的显存、速度或质量保证。对脸保持的社区反馈也并非一致。[早期作者展示](https://www.reddit.com/r/StableDiffusion/comments/1wlgv0l/i_tested_qwenimage21_its_amazing/)、[官方社区角色参考反馈](https://huggingface.co/Qwen/Qwen-Image-2.1/discussions/11)

## 11. 给实施 AI 的交付要求

最终交付应包括：

1. 新模型锁与真实 hash；原模型文件不被覆盖。
2. 固定 ComfyUI commit 和依赖版本清单，明确 Windows 上实际安装与运行结果。
3. 新 T2I、单参考和双参考工作流的 UI/API JSON，以及语义 bindings。
4. 新版本 Profile 契约与验证结果；纯 T2I 能出现在用户现有创建页和批量页。
5. 修正角色 smoke 被当作生产默认的问题。
6. 固定验收任务的图片、任务 ID、时间、失败日志与 GPU 占用记录。
7. 商用授权未满足时，保留旧模型作为商业生产默认；满足后也不改写历史项目引用。
8. 一条明确回退路径：恢复旧运行环境与默认路由，无需重新下载旧权重。

这次方案的核心是将 2.1 的组件、版本、节点和参数作为一个整体接入，并修正现有冻结默认与生产用途之间的错位。它不要求用户自行开展多模型赛跑，也不承诺生成模型对全部镜头一次成功。
