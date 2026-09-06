"""Multi-entity visual asset extraction — LLM-first with generic heuristic fallback."""

from __future__ import annotations

import collections
import hashlib
import json
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from local_drama.application.ports.creative_generation import LocalLLMClientProviderPort
from local_drama.application.ports.database import DatabaseUnitOfWork
from local_drama.config import Settings
from local_drama.infrastructure.local_llm import LocalLLMClient

logger = logging.getLogger(__name__)

# ------------------------------------------------------------
# Generic noise filters — NEVER include real character names.
# Only functional / narrative noise.
# ------------------------------------------------------------
STOP_WORDS = {
    "旁白", "画外音", "第", "场景", "时间", "地点", "镜头",
    "自己", "他们", "我们", "众人", "一人", "二人",
    "神色", "目光", "身着", "负手", "的名", "这种",
    "一句", "一道", "诸塔", "报数", "父命", "杀了他",
    "自语", "沉声", "冷笑", "怒喝", "清声", "淡然", "冷冷",
    "微笑", "轻声", "暗自", "心中", "咬牙", "缓缓", "突然",
    "一声", "笑着", "哭着", "叹道", "问道", "说道",
    "淡淡", "纠正", "烦躁", "立刻", "如今", "当年",
    "身后", "眼前", "双手", "自身", "宗门", "天下",
    "无法回", "给出一", "她只", "她反",
    "一位老", "灯焰对", "却在灯", "皱着脸", "她笑了", "她咳着",
}

# Location / prop suffixes — kept generic, covers 修仙/都市/科幻通用
LOCATION_SUFFIXES = (
    "大殿|主殿|偏殿|密室|静室|仙阁|楼阁|坊市|山谷|药园|山峰|密林|庭院|洞府|血海|王府|古城|宗门|天门|圣堂|花园|擂台|长街|要塞|关隘|石室|大厅|绝壁|峡谷|荒原|育婴堂|主峰|药铺|总部|公司|街道|医院|校园|基地|实验室|星舰|空间站|城市|广场|楼顶|车站|机场|港口|王宫|宫殿|神殿|祭坛|塔|峰|谷|崖|洞|城|镇|村|府|殿|阁|楼|门|堂|院|园|场|街|路|桥|河|湖|海|原|漠|林|山|峰|岛"
)

PROP_SUFFIXES = (
    "神剑|古剑|长剑|宝剑|飞剑|仙剑|神刀|宝刀|神枪|法宝|古灯|神灯|符箓|玉符|神符|宝鼎|药鼎|神鼎|灵玉|宝珠|古书|秘籍|宝镜|玉印|神印|玉瓶|古琴|宝盒|玉镯|宝扇|战旗|灵石|玉佩|金丹|灵丹|神丹|神药|灵药|令牌|神钟|铁鞭|古塔|战甲|灵戒|宝弓|神棍|铁锁|阵|钟|枪|剑|刀|灯|符|鼎|玉|珠|书|镜|印|瓶|琴|盒|镯|扇|旗|石|佩|丹|药|牌|鞭|塔|甲|戒|弓|棍|锁|枪械|手机|电脑|钥匙|文件|项链|戒指|手表|汽车|芯片|药剂|血清|装置"
)

# Verbs for generic extraction
LOCATION_VERBS = r"来到|位于|处于|身在|踏入|步入|身处|走进|前往|回到|在|坠入|出现|进入|离开|抵达|驻守|盘踞|隐藏|坐落"
PROP_VERBS = r"手持|手握|拔出|拿出|祭出|携带|放置|佩戴|夺得|炼制|吞下|服下|拿起|挥动|献给|获得|祭起|扣在|留下|递给|握紧|捡起|收起|丢出|抛出|掏出|扬起"

# LLM truncation — keep head + tail to preserve characters that appear late
LLM_MAX_CHARS = 12000
LLM_HEAD = 7000
LLM_TAIL = 3000


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _truncate_for_llm(text: str) -> str:
    if len(text) <= LLM_MAX_CHARS:
        return text
    head = text[:LLM_HEAD]
    tail = text[-LLM_TAIL:]
    return head + "\n\n…（中间部分已省略，启发式将覆盖全篇）…\n\n" + tail


