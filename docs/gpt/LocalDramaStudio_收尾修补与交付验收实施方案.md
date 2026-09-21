# LocalDramaStudio 收尾修补与交付验收实施方案

版本：2026-09-14
审查基线：`791b3c48aa66f15b23cf024ffd1d29c943a7055d`
项目：`Qioooba/local_drama_studio`
用途：可以整体交给编码 AI 实施。本文是设计与任务合同，不是已集成的补丁，不代表仓库测试已通过。

## 0. 交给实施 AI 的总指令

你以资深架构师、项目负责人和全栈工程师的职责实施本方案。目标是修复已定位的可靠性边界，完成有限验收，不是重新设计 LocalDramaStudio。

先核对当前 HEAD、本地未提交改动、已有等价实现与测试。下文路径以审查基线为准；当前代码如已有修复，补证据或缺失测试，不重复实现。不覆盖其他代理或用户的未提交代码，不执行破坏性 reset/clean，不擅自提交到远程。

保持现有 React / Router / TanStack Query / FastAPI / SQLite 模块化单体。继续使用已有 Job/Attempt、GenerationIntent/Variant、MediaVersion/Artifact、版本指纹、人工 ReviewDecision、时间线、交付验证与工作流机制。不新增队列、不新增第二套业务状态、不引入全局状态库、不更换 UI 框架。

不得为通过验收放宽 stale/revision/profile/审核门禁，不得写数据库补 APPROVED/SUCCEEDED，不得删除失败历史，不得自动采用素材，不得用重新生成掩盖回执未知。不把本地/可信局域网版本宣传为公开托管多租户版本。

工程回归可使用 mock、临时数据库与现有自动化测试。真实产品全流程验收必须由唯一测试者通过浏览器可见页面操作完成；沿用用户现有的单个 Luna 测试者时，先核实该环境确有调用和等级设置能力，不伪称已经启动代理。不得以 curl、脚本、浏览器控制台调用业务 API 或直接数据库改状态代替页面验收。诊断者可以读日志与代码。

不要求重新生成已通过的图片/视频，不开多组 GPU 测试，不无限抽查画面审美。共享 API/Worker/ComfyUI 有在途任务时，不擅自停止；优先在隔离环境验证代码，部署遵守已有排空和恢复机制。

## 1. 范围、优先级与完成定义

| 任务 | 优先级 | 本轮完成定义 |
|---|---|---|
| R01 草稿保存与导航守卫 | P1：数据保护 | 版本回执可信；旧回调/新编辑不会误放行；所有未保存内容都可追溯 |
| R02 资产批次提交与恢复 | P1：重复计算保护 | UNKNOWN 不换键；精确回执查询；同键异参冲突；刷新失败不变更提交事实 |
| R03 当前集任务重试反馈 | P2：流程可用性 | 同一任务单飞、失败可见、未知结果先核对、不误刷其他分集 |
| R04 未定位 API 失败收尾 | 发布判断项 | 定位失败并给出修复/环境问题证据，或明确保持 NOT_VERIFIED |
| R05 真实整集页面验收 | 发布判断项 | 一条真实页面路径交付 480p、目标约 120 秒视频，并保留真实证据 |

按 R01 → R02 → R03 → R04 → R05 推进，每项单独提供 diff、测试与边界说明，不一次混入无关需求。R04 可以先做日志定位，但不要阻塞前面确定性修补。每项只在对应断言成立时标记 CLOSED。

建议证据文件：`docs/evidence/2026-09-14-reliability-closure.md`。文件名为本方案建议新增项。

### 1.1 开始前必须执行

```bash
git status --short
git rev-parse HEAD
git diff --stat
git grep -n 'notifyDraftDirty' -- apps/web/src
git grep -n 'savedVersion' -- apps/web/src
git grep -n 'ProjectAssetImageWorkbench' -- apps/web/src
git grep -n 'AssetImageGenerationBatchService' -- apps/api
git grep -n 'command_idempotencies' -- apps/api/local_drama
git grep -n 'retryJob' -- apps/web/src
```

输出一张“审查问题 → 当前函数 → 是否已有修复 → 本轮改动”表即可。不要重复写大篇背景报告。基线变更时记录新 SHA，并按行为而非旧行号定位。

## 2. R01：草稿保存是一个带版本确认的有限协调过程

### 2.1 已核对的代码事实

- `apps/web/src/layouts/AppShell.tsx` 的 `finishBlockedNavigation` 只逐项检查当次快照，末尾直接 `setDraftDirty(false)` 与 `blocker.proceed?.()`，未再次核对整个集合。
- `apps/web/src/features/drafts/draftGuard.ts` 已定义 `savedVersion`，但调用端未据此核对保存的是哪一份本地编辑。
- `DirectorIntentEditor.tsx` 与 `ShotGenerationInspector.tsx` 已是版本化草稿生产者，仍需一并迁移生产者合同。只改 AppShell 不能修复生产者清掉新编辑的情况。
- 两个已核对编辑器均存在把保存与后续刷新放在同一个 try 中的路径；应区分“业务写入已确认”和“读取刷新失败”。

