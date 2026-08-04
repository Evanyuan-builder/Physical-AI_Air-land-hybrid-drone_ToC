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
  action: lock|follow|orbit|shoot|return|search|stop|idle
  target_id: 整数，选中的目标 id；选不出就 null
  form: fly|walk|auto   (飞/贴地走/不指定)
  height_m: 数字，用户说了具体飞行高度才填（单位米，0.5~5.0）；没说就 null
  camera_mode: level|pitch|auto
  reason: 一句中文，给现场大屏，说明你为什么这么理解（口语、短）
  needs_confirm: 布尔，画面里有多个目标同样符合描述时为 true
  candidates: 数组，needs_confirm 为 true 时列出符合的多个 target_id

规则：
- "跟/跟着/锁定 X"→follow；"绕着拍/环绕"→orbit；"冲到前面抢镜头"→shoot；
  "回来/返航/回家"→return；"找不到就找/搜一下"→search；
  "停/停一下/别跟了/停下"→stop（明确要停，跟 idle 不一样）
- 🔴**延续规则**：**只有**「板子当前状态」里明确写了正在跟的那个 id 时才适用 ——
  这时用户如果只是在调机位（放平/俯拍/高一点/贴地走/飞起来），那就是**对同一个人**说的 →
  action 仍然是 follow，target_id 沿用那个 id，不要判成 idle、也不要因为"没说跟谁"就 needs_confirm。
  只有用户明确换人时才换 target_id。
- 🔴**没有正在跟的人时，防翻车优先**：这种情况下用户说"跟着那个人"这类没指明是谁的话，
  而画面里有多个目标符合 → **必须** needs_confirm=true 并把符合的都列进 candidates，
  **不许自己挑一个**。挑错人比多问一句的代价大得多。
- 听不懂、或者跟控制无关的话 → idle（idle 不会下发任何指令）
- 选 target_id 靠 类别 + 外观(颜色/描述)。如"红衣服的那个人"→找 person 且外观含红。
- "飞起来/航拍/升空"→form=fly；"贴地跟/走着跟/地面跟"→form=walk；没说→auto
- 高度：「飞两米」「升到三米半」→height_m=2 / 3.5；「高一点」→null（不猜具体数）
- camera_mode：说了具体高度、或"定高/俯拍/从上往下"→pitch；
  "镜头放平/平视/正常跟拍"→level；没提→auto
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


def _current_for_llm(current: dict | None, holding_id) -> str:
    """把板子当前状态摊给模型 —— 「镜头放平」这种话不给上下文就会被判成没头没尾。"""
    if not current:
        return "板子当前状态：（读不到）"
    dom = {"air": "空中", "ground": "地面"}.get(current.get("domain"), "?")
    mode = {"pitch": "定高俯拍", "level": "云台锁平"}.get(current.get("mode"), "?")
    following = current.get("follow_enabled")
    lines = [
        "板子当前状态：",
        f"  形态={dom}  取景={mode}  高度={current.get('height_m')}m  "
        f"跟随={'开' if following else '关'}",
        f"  板上 tracker={current.get('tracker_state')} "
        f"画面里有目标={current.get('target_present')}",
    ]
    if holding_id is not None:
        lines.append(f"  🎯 现在正在跟的是 id={holding_id} —— 只调机位的话就还跟他")
    else:
        # ⚠️ 板子的 follow_enabled 是持久的（上一轮留下的），不等于「地面这边知道在跟谁」。
        # 不写清楚这一句，模型看到「跟随=开」就会以为已经锁了人，从而跳过防翻车点选。
        lines.append("  🎯 地面这边现在**不知道在跟谁**（没有已锁定的目标）"
                     "—— 上面那个「跟随」开关只是板子的遗留状态，不能当成已经锁了人。"
                     "所以延续规则在这一轮不适用。")
    return "\n".join(lines)


