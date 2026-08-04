#!/usr/bin/env python3
"""地面大脑 → 队里那块屏。一句话，界面自己动。

跟 demo_cli.py 的区别只有一个：downlink 不再是 MockDownlink 打印，而是
StateApiDownlink 真 POST 到 `tracking/control_server.py` 的 /api/state。
说完一句话，手机页上的 Ground/Air、跟随模式、高度滑条、开始跟随会自己变，
遥测（tracker_state / fps / error）是板上 tracker 现算的。

先把队里那套跑起来（板子上 `python main.py`，或本地 rig），然后：

    python demo_live.py                          # 交互，自己打字说
    python demo_live.py --scripted                # 自动播那几句
    python demo_live.py --url http://192.168.4.1:8080   # 接真板子

⚠️ 画面里"跟谁"这一层目前落不下去：/api/state 没有 target_id 字段，
   板上 tracker 是 select_mode="largest"（锁画面里最大的人）。
   grounding 已经能挑对人（下面会打出来它挑了谁），但那个选择还传不过去 ——
   等队里在 /api/state 上加一个 target_id，一行映射就通（见 README）。
"""
from __future__ import annotations

import sys
import time

from agent import GroundAgent, Decision
from downlink import StateApiDownlink
from memory import PreferenceMemory
from perception import MockYoloSource
from telemetry import MockTelemetry
import llm

BAR = "─" * 64


def show(d: Decision, dl: StateApiDownlink) -> None:
    i = d.intent
    print(f"\n🧠 听懂了 → {i.pretty()}")
    if i.needs_confirm:
        names = []
        for tid in i.candidates:
            det = d.frame.by_id(tid)
            names.append(f"#{tid} {det.label if det else ''}")
        print(f"   ⚠️ 防翻车：{'、'.join(names)} —— 输入 'pick <id>' 点一个（不下发）")
    elif not d.sent:
        print("   （没下发）")
    if i.target_id is not None:
        det = d.frame.by_id(i.target_id)
        print(f"   🎯 grounding 挑的人：#{i.target_id} "
              f"{det.label if det else ''}   ← 这一层还传不到板子（缺 target_id 字段）")


SCRIPT = [
    "起飞跟着我，定高两米四",
    "镜头放平，正常跟拍",
    "贴地走着跟",
    "停一下",
    "返航",
]


def main() -> None:
    url = "http://localhost:8080"
    if "--url" in sys.argv:
        url = sys.argv[sys.argv.index("--url") + 1]

    dl = StateApiDownlink(base_url=url)
    try:
        state = dl.read_state()
    except Exception as e:
        raise SystemExit(f"连不上 {url}/api/state（{type(e).__name__}: {e}）\n"
                         f"先把队里那套跑起来：板上 `python main.py`，或本地 rig。")

    print(f"{BAR}\n地面大脑 → /api/state @ {url}")
    print(f"板子当前：mode={state['mode']} domain={state['domain']} "
          f"follow={state['follow_enabled']} h={state['height_m']}m "
          f"| tracker={state['tracker_state']} fps={state['fps']}")
    print(f"grounding={'LLM' if llm.available() else '规则降级（无 key）'}"
          f" · 心跳已开（不发心跳，跟随 2 秒后会被 watchdog 关掉）\n{BAR}")

    yolo = MockYoloSource("mock_data/feed_follow_then_lost.jsonl")
    tele = MockTelemetry()
    mem = PreferenceMemory()
    mem.remember_owner("穿红色卫衣的男生", "红")
    agent = GroundAgent(yolo, tele, downlink=dl, memory=mem)
    last = None

    if "--scripted" in sys.argv:
        for text in SCRIPT:
            time.sleep(1.2)
            print(f"\n{BAR}\n🗣️  「{text}」")
            d = agent.handle(text)
            last = d.intent
            show(d, dl)
        print(f"\n{BAR}\n播完。板子最终状态：{dl.last_state}")
        return

    print("对它说话（打字）。例：起飞跟着我定高两米四 / 镜头放平 / 贴地走着跟 / 停一下")
    print("命令：pick <id> · state 看板子 · q 退出")
    while True:
        try:
            text = input("\n🗣️  ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye")
            return
        if not text:
            continue
        if text.lower() in ("q", "quit", "exit"):
            return
        if text.lower() == "state":
            print("  ", dl.read_state())
            continue
        if text.lower().startswith("pick "):
            from intents import Intent
            d = agent.confirm_pick(int(text.split()[1]), last or Intent(action="follow"))
        else:
            d = agent.handle(text)
        last = d.intent
        show(d, dl)


if __name__ == "__main__":
    main()
