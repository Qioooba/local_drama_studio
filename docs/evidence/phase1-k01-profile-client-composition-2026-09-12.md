# Phase 1 / K01：故事规划 Profile 客户端装配实施记录

- 日期：2026-09-12
- 状态：`FIX_MINIMAL`
- 基线 HEAD：`e7b7ec84c88cffdb1c2c78a7298bf3140d8d00c3`
- 范围：仅 K01；未启动真实 LLM/GPU，未访问远端 API，未修改生产数据库，未重启在线服务。

## 问题与红灯证据

真实 `build_story_ai()` 将 `LocalLLMService` 注入 `FullStoryAIGenerationService`。调用方按端口合同执行
`client(profile_version_id=...)`，而实现原先不接受该参数。

新增装配测试首次执行结果：4 个场景中 2 个失败、2 个通过。两个失败均为：

```text
TypeError: LocalLLMService.client() got an unexpected keyword argument 'profile_version_id'
```

## 最小修复

1. `LocalLLMService.client()` 增加显式 `profile_version_id` 合同。
2. 只接受已发布的 LLM Profile；不存在、未发布、能力不匹配或配置不完整均明确拒绝。
3. Profile 引用 ProviderConnection 时，复用现有连接、协议、endpoint 与 secret 解析；Profile 冻结的 model 优先于连接默认 model。
4. 无 ProviderConnection 时，Provider、endpoint 与 model 必须来自 Profile 冻结配置，不静默回落到全局默认。
5. Profile 模式禁止同时传入模型、连接或密钥覆盖，避免执行身份歧义。
6. 默认 Profile 排序增加 `epv.id` 稳定 tie-breaker。

## 验收结果

- K01 新测试：6 passed。
- K01 + 原故事规划 + ProviderConnection：37 passed。
- 扩展相关回归：69 passed，2 deselected。
- `ruff`：新增测试通过；修改模块按仓库规则忽略原有 B007/B904/B905 后通过。
- `git diff --check`：通过。

扩展回归中另确认两个既有测试与当前仓库配置不一致，因此未纳入 K01 绿灯：

- `test_settings_from_env_supports_deepseek_key_alias` 仍假定 DeepSeek 环境变量必须写入 Settings；当前配置由 ProviderConnection/运行时解析密钥。
- `test_system_runs_normally_without_cloud_key` 仍假定默认 Provider 为 Ollama；当前默认已是 `LLAMA_CPP_MANAGED`。

这两个失败在 K01 修改之外，不通过改回当前模型默认或扩大本工作包来掩盖。

## 覆盖场景

- 显式发布 Profile + 远端 ProviderConnection：截获最外层 HTTP transport，核对 URL、Bearer secret 和请求体 model。
- 默认发布 Profile：选择最新版本；完全同序时按 ID 稳定选择。
- DRAFT、RETIRED、能力不匹配：明确拒绝。
- 本地 Profile：使用其 endpoint/model，且不继承全局云 key。

## 回滚边界

回退 `LocalLLMService.client()` 的 Profile 分支、默认选择 tie-breaker 和对应测试即可；没有 schema、数据或历史 Profile 重写需要回滚。
