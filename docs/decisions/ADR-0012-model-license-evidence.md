# ADR-0012：模型许可证证据必须是项目内本地记录

## 状态

Accepted — G7-11 / `MODEL_LICENSE_HASH_QUANTIZATION_REPORT`

## 决策

模型 hash/头部/量化报告可以离线生成，但只有项目内 `00_admin/licenses`（或其子路径）的操作者本地 JSON 记录，声明当前模型 SHA-256、license 名称和 `LOCAL_LICENSE_VERIFIED`/`USER_OWNED` 状态，并通过文件内容 hash 校验后，才可把报告置为 PASS。

API 不下载、查询或猜测许可证；路径越界、symlink、JSON 无效、模型 SHA 不匹配均保持 BLOCKED。旧报告不被原地改写，新的报告保留完整审计谱系。

## 影响

- 新增 `model_license_evidence` 不可逆迁移、导入 API 和服务校验。
- G7 readiness 按项目绑定证据与当前模型 SHA 联合判断。
- 没有真实本地许可证文件时，G7 继续诚实地停在 license blocker。