class ScenePropAssetGenerationService:
    """Extract and manage visual reference assets for characters, scenes, and props."""

    def __init__(
        self,
        database: DatabaseUnitOfWork,
        settings: Settings,
        *,
        llm: LocalLLMClientProviderPort,
    ) -> None:
        self.database = database
        self.settings = settings
        self.llm = llm

    def extract_and_create_all_assets(
        self,
        project_id: str,
        scenes_data: list[dict[str, Any]] | None = None,
        script_text: str = "",
        *,
        visual_style: str = "国风仙侠 电影级写实 (Cinematic Realistic)",
        capability_profile_version_id: str | None = None,
        llm_config: dict[str, Any] | None = None,
        persist: bool = True,
        actor: str = "pipeline-orchestrator",
    ) -> dict[str, Any]:
        scenes_data = scenes_data or []
        extracted_characters: dict[str, dict[str, Any]] = {}
        extracted_scenes: dict[str, dict[str, Any]] = {}
        extracted_props: dict[str, dict[str, Any]] = {}
        llm_succeeded = False
        llm_model_used: str | None = None
        llm_provider_used: str | None = None
        llm_error_msg: str | None = None
        extraction_mode: str = "HEURISTIC"

        # 0. LLM extraction — auto-select profile if none was explicitly chosen
        if script_text:
            try:
                client: LocalLLMClient | None = None

                if capability_profile_version_id:
                    client = self.llm.client(profile_version_id=capability_profile_version_id)
                    llm_model_used = getattr(client, "model", None)
                    llm_provider_used = getattr(client, "provider", None)
                elif llm_config and llm_config.get("base_url"):
                    client = LocalLLMClient(
                        base_url=str(llm_config["base_url"]),
                        model=str(llm_config.get("model") or "qwen2.5:7b"),
                        provider=str(llm_config.get("provider") or "OLLAMA_LOOPBACK"),
                        api_key=llm_config.get("api_key"),
                        allow_private_network=self.settings.allows_private_network,
                        timeout_seconds=60.0,
                    )
                    llm_model_used = getattr(client, "model", None)
                    llm_provider_used = getattr(client, "provider", None)
                else:
                    # Try to auto-select the best available LLM_STORY_PARSE profile for this project
                    try:
                        # Direct DB lookup for a published LLM_STORY_PARSE profile version
                        with self.database.connect() as conn:
                            row = conn.execute(
                                """
                                SELECT epv.id as profile_version_id, epv.model_bundle_json, epv.capability_json
                                FROM execution_profile_versions epv
                                JOIN execution_profiles ep ON ep.id = epv.execution_profile_id
                                WHERE epv.capability = 'LLM_STORY_PARSE' AND epv.status = 'PUBLISHED'
                                ORDER BY epv.version_no DESC LIMIT 1
                                """
                            ).fetchone()
                            if row and row["profile_version_id"]:
                                client = self.llm.client(profile_version_id=str(row["profile_version_id"]))
                                llm_model_used = getattr(client, "model", None)
                                llm_provider_used = getattr(client, "provider", None)
                                logger.info("Auto-selected LLM profile %s (%s) for pipeline", row["profile_version_id"], llm_model_used)
                            else:
                                # No published profile, try default client (uses Settings)
                                client = self.llm.client()
                                llm_model_used = getattr(client, "model", None)
                                llm_provider_used = getattr(client, "provider", None)
                                if not llm_model_used:
                                    llm_error_msg = "未找到可用的 LLM_STORY_PARSE 方案且默认模型未配置"
                    except Exception as auto_e:
                        logger.info("Auto-select LLM profile failed (%s), falling back to default client", auto_e)
                        client = self.llm.client()
                        llm_model_used = getattr(client, "model", None)
                        llm_provider_used = getattr(client, "provider", None)

                if client:
                    system_msg = (
                        "你是一位专业影视概念总监与短剧编剧总监。"
                        "请深度阅读小说正文，提取全书核心出场角色（3-8位主角/配角）、核心场景空间（2-5个主要发生地）与关键道具物资（2-5个关键宝物道具）。"
                        "请为每个实体输出精准的角色小传、场景氛围描述或道具特写设定。"
                    )
                    user_msg = (
                        f"视觉风格设定：{visual_style}\n\n"
                        f"小说文本内容：\n{_truncate_for_llm(script_text)}\n\n"
                        "请务必输出严格的标准 JSON 格式：\n"
                        "{\n"
                        '  "characters": [\n'
                        '    {"name": "干净的角色名(如角色甲/角色乙)", "description": "外貌特征、服饰配色、性格特质与身世背景"}\n'
                        "  ],\n"
                        '  "scenes": [\n'
                        '    {"name": "场景空间名(如地点甲/地点乙)", "description": "建筑风貌、空间结构、环境光影与氛围感"}\n'
                        "  ],\n"
                        '  "props": [\n'
                        '    {"name": "关键道具名(如道具甲/道具乙)", "description": "材质质感、特写细节与关键剧情作用"}\n'
                        "  ]\n"
                        "}"
                    )
                    res = client.chat_json(system=system_msg, user=user_msg)
                    if isinstance(res, dict) and (res.get("characters") or res.get("scenes")):
                        for c in res.get("characters") or []:
                            name = self._clean_person_name(str(c.get("name") or ""))
                            if name and name not in STOP_WORDS:
                                extracted_characters[name] = {
                                    "name": name,
                                    "description": str(c.get("description") or f"{name}，核心角色，视觉风格：{visual_style}"),
                                    "kind": "CHARACTER",
                                }
                        for s in res.get("scenes") or []:
                            name = str(s.get("name") or "").strip()
                            if name and 1 <= len(name) <= 20:
                                extracted_scenes[name] = {
                                    "name": name,
                                    "description": str(s.get("description") or f"{name}，空间环境，视觉风格：{visual_style}"),
                                    "kind": "SCENE",
                                }
                        for p in res.get("props") or []:
                            name = str(p.get("name") or "").strip()
                            if name and 1 <= len(name) <= 15:
                                extracted_props[name] = {
                                    "name": name,
                                    "description": str(p.get("description") or f"{name}，道具物资，视觉风格：{visual_style}"),
                                    "kind": "PROP",
                                }
                        if extracted_characters:
                            llm_succeeded = True
                            extraction_mode = "LLM"
                            logger.info(
                                "AI LLM extraction succeeded with %d characters, %d scenes, %d props (model=%s provider=%s)",
                                len(extracted_characters),
                                len(extracted_scenes),
                                len(extracted_props),
                                llm_model_used,
                                llm_provider_used,
                            )
                        else:
                            llm_error_msg = "LLM 返回空，自动降级到启发式"
                            extraction_mode = "HEURISTIC"
                            logger.info("LLM returned no characters, falling back to heuristic")
            except Exception as e:
                raw_msg = str(e)[:500]
                # Map technical errors to user-friendly messages
                if "LOCAL_LLM_MODEL_REQUIRED" in raw_msg or "必须显式配置模型名" in raw_msg or "model" in raw_msg.lower() and "required" in raw_msg.lower():
                    llm_error_msg = "未配置可用大模型（LLM_STORY_PARSE），请先到 模型与能力 → 模型与能力页 发布或配置 Ollama/DeepSeek/OpenAI 方案"
                elif "LOCAL_LLM_LOOPBACK_UNAVAILABLE" in raw_msg or "Ollama" in raw_msg and "无法连接" in raw_msg:
                    llm_error_msg = "本机 Ollama 未运行或不可达，已降级到启发式；请检查 Ollama 是否启动（默认 http://127.0.0.1:11434）或配置远程模型"
                elif "LLAMA_CPP" in raw_msg:
                    llm_error_msg = "本机 llama.cpp 未运行，已降级到启发式"
                else:
                    llm_error_msg = raw_msg
                extraction_mode = "HEURISTIC"
                logger.info("LLM extraction failed or skipped (%s), using high-precision heuristic fallback", e)

        # 1. Heuristic fallback — frequency-filtered, generic patterns
        if not llm_succeeded and script_text:
            candidate_counts: collections.Counter[str] = collections.Counter()

            # Robust dialogue attribution — supports a named speaker with an intervening clause
            dialogue_verb = r"(?:冷笑一声|沉声说道|沉声问道|沉声道|清声道|冷哼一声|怒喝道|淡然道|微笑道|轻声道|轻声说道|清声说道|自语道|自语|沉声|清声|冷笑|冷哼|怒喝|大声|淡然|冷冷|微笑|轻声|暗自|心中|咬牙|缓缓|突然|一声|淡淡|笑道|说道|问道|喊道|叹道|骂道|怒道|反问|道|说|问|喊|喝|叹|答|笑)"
            raw_dialogues = re.findall(
                rf"([\u4e00-\u9fa5]{{2,4}})[^：:\n]{{0,12}}?{dialogue_verb}\s*[：:]\s*(?:“|\")?([^\n\r]{{2,80}})",
                script_text,
            )
            for raw_spk, _ in raw_dialogues:
                cleaned = self._clean_person_name(raw_spk)
                if cleaned:
                    candidate_counts[cleaned] += 2

            post_dialogues = re.findall(
                rf"(?:“|\")([^”\"]{{2,50}})(?:”|\")\s*([\u4e00-\u9fa5]{{2,4}})[^：:\n]{{0,8}}?{dialogue_verb}",
                script_text,
            )
            for _, raw_spk in post_dialogues:
                cleaned = self._clean_person_name(raw_spk)
                if cleaned:
                    candidate_counts[cleaned] += 2

            intro_names = re.findall(
                r"(?:正是|老者|少年|少女|女子|宗主|掌门|尊者|峰主|阁主|师兄|师姐|长老|侍卫|徒儿)([\u4e00-\u9fa5]{2,4})",
                script_text,
            )
            for raw_name in intro_names:
                cleaned = self._clean_person_name(raw_name)
                if cleaned:
                    candidate_counts[cleaned] += 1

            # Frequency threshold: require at least 2 mentions to be considered a real character
            for name, count in candidate_counts.most_common(8):
                if name in extracted_characters:
                    continue
                if count < 2:
                    continue
                extracted_characters[name] = {
                    "name": name,
                    "description": f"{name}，全剧核心出场人物（文中出场频次：{count}次）。性格鲜明，视觉风格：{visual_style}。",
                    "kind": "CHARACTER",
                }

            # 1b. Scenes — first pass with suffix list, second generic fallback
            scene_counts: collections.Counter[str] = collections.Counter()
            scene_matches = re.findall(
                rf"(?:{LOCATION_VERBS})([\u4e00-\u9fa5]{{2,10}}?(?:{LOCATION_SUFFIXES}))",
                script_text,
            )
            for raw_sc in scene_matches:
                sc_name = re.sub(r"^(?:巍峨庄严的|荒凉险峻的|幽暗深邃的|残破的|险峻的|宏大的|古老的|荒凉的)", "", raw_sc).strip()
                if 2 <= len(sc_name) <= 10 and sc_name not in STOP_WORDS:
                    scene_counts[sc_name] += 1

            # generic fallback if little found — any 2-8 char Chinese phrase after location verb
            if len(scene_counts) < 2:
                generic_scenes = re.findall(rf"(?:{LOCATION_VERBS})[\s·]*([\u4e00-\u9fa5]{{2,8}})", script_text)
                for g in generic_scenes:
                    g = g.strip()
                    if 2 <= len(g) <= 8 and g not in STOP_WORDS and g not in scene_counts:
                        # avoid verbs/particles
                        if g not in {"同时", "一个", "这个", "那个", "这里", "那里"}:
                            scene_counts[g] += 1

            for sc_name, _ in scene_counts.most_common(5):
                if sc_name not in extracted_scenes:
                    extracted_scenes[sc_name] = {
                        "name": sc_name,
                        "description": f"{sc_name}，核心剧情空间。空间结构与光影设计符合{visual_style}。",
                        "kind": "SCENE",
                    }

            # 1c. Props — suffix list + generic fallback
            prop_counts: collections.Counter[str] = collections.Counter()
            prop_matches = re.findall(
                rf"(?:{PROP_VERBS})(?:一枚|一柄|一把|一件|尊|块|口|具)?(?:闪烁微光的|微光的|锋利的|神秘的|上古的)?([\u4e00-\u9fa5]{{0,6}}(?:{PROP_SUFFIXES}))",
                script_text,
            )
            for pr_name in prop_matches:
                pr_name = re.sub(r"^(?:你手中的|你手里的|他手中的|她手中的|手中的|手里的|你|他|她|我|的|之|手中|手里)+", "", pr_name.strip())
                pr_name = re.sub(r"(?:防身|而已|而立|而去|而笑|而道)$", "", pr_name)
                if 2 <= len(pr_name) <= 8 and pr_name not in STOP_WORDS:
                    prop_counts[pr_name] += 1

            # Whole-text scan to upgrade a short suffix to a fuller item name (only clean upgrades).
            # A bare six-character window is too permissive in Chinese prose: a
            # command such as "别让某物" would otherwise become a fake asset.
            # Only expand a candidate when a generic context/name prefix was
            # actually removed, keeping the rule independent of any story title
            # or entity name.
            whole_text_props = re.findall(rf"([\u4e00-\u9fa5]{{2,6}}(?:{PROP_SUFFIXES}))", script_text)
            prop_noise_prefixes = (
                "手中的", "手里的", "的", "之", "手中", "手里", "一枚", "一柄", "一把", "一件",
                "一尊", "一块", "一口", "一具", "闪烁微光的", "微光的", "锋利的", "神秘的", "上古的",
                "烁微光的", "快", "拔出", "握紧", "躺着", "摆着", "放着", "拿出", "祭出", "携带",
                "放置", "佩戴", "夺得", "炼制", "吞下", "服下", "拿起", "挥动", "献给", "获得",
                "祭起", "扣在", "留下", "递给", "捡起", "收起", "丢出", "抛出", "掏出", "扬起",
            )
            entity_prefixes = tuple(sorted(extracted_characters, key=len, reverse=True))
            prop_verb_prefixes = tuple(verb for verb in PROP_VERBS.split("|") if verb)
            for wp in whole_text_props:
                original_wp = wp.strip()
                prefix_pattern = "|".join(
                    re.escape(prefix)
                    for prefix in (*entity_prefixes, *prop_noise_prefixes, *prop_verb_prefixes)
                    if prefix
                )
                if prefix_pattern:
                    wp = re.sub(rf"^(?:{prefix_pattern})+", "", original_wp)
                else:
                    wp = original_wp
                wp = re.sub(r"(?:防身|而已|而立|而去|而笑|而道)$", "", wp)
                if wp == original_wp or not (2 <= len(wp) <= 8) or wp in STOP_WORDS:
                    continue
                if 2 <= len(wp) <= 8 and wp not in STOP_WORDS:
                    # skip noisy upgrade candidates that still contain a pronoun or hand phrase
                    if wp.startswith(("你", "他", "她", "我")) or "手中" in wp[:4] or "手里" in wp[:4]:
                        continue
                    for existing in list(prop_counts.keys()):
                        if existing != wp and existing in wp and len(wp) > len(existing):
                            cnt = prop_counts.pop(existing)
                            prop_counts[wp] = cnt + 1
                            break
                    # do not add entirely new whole-text props that were not seen via verb — too noisy

            # generic fallback: tighten to avoid verb tails like 防身
            if len(prop_counts) < 2:
                generic_props = re.findall(rf"(?:{PROP_VERBS})[\s·]*([\u4e00-\u9fa5]{{2,6}})", script_text)
                for g in generic_props:
                    g = re.sub(r"(?:防身|防身的|而已|而立|而去|而笑|而道)$", "", g.strip())
                    g = re.sub(r"^(?:手中的|手里的|的|之|手中|手里)", "", g)
                    if 2 <= len(g) <= 6 and g not in STOP_WORDS and g not in prop_counts:
                        # avoid substring duplicates: if existing prop is substring of g, skip g
                        if any(existing in g and existing != g for existing in prop_counts):
                            continue
                        prop_counts[g] += 1

            # Deduplicate by suffix: for each suffix group keep longest clean prop
            suffix_groups: dict[str, list[str]] = {}
            known_prop_suffixes = tuple(
                sorted(
                    {suffix for suffix in PROP_SUFFIXES.split("|") if suffix},
                    key=len,
                    reverse=True,
                )
            )
            for name in prop_counts:
                # Group by the longest suffix declared in the generic vocabulary.
                # This keeps a specific name (for example, a long-form item and
                # its short-form suffix) without knowing any story-specific name.
                suffix = next((candidate for candidate in known_prop_suffixes if name.endswith(candidate)), "")
                suffix = suffix or (name[-2:] if len(name) >= 2 else name)
                suffix_groups.setdefault(suffix, []).append(name)

            deduped: dict[str, int] = {}
            for suffix, names in suffix_groups.items():
                # filter noisy prefix
                clean_names = []
                character_names = tuple(extracted_characters)
                generic_noise = ("你", "他", "她", "我", "手中", "手里", "你手中")
                for n in names:
                    prefix = n[: -len(suffix)] if n.endswith(suffix) and len(n) > len(suffix) else ""
                    if any(bad in prefix or n.startswith(bad) for bad in generic_noise + character_names):
                        # keep only if no cleaner alternative exists
                        continue
                    clean_names.append(n)
                candidates = clean_names if clean_names else names
                # keep longest among candidates (most specific)
                candidates.sort(key=lambda x: (len(x), prop_counts[x]), reverse=True)
                best = candidates[0]
                deduped[best] = prop_counts[best]
                # also keep any other suffix groups that are distinct but not substring of best?
                # When a short form and a fuller item share a declared suffix, keep the longest.
                # So only best per suffix.

            # Distinct suffix groups remain distinct and are each represented by their best candidate.
            # The above already handles per suffix, so we have one per suffix.
            prop_counts = collections.Counter(deduped)

            for pr_name, _ in prop_counts.most_common(5):
                if pr_name not in extracted_props:
                    extracted_props[pr_name] = {
                        "name": pr_name,
                        "description": f"关键道具物资：{pr_name}。具有独特材质纹理与微光细节，符合{visual_style}。",
                        "kind": "PROP",
                    }

        # Fallback minimal entities if completely empty — generic, not story-specific
        if not extracted_characters:
            extracted_characters["主角"] = {
                "name": "主角",
                "description": f"核心主角，视觉风格：{visual_style}",
                "kind": "CHARACTER",
            }
        if not extracted_scenes:
            extracted_scenes["核心主场景"] = {
                "name": "核心主场景",
                "description": f"主场景空间，视觉风格：{visual_style}",
                "kind": "SCENE",
            }

        # 3. Materialize candidates. Draft-first callers keep these candidates
        # isolated on their pipeline run; only the explicit apply command may
        # turn them into reviewable project proposals.
        character_assets: list[dict[str, Any]] = []
        scene_assets: list[dict[str, Any]] = []
        prop_assets: list[dict[str, Any]] = []

        def draft_candidate(kind: str, idx: int, name: str, description: str) -> dict[str, Any]:
            prefix = "CHAR" if kind == "CHARACTER" else "SCENE" if kind == "SCENE" else "PROP"
            code = f"{prefix}_{idx:03d}_{self._clean_code(name)}"
            digest = hashlib.sha256(f"{kind}:{name}".encode("utf-8")).hexdigest()[:12]
            return {
                "id": f"draft-{digest}",
                "project_id": project_id,
                "kind": kind,
                "code": code,
                "name": name,
                "description": description,
                "status": "DRAFT_CANDIDATE",
            }

        if persist:
            now = _now()
            with self.database.transaction() as connection:
                for idx, (name, meta) in enumerate(extracted_characters.items(), start=1):
                    asset = self._get_or_create_asset(
                        connection, project_id, "CHARACTER", f"CHAR_{idx:03d}_{self._clean_code(name)}", name, meta["description"], actor, now
                    )
                    character_assets.append(asset)

                for idx, (name, meta) in enumerate(extracted_scenes.items(), start=1):
                    asset = self._get_or_create_asset(
                        connection, project_id, "SCENE", f"SCENE_{idx:03d}_{self._clean_code(name)}", name, meta["description"], actor, now
                    )
                    scene_assets.append(asset)

                for idx, (name, meta) in enumerate(extracted_props.items(), start=1):
                    asset = self._get_or_create_asset(
                        connection, project_id, "PROP", f"PROP_{idx:03d}_{self._clean_code(name)}", name, meta["description"], actor, now
                    )
                    prop_assets.append(asset)
        else:
            character_assets = [
                draft_candidate("CHARACTER", idx, name, meta["description"])
                for idx, (name, meta) in enumerate(extracted_characters.items(), start=1)
            ]
            scene_assets = [
                draft_candidate("SCENE", idx, name, meta["description"])
                for idx, (name, meta) in enumerate(extracted_scenes.items(), start=1)
            ]
            prop_assets = [
                draft_candidate("PROP", idx, name, meta["description"])
                for idx, (name, meta) in enumerate(extracted_props.items(), start=1)
            ]

        # ensure extraction_mode is set
        if not llm_succeeded and not llm_error_msg:
            # heuristic path without LLM attempt (or LLM returned no characters)
            if not script_text:
                llm_error_msg = "无文本，未尝试 LLM"
                extraction_mode = "HEURISTIC"
            elif not llm_model_used:
                extraction_mode = "HEURISTIC"
                if not llm_error_msg:
                    llm_error_msg = "未配置可用大模型，已使用启发式"

        return {
            "project_id": project_id,
            "characters": character_assets,
            "scenes": scene_assets,
            "props": prop_assets,
            "total_assets_count": len(character_assets) + len(scene_assets) + len(prop_assets),
            "extraction_mode": extraction_mode,
            "llm_model": llm_model_used,
            "llm_provider": llm_provider_used,
            "llm_error": llm_error_msg,
        }

    def _get_or_create_asset(
        self,
        connection: Any,
        project_id: str,
        kind: str,
        code: str,
        name: str,
        description: str,
        actor: str,
        now: str,
    ) -> dict[str, Any]:
        existing = connection.execute(
            "SELECT * FROM story_assets WHERE project_id=? AND kind=? AND status='ACTIVE' AND lower(name)=lower(?)",
            (project_id, kind, name.strip()),
        ).fetchone()
        if existing:
            # update description if previously empty and new one richer
            if not (existing["description"] or "").strip() and description.strip():
                try:
                    connection.execute(
                        "UPDATE story_assets SET description=?, updated_at=? WHERE id=?",
                        (description, now, str(existing["id"])),
                    )
                    existing = connection.execute("SELECT * FROM story_assets WHERE id=?", (str(existing["id"]),)).fetchone()
                except Exception:
                    pass
            return dict(existing)

        asset_id = str(uuid.uuid4())
        unique_code = code
        dup_count = 1
        while connection.execute("SELECT 1 FROM story_assets WHERE project_id=? AND code=?", (project_id, unique_code)).fetchone():
            unique_code = f"{code[:80]}_{dup_count}"
            dup_count += 1
            if dup_count > 99:
                unique_code = f"{code[:60]}_{uuid.uuid4().hex[:6]}"
                break

        connection.execute(
            """INSERT INTO story_assets
            (id,project_id,kind,code,name,description,canonical_media_version_id,extra_json,status,
             created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,?,?,NULL,'{}','ACTIVE',?,?,?,1,'v2')""",
            (asset_id, project_id, kind, unique_code, name.strip(), description, now, now, actor),
        )

        # Sync to creative_entries
        try:
            creative_code = f"{kind}_{unique_code}"[:60]
            creative_existing = connection.execute(
                "SELECT 1 FROM creative_entries WHERE project_id=? AND kind=? AND code=?",
                (project_id, kind, creative_code),
            ).fetchone()
            if not creative_existing:
                entry_id = str(uuid.uuid4())
                rev_id = str(uuid.uuid4())
                content_payload = {
                    "name": name.strip(),
                    "kind": kind,
                    "description": description,
                }
                content_hash = hashlib.sha256(_json(content_payload).encode("utf-8")).hexdigest()
                connection.execute(
                    """INSERT INTO creative_entries (id,project_id,kind,code,title,current_revision_id,created_at,updated_at,created_by,revision,schema_version)
                    VALUES (?,?,?,?,?,?,?,?,?,1,'v2')""",
                    (entry_id, project_id, kind, creative_code, name.strip(), rev_id, now, now, actor),
                )
                connection.execute(
                    """INSERT INTO creative_entry_revisions (id,entry_id,revision_no,parent_revision_id,restored_from_revision_id,content_json,content_hash,change_note,created_at,updated_at,created_by,revision,schema_version)
                    VALUES (?,?,1,NULL,NULL,?,?,?,?,?,?,1,'v2')""",
                    (rev_id, entry_id, _json(content_payload), content_hash, "一键故事与圣经生成建档", now, now, actor),
                )
        except Exception as e:
            logger.warning("Failed to sync creative_entries for %s %s: %s", kind, name, e)

        created = connection.execute("SELECT * FROM story_assets WHERE id=?", (asset_id,)).fetchone()
        return dict(created)

    @staticmethod
    def _clean_person_name(raw: str) -> str:
        """Strip suffix verbs and prefix titles conservatively."""
        if not raw:
            return ""
        name = raw.strip()
        # Remove trailing action fragments that leak into speaker capture (看着/沉 etc.)
        name = re.sub(
            r"(?:自语|沉声|清声|冷笑|冷哼|怒喝|大声|淡然|冷冷|微笑|轻声|暗自|心中|咬牙|缓缓|突然|一声|淡淡|纠正|烦躁地|烦躁|无法回|立刻|笑道|说道|问道|喊道|叹道|骂道|怒道|反问|道|说|问|喊|喝|叹|答|笑|哭着|笑着|又说|接着|隔着|来到|位于|处于|身在|踏入|步入|身处|走进|前往|回到|坠入|出现|进入|离开|抵达|驻守|盘踞|隐藏|坐落|走出|擦干|回头|身边|看着|看|沉)+$",
            "",
            name,
        )
        # Remove leading honorifics/titles wholly (not keeping last 3 chars)
        name = re.sub(
            r"^(?:正是|老者|少年|少女|女子|男子|宗主|掌门|尊者|峰主|阁主|师兄|师姐|长老|侍卫|徒儿|楼主|前辈|大人|公子|小姐)+",
            "",
            name,
        ).strip()
        name = re.sub(r"[^\u4e00-\u9fa5A-Za-z]", "", name).strip()
        # Reject verb-phrase contamination that intro_names pattern may capture
        if any(bad in name for bad in ("注视", "凝视", "手中", "手里", "握紧", "手持", "注视着", "盯着")):
            return ""
        if 2 <= len(name) <= 4 and name not in STOP_WORDS:
            return name
        return ""

    @staticmethod
    def _clean_code(name: str) -> str:
        code = re.sub(r"[^A-Za-z0-9_-]", "_", name).strip("_")
        return (code.upper()[:20]) if code else "ENTITY"