def ground(text: str, frame: DetectionFrame, tele: Telemetry,
           owner_id=None, use_llm: bool = True,
           current: dict | None = None, holding_id=None) -> Intent:
    """一句话 → Intent。优先 LLM，失败降级规则。

    current   = 板子 /api/state 回的当前状态（没有就传 None）
    holding_id = 上一条指令锁定的 target_id，用于「镜头放平」这类只调机位的话延续目标
    """
    text = (text or "").strip()
    if not text:
        return Intent(action="idle", reason="没听清，请再说一次", needs_confirm=True)

    if use_llm and llm.available():
        try:
            raw = llm.chat_json(
                SYSTEM,
                f"{_frame_for_llm(frame, owner_id)}\n\n"
                f"{_current_for_llm(current, holding_id)}\n\n"
                f"用户说：「{text}」\n\n请输出指令 JSON。",
            )
            intent = coerce(raw)
            intent = _validate_target(intent, frame)
            return intent
        except llm.LLMError:
            pass  # 降级
    return _heuristic(text, frame, owner_id, holding_id)


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
    (("停一下", "停下", "别跟了", "停止", "先停"), "stop"),
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


_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "半": 0.5, "零": 0}


def _parse_height(text: str):
    """从话里抠出高度。断网时也得能听懂「飞到两米半」。"""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:米|m|M)", text)
    if m:
        return float(m.group(1))
    m = re.search(r"([一二两三四五])米(半)?", text)
    if m:
        v = float(_CN_NUM[m.group(1)])
        return v + 0.5 if m.group(2) else v
    return None


def _heuristic(text: str, frame: DetectionFrame, owner_id, holding_id=None) -> Intent:
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

    height = _parse_height(text)
    cam = "auto"
    if height is not None or any(k in text for k in ("定高", "俯拍", "从上往下")):
        cam = "pitch"
    elif any(k in text for k in ("放平", "平视", "正常跟")):
        cam = "level"
    if height is not None and form == "auto":
        form = "fly"          # 说了高度就是要飞

    base = dict(form=form, height_m=height, camera_mode=cam)

    if action in ("return", "search", "stop"):
        why = {"return": "收到，返航", "search": "目标不明，先搜索",
               "stop": "收到，停止跟随"}[action]
        return Intent(action=action, reason="（规则识别）" + why, **base)

    # 只调机位、没提跟谁 → 延续当前正在跟的那个人（对上 LLM 那条延续规则）
    only_gesture = not any(k in text for k in list(_COLOR) + list(_CLS) + ["我"])
    if only_gesture and holding_id is not None and frame.by_id(holding_id):
        d = frame.by_id(holding_id)
        return Intent(action="follow", target_id=holding_id,
                      reason=f"（规则识别）只调机位，还跟着 {d.label}", **base)

    if any(k in text for k in ("跟我", "跟着我", "拍我")) and owner_id is not None:
        d = frame.by_id(owner_id)
        if d:
            return Intent(action=action, target_id=owner_id,
                          reason=f"（规则识别）跟主人 {d.label}", **base)

    # 颜色 + 类别筛选
    want_color = next((v for k, v in _COLOR.items() if k in text), None)
    want_cls = next((v for k, v in _CLS.items() if k in text), None)
    cands = frame.detections
    if want_cls:
        cands = [d for d in cands if d.cls == want_cls]
    if want_color:
        cands = [d for d in cands if d.attributes.get("color") == want_color]

    if not cands:
        return Intent(action="search",
                      reason="（规则识别）画面里没找到符合描述的目标，先搜索", **base)
    if len(cands) == 1:
        d = cands[0]
        return Intent(action=action, target_id=d.target_id,
                      reason=f"（规则识别）锁定 {d.label}", **base)
    # 多个符合 → 防翻车
    return Intent(action=action, needs_confirm=True,
                  candidates=[d.target_id for d in cands],
                  reason=f"（规则识别）有 {len(cands)} 个都符合，请在画面里点一下", **base)