### 2.2 改动文件

修改现有 `draftGuard.ts`、`AppShell.tsx`、上述两个生产者及相应测试；用全仓搜索确认其他草稿生产者并逐一处理。

建议将协调逻辑提取为 `features/drafts/settleDirtyDrafts.ts`；注册集合如没有可复用实现，可新增一个轻量 `draftRegistry.ts`。这些是建议新增路径，不是断言仓库已有文件。不为此改造全站状态管理。

### 2.3 明确四类身份

| 字段 | 语义 | 禁止的做法 |
|---|---|---|
| ownerId | 一处编辑器的逻辑身份 | 仅用当前 URL 区分同页多个草稿 |
| entityKey | 可给用户看的实体名称 | 将它用作唯一技术 ID |
| registrationToken | 一次挂载注册的身份 | 用旧 token 的异步清理删掉新实例 |
| version | 本地可提交编辑载荷的版本 | 与服务端 revision_no 混用，或在 await 后读取“最新版本”冒充已保存版本 |

`version` 在可提交内容发生实际变化时更新，包括自动联动、恢复草稿与影响提交结果的独立控件。服务端基线刷新但本地可提交内容没变，不应制造一次“用户新编辑”。同 token 版本不得倒退；不同 token 的版本没有大小可比性。

### 2.4 注册集合合同

注册、更新、注销要区分。`dirty=false` 表示该注册当前没有未保存编辑，不表示组件卸载。挂载期间保留 clean owner，以便协调器核对完成回执；真正卸载使用单独注销操作。

注册返回带 token 的句柄；后续 update/unregister 只接受当前句柄。旧句柄晚到的更新和 cleanup 必须忽略。不要再使用“不同 token 但 version 更大就覆盖”的判断。初始化注册必须只来自真实挂载，不允许旧异步回调重新注册已注销实例。

同步读取 registry 必须立即反映输入事件中发生的编辑，不能只依赖异步 React state 或 effect 的最后一次广播。推荐统一 `updateDraft` 入口先更新载荷及本地版本，再发布元数据；不要在 setState updater 内夹带可能被重复执行的副作用。

界面订阅 registry 的不可变摘要；可以使用现有 state 镜像，或 React 原生 `useSyncExternalStore`。后者的 `getSnapshot` 在集合未变时返回同一快照，在集合变化时返回新快照。不要只修改 ref 再期待按钮自动重绘，也不要每次 getSnapshot 都创建新数组。

保存中某个目标 owner 消失，不得把“找不到”解释为“已保存”；本次切换应阻止并要求重新确认。

### 2.5 保存/放弃合同

```ts
type DraftSaveResult =
  | { status: "saved"; savedVersion: number }
  | { status: "blocked"; reason: string };

type DraftDiscardResult =
  | { status: "discarded"; discardedVersion: number }
  | { status: "blocked"; reason: string };
```

生产者的 save/discard 接收 `expectedVersion`。开始前校验当前版本；冻结“当次载荷、本地版本、服务端 expected revision”再调用现有保存接口。所有影响保存的状态须进入快照。

成功后：确认服务端写入；把正式基线更新为当次成功载荷；只有当前载荷仍对应提交版本时才清 dirty。出现后续编辑时必须保留，不得 setDraft(serverResponse) 覆盖。返回的 savedVersion 是当次提交的本地版本。

查询刷新失败单独记 `refreshWarning`，不要伪装成保存未提交；如写入结果已知但尚不能安全对齐当前基线，阻止导航并显示“已保存，页面同步尚未完成”，不得自动再写一版。

多个 owner 写同一个 Shot 时仍遵守服务端 expected_revision_no。协调器执行第二个 owner 前读取最新 callback；生产者收到新服务端基线不能直接清除仍脏的其他区域。出现真实 409 就停止并保留未保存编辑，不得仅替换 expected_revision_no 后原样重发旧全量 fields。不得通过无条件合并覆盖其他 owner 的字段。

旧的 boolean/void 回调不能一刀切删除；先盘点调用点并迁移。尚未迁移的 owner 不允许把 `undefined` 当成功，应返回明确 blocked 或只允许回原编辑器处理。

### 2.6 AppShell 的最终放行规则

协调器只对开始时的一组身份做一次有限遍历，不递归保存中途新增草稿；某项失败不回滚已经成功的其他保存，但必须报告已完成和仍待处理的实体。

```text
同步 single-flight 锁
→ 捕获本次被阻止导航的身份
→ 捕获脏草稿身份集合
→ 每项执行前读取最新注册并验证 token/version
→ 调用当项 save/discard
→ 检查回执版本、当前注册与当前 dirty
→ 重新检查整个注册集合
→ 回到 AppShell 后立即再次检查：
   导航意图未变化、仍处于 blocked、不存在脏草稿
→ 同一同步段中 proceed
```

最后一次检查与 `proceed()` 之间不得再 await。不要靠 `setDraftDirty(false)` 制造放行条件，dirty 必须派生自真实注册集合。使用 ref 做同步单飞锁防止同一个事件循环里的双击，pending state 负责界面提示。

