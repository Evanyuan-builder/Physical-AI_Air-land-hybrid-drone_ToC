"""回传通道 —— 地面大脑把决定好的 Intent 送回板子。

✅ 2026-08-04 更新：通道不用再等人定了 —— 队里 feat/tracking 分支的
   `tracking/control_server.py` 已经把它做出来了，就是 **POST /api/state**：
       {"mode": "level"|"pitch", "domain": "ground"|"air",
        "follow_enabled": bool, "height_m": 0.5~5.0}
   回的是同一个 dict 再加上遥测（fps / tracker_state / error_x / error_y /
   target_present），字段不合法会带一个 "rejected" 列表回来。
   → 用 StateApiDownlink，本地实测跑通（见 demo_live.py）。

三条历史实现留着不删：
  A. HttpIntentDownlink —— 语义端点 POST /api/intent（板上没这个端点，未启用）
  B. MavlinkDownlink   —— MAVLink offboard，占位
  C. MockDownlink      —— 只打印，无硬件也能演
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

from intents import Intent


class Downlink:
    def send(self, intent: Intent) -> dict:
        raise NotImplementedError


class MockDownlink(Downlink):
    """不连硬件，把回传载荷打印出来（demo 大屏上能看见「下发给板子」这一下）。"""

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.log: list[dict] = []

    def send(self, intent: Intent) -> dict:
        wire = intent.to_wire()
        self.log.append(wire)
        if self.verbose:
            print(f"  ↳ 下发板子：{json.dumps(wire, ensure_ascii=False)}")
        return {"ok": True, "wire": wire}


class StateApiDownlink(Downlink):
    """把 Intent 翻成队里 control_server.py 的 POST /api/state。

    三件必须处理的真事，不处理 demo 当场就会打脸：

    1) **心跳**。他们的 FollowState 有 2 秒 watchdog（LINK_TIMEOUT_S），
       只有 POST 会刷新。地面大脑不像手机页面那样每 500ms 轮询，所以
       下发完 2 秒跟随就自己关了 —— 必须自己起心跳。

    2) **切 domain 要先关 follow**。他们前端 chooseDomain() 是
       `send({domain: x, follow_enabled: false})`，先停再让你重新点跟随。
       服务端的 apply() 不强制这条，所以如果地面大脑直接切，就会出现
       「手机上切换会停、语音切换不停」两个客户端行为不一致。跟前端保持一致。

    3) **有些 action 板子这边没有对应动作**。接口只有四个字段，
       return / orbit / shoot 落不下去 —— 这种情况如实返回 unsupported，
       不许当成下发成功。
    """

    # Intent.action → 这个接口能表达的部分
    _FOLLOW_ON = {"follow", "lock", "search"}   # search: 板上 tracker 自己在找，跟随保持开
    _FOLLOW_OFF = {"stop", "idle"}
    _UNSUPPORTED = {"return", "orbit", "shoot"}  # 接口里没有，只能停下并说清楚

    def __init__(self, base_url: str = "http://localhost:8080",
                 heartbeat: bool = True, timeout: float = 3.0,
                 verbose: bool = True):
        self.base_url = base_url.rstrip("/")
        self.endpoint = f"{self.base_url}/api/state"
        self.timeout = timeout
        self.verbose = verbose
        self.last_state: dict | None = None
        self._hb_stop = threading.Event()
        self._hb: threading.Thread | None = None
        if heartbeat:
            self.start_heartbeat()

    # ---------- 心跳：不发它，跟随 2 秒后自己断 ----------
    def start_heartbeat(self, period_s: float = 0.5) -> None:
        if self._hb and self._hb.is_alive():
            return

        def beat():
            while not self._hb_stop.wait(period_s):
                try:
                    self._post({})          # 空命令 = 纯保活，跟他们前端 poll() 一样
                except Exception:
                    pass                    # 板子掉线不该把大脑打死

        self._hb = threading.Thread(target=beat, daemon=True)
        self._hb.start()

    def stop_heartbeat(self) -> None:
        self._hb_stop.set()

    # ---------- Intent → patch ----------
    def to_patch(self, intent: Intent) -> dict:
        patch: dict = {}

        if intent.form == "fly":
            patch["domain"] = "air"
        elif intent.form == "walk":
            patch["domain"] = "ground"

        if intent.camera_mode in ("level", "pitch"):
            patch["mode"] = intent.camera_mode
        if intent.height_m is not None:
            patch["height_m"] = float(intent.height_m)
            # 定了高度就是要定高俯拍 —— 他们的 level 模式会把滑条置灰不吃高度
            patch.setdefault("mode", "pitch")

        if intent.action in self._FOLLOW_ON:
            patch["follow_enabled"] = True
        elif intent.action in self._FOLLOW_OFF or intent.action in self._UNSUPPORTED:
            patch["follow_enabled"] = False

        return patch

    def send(self, intent: Intent) -> dict:
        patch = self.to_patch(intent)
        note = ""

        # 跟他们前端一致：换形态先停跟随，再按 intent 决定要不要重新开
        if "domain" in patch and self.last_state \
                and patch["domain"] != self.last_state.get("domain"):
            self._post({"domain": patch["domain"], "follow_enabled": False})
            note = f"（换形态到 {patch['domain']}，按前端约定先停跟随再重开）"

        state = self._post(patch)
        self.last_state = state

        ok = True
        problems = []
        if state.get("rejected"):
            ok = False
            problems.append(f"板子拒收字段：{state['rejected']}")
        # clamp 检查：他们对 height 是夹不是拒，光看 rejected 看不出来
        if intent.height_m is not None:
            got = state.get("height_m")
            if got is not None and abs(got - intent.height_m) > 0.051:
                problems.append(f"高度被板子夹到 {got}m（我要的是 {intent.height_m}m）")
        if intent.action in self._UNSUPPORTED:
            ok = False
            problems.append(
                f"'{intent.action}' 这个动作 /api/state 没有对应字段 —— "
                f"已停跟随，但**没有真的执行 {intent.action}**")

        if self.verbose:
            print(f"  ↳ POST /api/state {json.dumps(patch, ensure_ascii=False)}{note}")
            print(f"     板子回：mode={state.get('mode')} domain={state.get('domain')} "
                  f"follow={state.get('follow_enabled')} h={state.get('height_m')}m "
                  f"| tracker={state.get('tracker_state')} "
                  f"target_present={state.get('target_present')} fps={state.get('fps')}")
            for p in problems:
                print(f"     ⚠️ {p}")

        return {"ok": ok, "patch": patch, "state": state, "problems": problems}

    def _post(self, patch: dict) -> dict:
        data = json.dumps(patch).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint, data=data,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def read_state(self) -> dict:
        with urllib.request.urlopen(f"{self.endpoint}", timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))


class HttpIntentDownlink(Downlink):
    """候选 A：POST 到板上语义端点。板上没有这个端点，留着不删。"""

    def __init__(self, endpoint: str = "http://192.168.4.1/api/intent"):
        self.endpoint = endpoint

    def send(self, intent: Intent) -> dict:
        import urllib.request
        data = json.dumps(intent.to_wire()).encode("utf-8")
        req = urllib.request.Request(self.endpoint, data=data,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8"))


class MavlinkDownlink(Downlink):
    """候选 B：MAVLink offboard 发高层 setpoint。占位。"""

    def __init__(self, conn: str):
        self.conn = conn

    def send(self, intent: Intent) -> dict:
        raise NotImplementedError("接 MAVLink offboard —— 跟徐梓阳定通道后填")
