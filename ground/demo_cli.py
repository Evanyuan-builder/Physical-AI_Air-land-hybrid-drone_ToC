#!/usr/bin/env python3
"""地面端 demo —— 命令行版，无硬件、无网也能跑。

两种用法：
  python demo_cli.py            # 交互：自己对它说话（打字），看它听懂、挑人、下发
  python demo_cli.py --scripted # 自动播那 30 秒 beat（现场彩排/录屏用）

现场那句就是：对它说「跟穿红衣服的那个人」→ 看它锁定 #1 → 下发板子。
"""
from __future__ import annotations

import sys
import time

from perception import MockYoloSource
from telemetry import MockTelemetry
from downlink import MockDownlink
from memory import PreferenceMemory
from agent import GroundAgent, Decision
import llm

BAR = "─" * 58


def show_scene(agent: GroundAgent) -> None:
    frame = agent.yolo.latest()
    tele = agent.tele.latest()
    print(f"\n📷 画面（RTSP + YOLO）    🔋 电量 {tele.battery_pct}%  "
          f"形态 {'✈️ 飞' if tele.airborne else '🚗 地面'}")
    print(frame.describe())


def show_decision(d: Decision) -> None:
    i = d.intent
    print(f"\n🧠 听懂了 → {i.pretty()}")
    if i.needs_confirm:
        names = []
        for tid in i.candidates:
            det = d.frame.by_id(tid)
            names.append(f"#{tid} {det.label if det else ''}")
        print(f"   ⚠️ 防翻车：{'、'.join(names)} —— 输入 'pick <id>' 点一个")
    elif d.sent:
        print("   ✅ 已下发板子")
    else:
        print("   （未下发）")


def scripted(agent: GroundAgent) -> None:
    """30 秒 beat 彩排：听懂 → 防翻车点选 → 换形态 → 目标丢失搜索 → 低电返航。"""
    steps = [
        ("跟穿红衣服的那个人", None),
        ("跟着那个人",         None),   # 三个 person，触发防翻车
        ("__pick__",           2),      # 现场点了蓝衣女生
        ("贴地跟着那只狗",      None),   # 换形态 walk + 跟狗
        ("__advance__",        None),   # 画面推进，红衣男生走出画面
        ("跟红衣服那个",        None),   # 目标丢了 → 搜索
        ("__lowbat__",         None),   # 电量掉到 12%
        ("继续跟那只狗",        None),   # 低电 → 强制返航
    ]
    show_scene(agent)
    for text, arg in steps:
        time.sleep(0.8)
        if text == "__advance__":
            agent.yolo.advance(); agent.yolo.advance()
            print(f"\n{BAR}\n（画面变化：红衣男生走出镜头）")
            show_scene(agent)
            continue
        if text == "__lowbat__":
            agent.tele.set(battery_pct=12)
            print(f"\n{BAR}\n（电量掉到 12%）")
            continue
        if text == "__pick__":
            print(f"\n{BAR}\n🗣️  （在画面里点了 #{arg}）")
            show_decision(agent.confirm_pick(arg, agent._last))
            continue
        print(f"\n{BAR}\n🗣️  「{text}」")
        d = agent.handle(text)
        agent._last = d.intent
        show_decision(d)
    print(f"\n{BAR}\n彩排结束。")


def interactive(agent: GroundAgent) -> None:
    print("对无人机说话（打字）。例：跟穿红衣服的那个人 / 贴地跟那只狗 / 环绕那个蓝衣服的 / 返航")
    print("命令：pick <id> 点选 · scene 看画面 · next 推进画面 · bat <n> 设电量 · q 退出")
    show_scene(agent)
    while True:
        try:
            text = input("\n🗣️  ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye"); return
        if not text:
            continue
        low = text.lower()
        if low in ("q", "quit", "exit"):
            return
        if low == "scene":
            show_scene(agent); continue
        if low == "next":
            agent.yolo.advance(); show_scene(agent); continue
        if low.startswith("bat "):
            agent.tele.set(battery_pct=int(low.split()[1])); show_scene(agent); continue
        if low.startswith("pick "):
            show_decision(agent.confirm_pick(int(low.split()[1]), getattr(agent, "_last", None) or _idle())); continue
        d = agent.handle(text)
        agent._last = d.intent
        show_decision(d)


def _idle():
    from intents import Intent
    return Intent(action="follow", form="auto")


def build_agent() -> GroundAgent:
    yolo = MockYoloSource("mock_data/feed_follow_then_lost.jsonl")
    tele = MockTelemetry()
    mem = PreferenceMemory()
    mem.remember_owner("穿红色卫衣的男生", "红")   # demo：假设主人=红衣男生，用于「跟我」
    agent = GroundAgent(yolo, tele, downlink=MockDownlink(), memory=mem, use_llm=True)
    agent._last = _idle()
    mode = "LLM（deepseek-v4-flash）" if llm.available() else "规则降级（无 key）"
    print(f"{BAR}\n两栖跟拍机 · 地面大脑 demo    grounding={mode}")
    return agent


if __name__ == "__main__":
    a = build_agent()
    if "--scripted" in sys.argv:
        scripted(a)
    else:
        interactive(a)