未选择保存成功/放弃成功前，浏览器关闭提醒仍应保留。同站导航由 AppShell 统一负责，避免同时弹浏览器 confirm 和 Router 对话框。移除生产者的重复监听前，先核实独立挂载、外链与 beforeunload 场景由谁承担。

### 2.7 参考代码和验证边界

附件 `settleDirtyDrafts.reference.ts` 提供上述有限协调算法的可编译参考实现。实现者必须先完成生产者与注册集合合同，再集成，不能只复制 helper 而保留旧的“dirty=false 就删除注册”行为。

附件的 12 个独立检查验证了算法边界，不是仓库集成测试，不覆盖真实 Router、组件生命周期或实际保存接口。

### 2.8 必须补的定向测试

| 用例 | 必须断言 |
|---|---|
| A 保存完成，B 保存期间 A 再次变脏 | 不导航，A 的新编辑存在 |
| 保存期间新增 owner C | 不导航，C 没被偷偷自动保存 |
| savedVersion 与提交版本不同 | 不导航，回执错误可见 |
| 当前 owner 保存时出现新编辑 | 新编辑保留，本次停止 |
| 旧实例 cleanup 晚到 | 不影响新 token 注册 |
| owner 处理期间卸载 | 不把缺失当成功 |
| 缺少保存回调/旧 void 返回值 | 不放行 |
| A 保存后 B 的 callback 更新 | 使用最新 callback，不调用旧闭包 |
| 写入成功、查询刷新失败 | 不显示“未保存”，不自动再发写请求 |
| 放弃时产生新版本 | 不清掉新版本 |
| 两次点击保存并切换 | 当次动作只启动一份 |
| 同 Shot 的两个编辑区域 | 409 不覆盖已保存字段，未保存区域不被刷新重置 |

## 3. R02：将“请求是否受理”与“任务执行到哪”分开

### 3.1 已核对的代码事实

`ProjectAssetImageWorkbench.tsx` 已有分组回执和 UNKNOWN 专用恢复按钮，但普通 generate 会创建新键并替换回执，按钮只看 total/busy。恢复函数通过最近几条批次的 plan_hash 匹配，不是精确命令查询。两个 finally 中先 await onChanged 再解除 busy。

`assetImageBatchClient.ts` 的 list 默认只查最近 5 条。

`apps/api/local_drama/application/asset_image_generation.py` 中，`AssetImageGenerationBatchService.submit` 命中 project_id + idempotency_key 就回放，未比较此次请求载荷；检查与插入分处不同数据库上下文。

仓库 `JobService.create_job_in_transaction` 已在 `command_idempotencies` 中实现 scope/key/payload_hash 校验。这次复用该既有机制和相同业务幂等键，不新增第二套幂等表，不新增用户可见命令键。

### 3.2 有限改动范围

前端修改 `ProjectAssetImageWorkbench.tsx`、`assetImageBatchClient.ts` 及测试；可以在该 feature 内提取受控的回执处理 hook/纯函数，不创建全局任务平台。

后端修改 `asset_image_generation.py`；通过现有路由定位列表/提交 API 并做兼容扩展；同步 OpenAPI、生成脚本与生成客户端，不只手工改生成结果。优先复用现有 schema，只有确认缺少必需约束才新增 migration；不得重写旧 migration 或自动清理历史重复行。

### 3.3 前端冻结命令

```ts
type FrozenAssetCommand = {
  schemaVersion: 1;
  projectId: string;
  kind: AssetImageKind;
  idempotencyKey: string;
  expectedPlanHash: string;
  request: {
    asset_kind: AssetImageKind;
    asset_ids: string[];
    profile_version_id: string | null;
    mode: "MISSING_ONLY";
  };
  submittedAt: string;
};
```

每次明确的新生成意图创建一次 key；在第一次发送前冻结并深复制请求。恢复只能使用原 request、原 expectedPlanHash、原 key。原请求 profile_version_id=null 时恢复仍传 null，不能改成解析出的版本 ID。asset_ids 顺序按当前合同保留，不能只在恢复时临时排序。

回执按 projectId + idempotencyKey 存取，不只按 kind；否则同类型历史批次互相覆盖。保留 ACCEPTED/REJECTED/UNKNOWN 等已有语义；任务执行状态继续来自服务端 batch/job，不新增镜头或队列真相。

前端增加轻量、版本化、同标签页可恢复的待确认命令记录。优先复用已有存储设施；无设施时使用有界 sessionStorage。只存命令身份、原始请求和必要回执，不存图片/视频或函数。首次写入失败时要提示并阻止发送需要跨刷新恢复的命令；不要假装可恢复。未决条目不得因 TTL 或容量清理被静默删除。页面刷新读取失败时，先修复记录/核对服务器，不自动生成新键。

sessionStorage 不负责跨标签页并发控制；跨标签页正确性由服务端事务与在途重叠保护保证。不得用 localStorage 锁代替数据库约束。

### 3.4 按钮与状态规则

