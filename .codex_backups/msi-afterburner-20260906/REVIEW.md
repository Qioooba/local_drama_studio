# 3090 Ti 小飞机设置复查记录 — 2026-09-06

## 当前推荐配置

当前卡：NVIDIA RTX 3090 Ti，PCI 04:00.0，VBIOS 默认功耗480W，允许范围100–515W。

- Profile 2（日常均衡，启动自动加载）：73%=350.4W。
- Profile 3（节能）：69%=331.2W。
- 两档核心/显存偏移均为0，使用默认电压频率曲线，不锁定恒定电压。
- 两档温度目标78°C，解除功耗与温度滑条联动。
- 风扇使用显卡固件自动控制：FanMode=1，SwAutoFanControl=0。已通过Afterburner SDK控制接口读回FanAutomatic=true。
- 原Profile 1保留。完整原配置和每次应用前的配置均已备份在本目录。
- 现有Windows登录任务MSIAfterburner保留原触发器与权限，启动参数修正为 `/s -Profile2`；实际经该任务启动、读取GPU功耗及控制接口验证成功。未重启Windows。

活动配置位于 `C:\Program Files (x86)\MSI Afterburner\Profiles`。
重新应用脚本为工作区根目录 `apply_msi_afterburner_efficiency.ps1`，需要Windows管理员批准。
其内容已更新为本次复查版本，不再加载旧312W配置。

## 查到并修正的问题

1. 原来312W档的FanMode=0实际为手动模式，控制接口显示固定66%，并非先前报告的自动风扇。
2. 原登录任务只有 `/s`。用它重启实测，未加载新保存的功耗档；仅写Startup段不足以保证加载。现在明确加载Profile 2。
3. 上次引用普通3090或4K游戏测试，不能证明3090 Ti运行H3时只损失5%性能，更不能证明312W是最佳点。

## 本机H3采样实测

现有MiniMax H3 int8 convrot视频工作流，480×832、107帧、50步、res_multistep/simple。
在同一条现有视频的采样阶段依次改变功耗；核心/显存均保持默认。每档排除切换过渡步骤，使用6–7个有效步骤的累计时间差。原始记录 `power-sweep.ndjson`，计算脚本 `analyze-sweep.cjs`。

| 功耗上限 | 平均实际显卡功耗 | 秒/步 | 核心温度 | 估计显卡能耗/步 |
|---|---:|---:|---:|---:|
| 312W | 278W | 8.83 | 55°C | 2458J |
| 331.2W | 297W | 8.00 | 57°C | 2377J |
| 350.4W | 314W | 7.67 | 58°C | 2410J |
| 374.4W | 340W | 7.14 | 60°C | 2429J |

331.2W是本轮估计显卡能耗/步最低的档位；350.4W相对312W采样吞吐约增加15.2%，相对331.2W又快约4.3%，因此选为日常折中。374.4W更快但散热负担更大。417.6W阶段遇到视频结束，没有足够有效步骤，不纳入结果。

表内功耗来自nvidia-smi采样，不是整机墙上功耗；能耗为近似估计，几个百分点的差异应谨慎解读。短时分段测试未随机化顺序、未重复完整同种子视频，也未完全达到各档热平衡。这支持一个实测均衡建议，不能证明全局最佳点。所有档位都低于480W出厂默认。

历史同规格H3视频在此前417.6W/-104MHz状态完成约342–376秒；完整312W/default状态一条为471.64秒。输入图片、提示词及种子不同，且曲线同时改变，因此仅作为复查线索，不当作严格A/B性能数字。

## 应用后验证

`review-apply-result.json`记录：350.4W、78°C、风扇自动、核心与显存偏移0，启动任务加载成功。
应用后另做约一分钟BF16矩阵乘法短时校验：97–99%左右GPU利用率，功耗约348–350W，核心51–55°C，结果逐批精确校验通过。代码 `verify-gpu.py`；检测到视频队列恢复即停止，未启动新视频任务。

显存结温在NVIDIA接口返回N/A，小飞机未暴露该项。实际风扇转速回读为0，而控制设置可读，不能据此断言风扇停转，也不能从核心温度推导显存结温。此次未完成长时间显存热稳定验证。

## 研究依据

- [Igor's Lab 3090 Ti原始设置](https://www.igorslab.de/en/cooler-breaker-station-fusion-reactor-when-the-geforce-rtx-3090-ti-with-300-watt-choke-sets-the-efficiency-list-on-its-head-and-beats-the-radeons/)：300W测试还修改VF曲线，不能等同只降低功耗滑条。
- [Igor's Lab总结](https://www.igorslab.de/en/cooler-breaker-station-fusion-reactor-when-the-geforce-rtx-3090-ti-with-300-watt-choke-sets-the-efficiency-list-on-its-head-and-beats-the-radeons/12/)：4K游戏平均约313.8W对465.7W；优化档性能落后原档近12个百分点。这不是视频AI测评。
- [Puget RTX3090 TensorFlow测试](https://www.pugetsystems.com/labs/hpc/quad-rtx3090-gpu-wattage-limited-maxq-tensorflow-performance-1974/)：普通3090在280W达到约95%计算性能，结果限定于所测硬件与ResNet50负载。
- [LocalLLaMA 多任务原始测试](https://www.reddit.com/r/LocalLLaMA/comments/1egvoqj/rtx3090_power_tuning_results_on_llm_vision_tts/)：普通3090的250–300W值得尝试，作者强调针对工作负载选择功耗。
- [3090 llama.cpp原始功耗表](https://www.reddit.com/r/LocalLLaMA/comments/1hg6qrd/relative_performance_in_llamacpp_when_adjusting/)：计算型prefill与decode的性能损失不同，不应泛化至视频。
- [NVIDIA nvidia-smi文档](https://docs.nvidia.com/deploy/nvidia-smi/index.html)：SW Power Cap是功耗限制原因，须与温度限频区分。
- [MSI Afterburner官方使用说明](https://www.msi.com/support/technical_details/VGA_MSI_Utility_AfterBurner)。本机SDK `MACMSharedMemory.h`定义自动风扇标志为1，是此次读回验证的依据。
