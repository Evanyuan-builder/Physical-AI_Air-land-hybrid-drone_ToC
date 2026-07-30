"""语言落地（grounding）—— 整个卡位的核心那一下。

把「人的一句话」+「当前画面里的目标」翻译成「这具身体该去跟谁、用什么形态」。
这就是 demo 要让评委"哦"一下的地方：身体听懂了人。

两条路，永远有一条能走：
  1) LLM 主路：deepseek-v4-flash 读目标列表 + 用户话 → 结构化 Intent（自然语言、复杂描述都吃）
  2) 规则降级：没网/超时/没 key 时，颜色+类别+动词关键词匹配 —— 演示不因为断网翻车
"""
from __future__ import annotations

import re

from intents import Intent, coerce
from perception import DetectionFrame
from telemetry import Telemetry
import llm

SYSTEM = """你是一台两栖跟拍无人机的地面指挥大脑。用户用自然语言对你下指令，
你要把这句话翻译成结构化 JSON，决定这具会飞会走的身体去跟谁、用什么形态。

只输出一个 JSON 对象，字段：
  action: lock|follow|orbit|shoot|return|search|idle
  target_id: 整数，选中的目标 id；选不出就 null
  form: fly|walk|auto   (飞/贴地走/不指定)
  reason: 一句中文，给现场大屏，说明你为什么这么理解（口语、短）
  needs_confirm: 布尔，画面里有多个目标同样符合描述时为 true
  candidates: 数组，needs_confirm 为 true 时列出符合的多个 target_id

规则：
- "跟/跟着/锁定 X"→follow；"绕着拍/环绕"→orbit；"冲到前面抢镜头"→shoot；
  "回来/返航/回家"→return；"找不到就找/搜一下"→search
- 选 target_id 靠 类别 + 外观(颜色/描述)。如"红衣服的那个人"→找 person 且外观含红。
- "飞起来/航拍/升空"→form=fly；"贴地跟/走着跟/地面跟"→form=walk；没说→auto
- "跟我/跟着我"→若目标列表里标了 is_owner=true 就选它，否则 needs_confirm=true
- 有多个同样符合→needs_confirm=true、candidates 填多个 id，reason 里说"有N个都像，请点一下"
- 目标列表为空→action=search，reason 说明画面里暂时没目标
只输出 JSON，不要任何多余文字。"""


def _frame_for_llm(frame: DetectionFrame, owner_id) -> str:
    if not frame.detections:
        return "当前画面：（没有检测到任何目标）"
    lines = ["当前画面里的目标："]
    for d in frame.detections:
        owner = " is_owner=true" if d.target_id == owner_id else ""
        lines.append(
            f"  id={d.target_id} 类别={d.cls} 外观={d.label} 置信={d.conf:.2f}{owner}"
        )
    return "\n".join(lines)


def ground(text: str, frame: DetectionFrame, tele: Telemetry,
           owner_id=None, use_llm: bool = True) -> Intent:
    """一句话 → Intent。优先 LLM，失败降级规则。"""
    text = (text or "").strip()
    if not text:
        return Intent(action="idle", reason="没听清，请再说一次", needs_confirm=True)

    if use_llm and llm.available():
        try:
            raw = llm.chat_json(
                SYSTEM,
                f"{_frame_for_llm(frame, owner_id)}\n\n用户说：「{text}」\n\n请输出指令 JSON。",
            )
            intent = coerce(raw)
            intent = _validate_target(intent, frame)
            return intent
        except llm.LLMError:
            pass  # 降级
    return _heuristic(text, frame, owner_id)


def _validate_target(intent: Intent, frame: DetectionFrame) -> Intent:
    """LLM 有时会编一个不存在的 id —— 校正回来。"""
    ids = frame.ids()
    if intent.target_id is not None and intent.target_id not in ids:
        if len(ids) == 1:
            intent.target_id = next(iter(ids))
        else:
            intent.needs_confirm = True
            intent.candidates = sorted(ids)
            intent.reason = (intent.reason + " · 没锁准，请在画面里点一下").strip(" ·")
    return intent


# ----------------- 规则降级（无网络也能演） -----------------
_ACTION = [
    (("返航", "回来", "回家", "回去"), "return"),
    (("环绕", "绕着", "绕圈", "绕一圈"), "orbit"),
    (("抢镜", "冲到前面", "冲前面", "到前面"), "shoot"),
    (("搜", "找一下", "找找"), "search"),
    (("跟", "追", "锁定", "盯", "拍"), "follow"),
]
_COLOR = {"红": "红", "蓝": "蓝", "黑": "黑", "白": "白", "绿": "绿",
          "黄": "黄", "灰": "灰", "棕": "棕", "紫": "紫", "粉": "粉"}
_CLS = {"人": "person", "男": "person", "女": "person", "孩子": "person",
        "狗": "dog", "猫": "cat", "车": "car"}


def _heuristic(text: str, frame: DetectionFrame, owner_id) -> Intent:
    action = "follow"
    for kws, act in _ACTION:
        if any(k in text for k in kws):
            action = act
            break

    form = "auto"
    if any(k in text for k in ("飞", "航拍", "升空", "起飞")):
        form = "fly"
    elif any(k in text for k in ("贴地", "走着", "地面", "走路")):
        form = "walk"

    if action in ("return", "search"):
        return Intent(action=action, form=form,
                      reason="（规则识别）" + ("收到，返航" if action == "return" else "目标不明，先搜索"))

    if any(k in text for k in ("跟我", "跟着我", "拍我")) and owner_id is not None:
        d = frame.by_id(owner_id)
        if d:
            return Intent(action=action, target_id=owner_id, form=form,
                          reason=f"（规则识别）跟主人 {d.label}")

    # 颜色 + 类别筛选
    want_color = next((v for k, v in _COLOR.items() if k in text), None)
    want_cls = next((v for k, v in _CLS.items() if k in text), None)
    cands = frame.detections
    if want_cls:
        cands = [d for d in cands if d.cls == want_cls]
    if want_color:
        cands = [d for d in cands if d.attributes.get("color") == want_color]

    if not cands:
        return Intent(action="search", form=form,
                      reason="（规则识别）画面里没找到符合描述的目标，先搜索")
    if len(cands) == 1:
        d = cands[0]
        return Intent(action=action, target_id=d.target_id, form=form,
                      reason=f"（规则识别）锁定 {d.label}")
    # 多个符合 → 防翻车
    return Intent(action=action, form=form, needs_confirm=True,
                  candidates=[d.target_id for d in cands],
                  reason=f"（规则识别）有 {len(cands)} 个都符合，请在画面里点一下")