| 情况 | 普通生成入口 | 允许的操作 |
|---|---|---|
| 无未决命令，目标主图确实缺失 | 可用 | 预览后创建一次新意图 |
| PREVIEWED/SUBMITTING | 锁住当次操作 | 等待受理/查看说明 |
| 存在 UNKNOWN | 不创建新键 | 精确核对回执、沿原键恢复 |
| ACCEPTED 且仍在途 | 不重复提交相同资产 | 查看批次进度 |
| 已受理但刷新失败 | 不转为 REJECTED | 显示“已受理，页面暂未同步”，只刷新 |
| 已确认未受理 | 保留失败原因 | 修正后由用户明确重新预览 |
| 同键异参冲突 | 不自动换键 | 展示冲突与已有批次，人工确认 |

仅禁用按钮不够，generate 函数入口也检查 unresolved/active 范围与同步 in-flight 锁。UNKNOWN 全局阻止该“补齐全部”入口是可接受的保守实现；专用核对按钮仍可用。

### 3.5 精确查回执，不再用 plan_hash 认领旧批次

在现有只读批次列表 API 上兼容增加 `idempotency_key` 过滤：

```http
GET /api/v1/projects/{projectId}/asset-image-batches?idempotency_key={originalKey}
```

本路径是本方案要求新增的查询参数合同，当前基线尚未实现。带参数时按 project_id + key 精确查询，返回 items 0/1 条，不受最近 5 条限制；不得查到别的项目。普通列表不带该参数时保持现有行为。

新增 typed client 方法 `findAssetImageBatchByCommandKey`。恢复规则：

```text
读原冻结命令
→ 精确查询 projectId + 原 key
→ 已找到：显示该 batch 的当前权威状态，不重新分派
→ 权威查询成功且未找到：沿原 key/原请求/原 planHash 提交
→ 查回执失败：保持 UNKNOWN，不判断成没提交
```

查不到与补发之间可能发生原请求提交，必须由服务端原子幂等裁决。plan_hash 只表示计划内容，不代表一次用户命令；禁止用最近 N 条中相同 plan_hash 的历史任务冒充此次回执。

### 3.6 后端请求指纹

在现有 command_idempotencies 中使用作用域，例如：

```text
scope = asset-image-batch:submit:{project_id}
idempotency_key = 前端原有同一个键
payload_hash = 固定规范化后的完整提交请求哈希
response_json = { "batch_id": "已持久化的批次 ID" }
```

这里新建的是既有表中的业务 scope，不是第二套系统或第二个键。response_json 只需保留可靠定位信息；回放通过 get_batch 读取当前进度，不把第一次受理时的 QUEUED 快照永久当最新状态。

指纹至少包含 project_id、规范化 asset_kind、按现有规则处理的完整 asset_ids、请求中的 profile_version_id（含 null）、mode、expected_plan_hash、请求合同版本。不包含时间戳或刷新次数。

参考纯函数：

