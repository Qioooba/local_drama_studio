"""接口连通性审计：
1) 解析 apps/web/src/generated/api.ts 中全部接口调用（URL + method）
2) 解析 apps/api 后端注册的全部路由
3) 对比找出"前端调用但后端未注册"的路径（必然 404/405）
输出 api-contract.json 供浏览器实测使用。
"""
import json
import pathlib
import re

api_ts = pathlib.Path("apps/web/src/generated/api.ts").read_text(encoding="utf-8")
api_dir = pathlib.Path("apps/api")

# --- 前端接口（按函数块解析）---
# 路径段参数白名单：这些变量是路径里的 id 段；其余（query/suffix/q/params 等）为查询构建变量，置空
PATH_ID_VARS = {
    "projectId", "project_id", "seasonId", "season_id", "episodeId", "episode_id", "shotId", "shot_id",
    "mediaVersionId", "media_version_id", "jobId", "job_id", "workflowId", "workflow_id", "runId", "run_id",
    "deliveryId", "delivery_id", "renderId", "render_id", "timelineRevisionId", "timeline_revision_id",
    "targetVersionId", "target_version_id", "artifactId", "artifact_id", "id", "token", "subscriptionId",
    "grantId", "grant_id", "promptId", "prompt_id", "revisionId", "revision_id", "intentId", "intent_id",
    "variantId", "variant_id", "experimentId", "experiment_id", "clientId", "client_id", "testRunId",
    "test_run_id", "versionId", "version_id", "proofId", "proof_id", "snapshotId", "snapshot_id",
    "scanId", "scan_id", "reviewId", "review_id", "planToken", "plan_token", "bindingId", "binding_id",
    "mediaVersionId2", "windowId", "window_id", "deliveryId2", "episodeId2", "filePath", "file_path",
    "stageToken", "profileVersionId", "diagnosticId", "draftId", "sessionId", "constraintId", "entryId",
    "candidateId", "packageId", "recipeId", "assetId", "groupId", "policyVersionId", "checkId",
    "proposalId", "transitionId",
}

def normalize_url_template(url: str) -> str:
    """把 `${...}`（含嵌套模板）整体替换：路径 id 变量 → {x}，查询构建变量 → 空。"""
    out = []
    i = 0
    n = len(url)
    while i < n:
        if url[i:i + 2] == "${":
            depth = 0
            j = i
            while j < n:
                if url[j:j + 2] == "${":
                    depth += 1
                    j += 2
                    continue
                if url[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            expr = url[i + 2:j]
            names = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expr)
            name = names[-1] if names else ""
            out.append("{x}" if name in PATH_ID_VARS else "")
            i = j + 1
        else:
            out.append(url[i])
            i += 1
    path = "".join(out)
    path = path.split("?", 1)[0]  # 去掉 query
    return path

front = []
# 找到每个 export async function 的边界
fn_positions = []
for m in re.finditer(r"export async function (\w+)\([^)]*\)", api_ts):
    fn_positions.append((m.start(), m.group(1)))
for idx, (start, name) in enumerate(fn_positions):
    end = fn_positions[idx + 1][0] if idx + 1 < len(fn_positions) else len(api_ts)
    body = api_ts[start:end]
    m = re.search(r"requestJson\((`[^`]*`|'[^']*'|\"[^\"]*\")", body)
    if not m:
        continue
    url = m.group(1).strip("`'\"")
    if not url.startswith("/api/"):
        continue
    norm = normalize_url_template(url)
    mm = re.search(r"method:\s*['\"]([A-Z]+)['\"]", body)
    method = mm.group(1) if mm else "GET"
    front.append({"fn": name, "url": url, "norm": norm, "method": method})

# 去重
front_map = {}
for f in front:
    key = (f["norm"], f["method"])
    front_map.setdefault(key, {"fn": f["fn"], "url": f["url"], "norm": f["norm"], "method": f["method"]})

# --- 后端路由 ---
back = []
for p in api_dir.rglob("*.py"):
    if "routes" not in str(p):
        continue
    text = p.read_text(encoding="utf-8")
    prefix = ""
    pm = re.search(r"router\s*=\s*APIRouter\([^)]*prefix=\"([^\"]*)\"", text)
    if pm:
        prefix = pm.group(1)
    for m in re.finditer(r"@router\.(get|post|put|patch|delete)\(\s*\"([^\"]*)\"", text):
        method, path = m.group(1).upper(), m.group(2)
        norm = re.sub(r"\{[^}]+\}", "{x}", prefix + path)
        back.append((norm, method, str(p.relative_to(api_dir))))

# include_router 前缀
prefixes = []
for p in api_dir.rglob("*.py"):
    text = p.read_text(encoding="utf-8")
    for m in re.finditer(r"include_router\([^,]+,\s*prefix=\"([^\"]+)\"", text):
        prefixes.append(m.group(1))

back_set = set()
for norm, method, src in back:
    # /api/v1 前缀
    back_set.add(("/api/v1" + norm, method))

def path_match(front_path, back_path):
    fs = front_path.split("/")
    bs = back_path.split("/")
    if len(fs) != len(bs):
        return False
    return all(a == b or (b == "{x}" and a.startswith("{")) or (b == "{x}" and a == "{x}") or (b == "{x}") or (a == "{x}") for a, b in zip(fs, bs))

# --- 对比 ---
missing = []
for key, f in front_map.items():
    norm, method = key
    matched = any(m == method and path_match(norm, bp) for bp, m in back_set)
    if not matched:
        missing.append(f)

print(f"前端接口数: {len(front_map)}  后端路由数: {len(back_set)}")
print(f"=== 前端调用但后端未注册（{len(missing)}）===")
for f in missing:
    print(f"  {f['method']} {f['norm']}  (fn={f['fn']})")

print("=== 后端路由样本 ===")
for bp, m in sorted(back_set)[:10]:
    print(f"  {m} {bp}")

out = {
    "frontend": sorted(front_map.values(), key=lambda x: (x["method"], x["norm"])),
    "missing": sorted(missing, key=lambda x: (x["method"], x["norm"])),
}
pathlib.Path("test-results/api-contract.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print("written test-results/api-contract.json")
