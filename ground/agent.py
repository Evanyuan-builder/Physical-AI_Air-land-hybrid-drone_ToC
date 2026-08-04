"""地面大脑主体 —— 把感知 / 遥测 / 语言落地 / 记忆 / 回传串起来。

一次指令的流水：
  一句话 → grounding（听懂、挑对人）→ 安全/决策层（低电返航、目标丢失搜索）→ 回传板子

「决策层」是 grounding 之外的第二个 AI 价值点，也是 pitch 里「不止是聪明遥控」的实证：
  · 电量低于阈值 → 不管你让它跟谁，先安全返航
  · 点名的目标这一帧丢了 → 不硬跟空气，转搜索
"""
from __future__ import annotations

from dataclasses import dataclass

from perception import YoloSource, DetectionFrame
from telemetry import TelemetrySource, Telemetry
from downlink import Downlink, MockDownlink
from memory import PreferenceMemory
from intents import Intent
import grounding


@dataclass
class Decision:
    intent: Intent
    frame: DetectionFrame
    tele: Telemetry
    sent: bool
    result: dict | None = None


class GroundAgent:
    def __init__(self, yolo: YoloSource, tele: TelemetrySource,
                 downlink: Downlink | None = None,
                 memory: PreferenceMemory | None = None,
                 low_battery_pct: int = 20, use_llm: bool = True):
        self.yolo = yolo
        self.tele = tele
        self.downlink = downlink or MockDownlink()
        self.memory = memory or PreferenceMemory()
        self.low_battery_pct = low_battery_pct
        self.use_llm = use_llm
        self.holding_id = None      # 当前正在跟谁（给延续规则用）

    def handle(self, text: str, auto_send: bool = True) -> Decision:
        frame = self.yolo.latest()
        tele = self.tele.latest()
        owner_id = self.memory.match_owner(frame)

        # 板子当前状态 + 上一次锁的人 —— 「镜头放平」这种只调机位的话靠它延续目标
        current = getattr(self.downlink, "last_state", None)
        intent = grounding.ground(text, frame, tele, owner_id=owner_id,
                                  use_llm=self.use_llm, current=current,
                                  holding_id=self.holding_id)
        intent = self._safety(intent, frame, tele)

        sent, result = False, None
        if auto_send and not intent.needs_confirm and intent.action != "idle":
            result = self.downlink.send(intent)
            sent = True
        self._remember_hold(intent)
        return Decision(intent=intent, frame=frame, tele=tele, sent=sent, result=result)

    def _remember_hold(self, intent: Intent) -> None:
        """记住当前跟的是谁；明确停/返航就清掉。"""
        if intent.action in ("stop", "return", "idle"):
            self.holding_id = None
        elif intent.target_id is not None:
            self.holding_id = intent.target_id

    def confirm_pick(self, target_id: int, base: Intent) -> Decision:
        """防翻车：用户在候选里点了一个，落定并下发。"""
        frame = self.yolo.latest()
        tele = self.tele.latest()
        d = frame.by_id(target_id)
        label = d.label if d else f"#{target_id}"
        intent = Intent(action=base.action if base.action != "idle" else "follow",
                        target_id=target_id, form=base.form,
                        height_m=base.height_m, camera_mode=base.camera_mode,
                        reason=f"你点选了 {label}，锁定")
        intent = self._safety(intent, frame, tele)
        result = self.downlink.send(intent) if intent.action != "idle" else None
        self._remember_hold(intent)
        return Decision(intent=intent, frame=frame, tele=tele,
                        sent=result is not None, result=result)

    # ----------------- 安全 / 决策层 -----------------
    def _safety(self, intent: Intent, frame: DetectionFrame, tele: Telemetry) -> Intent:
        # 1) 低电优先返航（压过一切跟随指令）
        if tele.battery_pct <= self.low_battery_pct and intent.action not in ("return", "idle"):
            return Intent(action="return", form="auto",
                          reason=f"电量 {tele.battery_pct}%，低于 {self.low_battery_pct}% 阈值，"
                                 f"自动返航（安全优先）")
        # 2) 要跟一个目标，但根本没锁到（丢了 / 画面里没有）→ 转搜索，不硬跟空气
        if intent.action in ("follow", "orbit", "shoot", "lock") \
                and (intent.target_id is None or intent.target_id not in frame.ids()) \
                and not intent.needs_confirm:
            # 形态/高度/取景保持不变去搜 —— 别因为丢了目标把机位也重置了
            return Intent(action="search", form=intent.form,
                          height_m=intent.height_m, camera_mode=intent.camera_mode,
                          reason="没锁到符合的目标，转入搜索，出现就接着跟")
        return intent