```python
import hashlib
import json


def asset_batch_request_identity(
    project_id: str,
    asset_kind: str,
    asset_ids: list[str],
    profile_version_id: str | None,
    mode: str,
    expected_plan_hash: str,
) -> tuple[str, str]:
    # 与当前 plan 的去重规则一致：保留首次出现顺序。
    normalized_ids = list(dict.fromkeys(
        str(value).strip() for value in asset_ids if str(value).strip()
    ))
    payload = {
        "schema_version": "asset-image-submit.v1",
        "project_id": project_id,
        "asset_kind": asset_kind.strip().upper(),
        "asset_ids": normalized_ids,
        "profile_version_id": profile_version_id,
        "mode": mode,
        "expected_plan_hash": expected_plan_hash,
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return canonical, hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

同键同指纹：回原批次，不再创建 Intent/Variant/Job。同键不同指纹：沿用仓库 `IDEMPOTENCY_PAYLOAD_MISMATCH` 类业务错误，明确映射为冲突响应，零新增副作用。错误映射以现有统一 DomainRuleError 处理器为准，不在单个接口随意拼不同 envelope。

### 3.7 事务边界与在途保护

1. 先检查既有命令身份。已受理同键回放必须发生在重新判定计划新鲜度之前，避免任务完成使 plan_hash 变化后无法恢复。
2. 对新请求沿用只读 plan/preflight，保留全部业务门禁。
3. 在一个短写事务内，再检查 scope/key；核验当前数据库权威修订/在途范围；预留 batch、items 和 command_idempotency 回执定位。相应唯一约束必须是数据库约束，不依赖前端或内存集合。
4. 唯一插入竞争失败时，仅对已确认的唯一键竞争回读同键记录并比对指纹；不能捕获所有数据库异常都当幂等成功。
5. 只有真正创建预留批次的一方进入既有 GenerationIntent/Variant/Job 分派；回放方不再执行逐资产创建循环。
6. 模型调用、GPU 申请与网络等待必须在短写事务之外。不要为了原子性把整段模型执行包进 SQLite 事务。

核验已有 `MISSING_ONLY` 在途重叠保护。若缺失，在同一预留事务内检查相同项目/资产/主图目的的既有在途 batch item，返回明确“已有进行中批次”及 ID，不创建第二份。按现有 Job 与 batch 的权威状态判定是否仍在途；不能仅看主图还没产出，也不能因前端超时就释放。复用已有 batch/items 和状态归约，不另建队列。

已经失败、部分成功与等待恢复的批次要区分。当前集/原任务恢复继续走既有机制。若发现已预留但进程中断导致未完成分派，回执必须显示真实待恢复状态；不得通过复用 key 再创建另一批，或把 QUEUED 直接标成功。缺少可用恢复入口时应作为明确阻塞记录，不假称已恢复。

历史批次没有完整请求指纹：允许按 key/batchId 只读查询并展示已有事实，不删历史，不伪造指纹；无法证明旧请求完整身份时，变更型回放返回明确兼容性提示和 existing_batch_id，不能悄悄接受异参。新前端优先精确只读查询，因此正常找回旧回执不依赖变更型重放。

### 3.8 分离刷新错误，保证 busy 释放

```ts
// action 内部负责冻结命令与更新提交回执；本函数不推断提交是否成功。
async function runWithRefresh(action: () => Promise<void>) {
  if (inFlightRef.current) return;
  inFlightRef.current = true;
  setBusy(true);
  setRefreshWarning(null);
  try {
    await action();
  } catch (error) {
    // 此处交给“提交阶段/权威业务错误/网络未知”分类函数，不能一概 REJECTED。
    recordCommandError(error);
  } finally {
    try {
      await onChanged();
    } catch (error) {
      setRefreshWarning(`回执已保留，刷新失败：${errorText(error)}`);
    } finally {
      inFlightRef.current = false;
      setBusy(false);
    }
  }
}
```

以上是待融入组件的结构片段；`recordCommandError/errorText` 等需使用仓库现有或本任务实现的 typed helper。不得将片段当完整可直接覆盖文件。

网络中断、响应解析失败、超时、5xx 后可能已有副作用，默认 UNKNOWN；明确的业务拒绝要检查错误 code 和受理阶段。不能靠所有 4xx 一律未受理；尤其同键冲突可能已有旧批次，必须保留原记录并提示核对。

### 3.9 必须补的定向测试

| 用例 | 必须断言 |
|---|---|
| UNKNOWN 后再点普通生成 | 不创建新 key，不多发新意图 |
| 点击精确恢复 | 使用原始 request、key、planHash |
| 同 planHash 的历史批次 | 不误认作当前命令 |
| 对应批次不在最近 5 条 | 精确查询仍找到 |
| 提交已成功但响应丢失 | 恢复返回原 batch，Intent/Variant/Job 数不增 |
| 同键改资产、kind、profile 或 planHash | 冲突，零新增副作用 |
| 同键并发 | 一个预留批次，一个分派方 |
| 两标签页不同 key、重叠在途资产 | 不并行创建两份相同补齐任务 |
| UNKNOWN 后刷新页面 | 原键仍在，不偷偷新提交 |
| 待确认记录存储失败 | 有可见错误，不假装具备恢复保障 |
| accepted 后 onChanged 抛错 | 回执仍 accepted，busy 释放，显示刷新提示 |
| 旧批次无指纹 | 能只读核对，无法证明时不异参回放 |

## 4. R03：任务重试是独立的变更操作

### 4.1 已核对的代码事实

`apps/web/src/layouts/EpisodeTaskDrawer.tsx` 的 retry 直接 await retryJob 再 refetch，点击通过 void 调用，没有变更错误捕获与提交中状态。列表读取错误 UI 不会自动显示重试请求错误。

### 4.2 实施方式

将每一行任务提取为同文件小组件或 `EpisodeJobRow.tsx`，在行组件顶层使用 useMutation。不得在父组件 map 回调里直接调用 Hook。

mutation identity 包含 projectId、episodeId、jobId；业务调用继续使用已生成的 `retryJob(jobId)`。同步 ref 阻止瞬时双击，isPending/state 负责界面。显式配置 `retry: 0`，写操作不能按读取失败策略自动重试。

采用 mutateAsync 必须捕获拒绝；使用 mutate 时把错误展示放在可靠的 mutation callback/state 中。关闭 Drawer/切换分集后，旧请求不能更新新分集的提示；用带 project/episode/job 的行 key、组件生命周期与原作用域失效键隔离。

### 4.3 两段操作分离

```text
retryJob(jobId)
  失败且确定未受理 → 行内失败原因，允许用户按原任务处理
  网络/响应未知      → “重试请求结果待确认”，先读当前任务
  返回受理回执       → “重试已受理”，保留 job ID 与回执修订
        ↓
刷新原项目/原分集任务与现有生产摘要
  刷新成功           → 显示权威任务状态
  刷新失败           → “重试已受理，列表暂未同步”
