"""Semantic quality rules for AI-extracted story entity identities."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class EntityNameAssessment:
    valid: bool
    reason: str | None = None


_ACTION_ONLY = {
    "微笑", "大笑", "冷笑", "苦笑", "注视", "凝视", "转身", "离开", "走近", "跑去",
    "说话", "沉默", "点头", "摇头", "倒茶", "开门", "拥抱", "回头", "皱眉", "叹气",
}
_PHRASE_PREFIXES = (
    "即便", "即使", "随后", "然后", "接着", "下一", "上一", "正在", "继续", "再次",
    "手持", "拿着", "握着", "留下", "扑向", "走向", "看着", "注视着", "望着", "将要", "开始",
)
_CHARACTER_BAD_SUFFIXES = ("淡", "笑", "纠", "说", "看", "问", "答", "道", "着", "了", "去", "来")
_CHARACTER_NARRATIVE_PREFIXES = (
    "他", "她", "它", "他们", "她们", "有人", "众人", "人群", "屏风后", "画像", "灯焰", "父命",
    "走出", "回头", "擦干", "给出", "信末", "一道",
)
_CHARACTER_NARRATIVE_MARKERS = (
    "仍然", "仍", "却", "只", "竟", "立刻", "无法", "为救", "咳着", "哭着", "笑着", "皱着",
    "趴在", "隔着", "走出", "回头", "擦干", "写着", "纠正", "身边", "桌上", "灯里",
)
_CHARACTER_NARRATIVE_SUFFIXES = ("地", "才", "却", "反", "急", "冷", "竟", "知", "写", "哭", "大")


def assess_entity_name(kind: str, value: object) -> EntityNameAssessment:
    name = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not name:
        return EntityNameAssessment(False, "名称为空")
    if len(name) > 20:
        return EntityNameAssessment(False, "名称过长，疑似整句或动作描述")
    if re.search(r"[。！？!?；;：:\n\r]", name):
        return EntityNameAssessment(False, "名称包含句子标点")
    if name.startswith(("的", "地", "得")):
        return EntityNameAssessment(False, "名称以结构助词开头，疑似文本碎片")
    if name in _ACTION_ONLY:
        return EntityNameAssessment(False, "名称是动作或表情，不是独立实体")
    if name.startswith(_PHRASE_PREFIXES):
        return EntityNameAssessment(False, "名称以动作或连接短语开头")
    if kind.upper() == "CHARACTER":
        if name.startswith(_CHARACTER_NARRATIVE_PREFIXES):
            return EntityNameAssessment(False, "人物名称以代词、动作或叙述片段开头")
        if any(marker in name for marker in _CHARACTER_NARRATIVE_MARKERS):
            return EntityNameAssessment(False, "人物名称包含动作、状态或空间叙述")
        if len(name) >= 3 and name.endswith(_CHARACTER_BAD_SUFFIXES + _CHARACTER_NARRATIVE_SUFFIXES):
            return EntityNameAssessment(False, "人物名称疑似姓名与动作/状态的粘连文本")
    if kind.upper() == "PROP" and re.search(r"(全部|中央|小小|已经|仍然|并未|没有)", name):
        return EntityNameAssessment(False, "道具名称包含叙述性修饰，疑似句子片段")
    return EntityNameAssessment(True)