```

这里的 refetch 错误单独处理。TanStack Query 的 refetch 返回结果也可能带 isError，而不是直接抛出；调用方要明确使用受支持的 throwOnError 选项或检查返回对象，不能 catch 永远捕不到的路径。

UNKNOWN 时不马上再发 retryJob；先查看当前任务 revision/attempt 和状态。缓存中的旧 FAILED 不是“请求肯定没受理”的证据。已确认本次重试受理后，直到看到该次 attempt 的权威进展，不因短暂旧缓存再次开放按钮；以后同一 job 新 attempt 真正失败，应再次允许用户显式重试。

### 4.4 结构示例

```ts
const retryMutation = useMutation({
  mutationKey: ["job-retry", projectId, episodeId, job.id],
  mutationFn: () => retryJob(job.id),
  retry: 0,
});

async function handleRetry() {
  if (retryLockRef.current || awaitingReceipt) return;
  retryLockRef.current = true;
  setRetryMessage(null);
  let accepted = false;
  try {
    const receipt = await retryMutation.mutateAsync();
    accepted = true;
    rememberAcceptedRetry(receipt); // 记录原任务/修订，不改为任务已成功。
    setRetryMessage("重试已受理，正在刷新任务状态。");
  } catch (error) {
    classifyAndShowRetryError(error); // 网络未知保留待核对态。
  } finally {
    try {
      await refreshOriginalScope(); // 使用调用开始时的 project/episode/job。
    } catch (error) {
      setRefreshWarning(accepted
        ? "重试已受理，但列表刷新失败，请刷新状态，不要重复重试。"
        : `任务状态读取失败：${errorText(error)}`);
    } finally {
      retryLockRef.current = false;
    }
  }
}
```

这是整合结构，不是独立可编译组件。需实现 accepted/unknown 与权威 attempt/revision 对齐后的可重试状态归约，不得只解除 ref 锁就再次开放未知请求。

### 4.5 测试

双击只发一次；确定拒绝时行内错误可见；无 unhandled rejection；受理成功但刷新失败不显示“重试失败”；超时不自动连发；切集后不污染当前集；同 job 后续新 attempt 失败还能再次显式重试。任务 input_snapshot、已有 media/采用事实不被本次 UI 修补改变。

## 5. R04：定位原来两个 API 失败，不盲跑整套媒体任务

原验收文档记录 API 全量运行约 60% 出现两个失败标记，随后被中止，缺少 traceback；Web 两轮全量各有一次失败，随后对应文件定向通过，未再次全量。这些是当时记录，不是对最新本地工作区的实时结果。

先保存已有日志和 pytest cache；查看原运行的控制台输出、JUnit、CI 日志、`.pytest_cache/v/cache/lastfailed`。记录 nodeid、代码 SHA、运行环境与异常。cache 存在不保证一定属于那次运行，必须核对，不能把空 cache 当全绿。

确认 cache 来源后可在项目既有 Python 环境执行：

```bash
python -m pytest --lf --last-failed-no-failures=none -vv --tb=short -ra \
  --junitxml=实际证据目录/api-lastfailed.xml
```

`实际证据目录` 为执行者创建的真实目录，不得原样保留占位符。`--last-failed-no-failures=none` 防止缓存为空时意外退化成全量运行。

失败 nodeid 已知则直接定向测试该用例及修改影响的同组测试。R02 后端测试使用临时数据库和 fake generation adapter，不调用真实 Comfy/LLM/TTS。未知失败找不到就记录 NOT_VERIFIED；不因定位失败而偷偷开启一次完整生产链来“碰碰运气”。

前端在 apps/web 下使用已存在脚本，例如：

```bash
npm test -- src/layouts/AppShell.test.tsx \
  src/features/asset-bible-v2/ProjectAssetImageWorkbench.test.tsx \
  src/layouts/EpisodeTaskDrawer.test.tsx
npm run build
```

实现时追加实际新增的 registry/coordinator/编辑器测试路径。包管理器与锁文件按工作区已有约定使用，不更换。现有 build 已包含 TypeScript、Vite 与 bundle budget；不得为了通过改宽预算。

若变更 API 合同，按仓库已有导出流程生成 OpenAPI，再运行已有 client generator；对 schema/client diff 做检查。最后执行 git diff --check。确需新增 migration 时加迁移前后定向测试，不修改旧迁移或删历史数据。

不允许跳过失败断言、扩大 timeout 掩盖竞态、修改测试期待值来迁就错误产品状态。异步测试等待可观察的状态/事件，不用任意 sleep。

## 6. R05：唯一真实整集流程验收

### 6.1 复用已完成工作

先读取 `docs/evidence/luna-ui-full-episode-2026-09-14.md` 的最新追加与当前页面状态。审查基线的尾部是多个视频候选采用进展，并非整集完成证明；本地工作区后来可能已继续，不允许按旧记录重新生成。

优先继续现有专用项目与已有 9 镜、资产、已采用素材。已经有可信前半段证据时只补后半段；不创建第二个相同故事反复消耗 GPU。

### 6.2 产品验收约束

测试者只使用可见页面、按钮、表单、播放与导出流程。不能从后台直接拼接交付，不能写数据库批准，不以请求 API 成功替代点击后的可见事实。

沿既有页面流程完成：必要的未完成视频/配音 → 正式采用和审核 → 后期编辑与时间线 → 冻结 → 整集渲染 → 文件验证 → 人工批准 → 交付/下载。

按现有项目配置核验横屏 480p（基线记录为 854×480、24fps）与目标约 120 秒。最终以当前明确保存的项目规格、冻结时间线和交付验证为准，不能擅自改变规格逃避失败；也不能把任意 110–130 秒当成约 120 秒通过。时长偏差按已有规格容差/帧级封装误差解释，无既定容差时记录实际值与原因，不偷偷放宽。

最终文件可在页面播放并导出。抽查开始、中段、结尾，确认不是空文件/损坏文件、不是全黑、必要音轨非全静音、没有明显断尾；确认每个已规划镜头被时间线实际覆盖。无需逐帧评审美术，不为审美不满意无上限重抽，不用复制一个短片凑两分钟。

刷新页面并重新打开项目后，交付记录与下载/播放入口仍存在。检查第二集或另一项目未被本集操作污染。不得为验证重启恢复在 GPU 执行中强杀生产进程。

### 6.3 证据记录

记录真实 commit、API/Worker 已加载版本（无法从页面确认的作为诊断信息单列）、项目/分集 ID、关键页面路径、冻结时间线/Render/交付包 ID、文件元数据、播放与下载结果、已知限制。工程日志证据与浏览器测试证据分列，不冒称代码读取就是页面通过。

失败时输出“页面步骤 → 实际结果 → 错误信息/任务 ID → 预期 → 修复提交 → 原步骤复验”。不要只写已修复。

## 7. 停止条件与交付包

本轮不追加新模型、云平台、计费、权限系统、全站 UI 换皮或新的架构层。遇到与这五项无关的问题放入 BACKLOG；确实阻断既定页面路径的缺陷可作为单独小任务修复并说明原因。

没有登录鉴权的可信局域网模式不纳入公网开放承诺；这次不顺便实现 SaaS。对外发布定位必须保持清楚。

最终交付：

| 必须内容 | 要求 |
|---|---|
| 任务状态表 | R01–R05 分别 CLOSED/BLOCKED/NOT_VERIFIED，附证据 |
| 改动清单 | 文件、函数、目的；标清新增建议路径与现有路径 |
| 定向测试结果 | 实际命令、代码 SHA、退出码、测试数量和证据位置 |
| API 合同影响 | 有/无；变更时列 OpenAPI/client/migration 状态 |
| 页面验收结果 | 实际交付信息，不以已有短片代表整集 |
| 剩余风险 | 未定位原失败、模型质量、当前部署边界分别说明 |

允许的完成描述示例：“R01–R03 定向验证通过；R04 两个历史失败中的一个尚未定位，因此 NOT_VERIFIED；R05 已完成真实交付/仍阻塞于具体页面步骤。”

禁止描述：“所有问题都修好了、全项目通过”，除非存在对应范围与版本的真实证据。参考算法检查通过不能列入仓库集成测试数量。

## 8. 核对来源（代码路径即实施入口）

S01：GitHub 默认分支提交查询，2026-09-14 检索 HEAD 为 `791b3c48aa66f15b23cf024ffd1d29c943a7055d`。

S02：`apps/web/src/layouts/AppShell.tsx`，`finishBlockedNavigation`；`apps/web/src/features/drafts/draftGuard.ts`。

S03：`apps/web/src/features/director-v2/DirectorIntentEditor.tsx`，save 与草稿广播；`ShotGenerationInspector.tsx`，saveMentionDraft 与注册。

S04：`apps/web/src/features/asset-bible-v2/ProjectAssetImageWorkbench.tsx`；`assetImageBatchClient.ts`；相应测试。

S05：`apps/api/local_drama/application/asset_image_generation.py`，plan、submit、list_batches、get_batch。

S06：`apps/api/local_drama/application/jobs.py`，`create_job_in_transaction` 的既有请求指纹/幂等模式。

S07：`apps/web/src/layouts/EpisodeTaskDrawer.tsx`；`apps/web/package.json`。

S08：`docs/evidence/round2-final-uat-and-benchmark-closure-2026-09-14.md`；`docs/evidence/luna-ui-full-episode-2026-09-14.md`；README 当前明确边界。

设计参考：React 官方 useRef / useSyncExternalStore 文档；TanStack Query v5 官方 Mutations 文档。实际实施以仓库锁定版本及类型检查为准，不升级依赖来迁就参考代码。

## 附录 A：草稿协调器参考实现

完整代码如下。它依赖本方案定义的“同步 registry + clean owner 保留 + 显式回执”合同，不可脱离该合同直接替换 AppShell。

```ts
/**
 * LocalDramaStudio navigation-draft coordinator — REFERENCE IMPLEMENTATION.
 * This file is not a repository patch. Integrate only after migrating producers
 * to the contract below and implementing a synchronous, observable registry.
 * version is the local editable-payload version, NOT a database revision.
 */
export type DraftAction = "save" | "discard";
export type DraftBlocked = { status: "blocked"; reason: string };
export type DraftSaveResult = { status: "saved"; savedVersion: number } | DraftBlocked;
export type DraftDiscardResult = { status: "discarded"; discardedVersion: number } | DraftBlocked;
export type MaybePromise<T> = T | Promise<T>;

export interface DraftOwner {
  readonly ownerId: string;
  readonly entityKey: string;
  readonly registrationToken: string;
  readonly version: number;
  readonly dirty: boolean;
  readonly save?: (expectedVersion: number) => MaybePromise<DraftSaveResult>;
  readonly discard?: (expectedVersion: number) => MaybePromise<DraftDiscardResult>;
}

export interface DraftRegistryReader {
  /** Clean mounted owners must remain readable; unregister is a separate action. */
  get(ownerId: string): Readonly<DraftOwner> | undefined;
  /** Return immutable entries. Updating an entry must not mutate previous snapshots. */
  getDirty(): readonly Readonly<DraftOwner>[];
}

export type SettleResult =
  | { allowed: true; completedOwnerIds: string[] }
  | { allowed: false; code: string; reason: string; completedOwnerIds: string[] };

function sameIdentity(a: Readonly<DraftOwner>, b: Readonly<DraftOwner>): boolean {
  return a.ownerId === b.ownerId
    && a.registrationToken === b.registrationToken
    && a.version === b.version;
}

/**
 * One bounded pass. Never auto-save newly arriving edits, clear the registry,
 * or navigate from this function. The caller must recheck the live registry
 * immediately before invoking the live router blocker's proceed().
 */
export async function settleDirtyDrafts(
  registry: DraftRegistryReader,
  action: DraftAction,
): Promise<SettleResult> {
  const targets = registry.getDirty().map((owner) => ({ ...owner }));
  const completedOwnerIds: string[] = [];
  const blocked = (code: string, reason: string): SettleResult => ({
    allowed: false, code, reason, completedOwnerIds: [...completedOwnerIds],
  });

  for (const target of targets) {
    // Re-read the callback: a previous save may have refreshed the server revision
    // without changing this owner's local editable-payload version.
    const current = registry.get(target.ownerId);
    if (!current) return blocked("DRAFT_OWNER_DISAPPEARED", `“${target.entityKey}”的编辑器已变化，请重新确认。`);
    if (!sameIdentity(current, target)) return blocked("DRAFT_CHANGED", `“${target.entityKey}”产生了新修改，请重新确认。`);
    if (!current.dirty) continue;

    let result: DraftSaveResult | DraftDiscardResult;
    try {
      if (action === "save") {
        if (!current.save) return blocked("DRAFT_SAVE_UNSUPPORTED", `请回到“${target.entityKey}”完成保存。`);
        result = await current.save(target.version);
      } else {
        if (!current.discard) return blocked("DRAFT_DISCARD_UNSUPPORTED", `请回到“${target.entityKey}”处理草稿。`);
        result = await current.discard(target.version);
      }
    } catch (error) {
      return blocked("DRAFT_ACTION_FAILED", `“${target.entityKey}”处理失败：${error instanceof Error ? error.message : String(error)}`);
    }
    if (result.status === "blocked") return blocked("DRAFT_ACTION_BLOCKED", result.reason);

    const acknowledgedVersion = action === "save" && result.status === "saved"
      ? result.savedVersion
      : action === "discard" && result.status === "discarded"
        ? result.discardedVersion
        : null;
    if (acknowledgedVersion !== target.version) {
      return blocked("DRAFT_ACK_MISMATCH", `“${target.entityKey}”的处理回执版本不匹配。`);
    }

    const latest = registry.get(target.ownerId);
    if (!latest || !sameIdentity(latest, target)) {
      return blocked("DRAFT_CHANGED_DURING_ACTION", `“${target.entityKey}”在处理期间发生变化，已保留新修改。`);
    }
    if (latest.dirty) return blocked("DRAFT_STILL_DIRTY", `“${target.entityKey}”仍有未保存内容。`);
    completedOwnerIds.push(target.ownerId);
  }

  const remaining = registry.getDirty();
  if (remaining.length > 0) {
    return blocked("NEW_DRAFT_PENDING", `仍有未处理内容：${remaining.map((owner) => owner.entityKey).join("、")}。`);
  }
  return { allowed: true, completedOwnerIds };
}
```

## 附录 B：参考实现检查方式

本交付制作过程中，参考文件使用 TypeScript 5.8.3 strict 编译，并以 Node.js 22.16.0 运行了 12 个独立算法检查，结果保存在 `reference-check-results.txt`。仓库 package.json 的 TypeScript 范围与本机具体锁定版本不同；实施时仍必须使用仓库环境编译和做集成测试。

```bash
tsc --strict --target ES2022 --module commonjs --outDir build settleDirtyDrafts.reference.ts
node coordinator.reference-tests.cjs
```

检查的是参考协调器，不是用户应用。没有在此次任务中修改远程仓库、启动 Luna、运行真实模型或验证最终整集成品。
