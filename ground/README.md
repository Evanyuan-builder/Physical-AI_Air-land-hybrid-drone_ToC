# 地面端大脑（ground）

两栖跟拍机的「听懂人、挑对人、下发身体」这一层。跑在地面笔记本，**完全不占那块 1GB 板子**，不动板上跟随闭环。

这是骁翰卡位的那 30 秒 demo beat 的代码骨架：

> 对它说「跟穿红衣服的那个人」→ 它锁定那个人 → 把指令下发给板子。

## 现在就能跑（无硬件、无网也行）

```bash
cd ground
python3 demo_cli.py --scripted   # 自动播 30 秒 beat（纯打印，不连任何东西）
python3 demo_cli.py              # 交互：自己打字对它说话

# ↓ 真接队里那块屏（先让 tracking/main.py 或本地 rig 跑起来）
python3 demo_live.py --scripted
python3 demo_live.py --url http://<板子IP>:8080
```

grounding 有 key 时走 LLM，没网/没 key 自动降级成规则匹配 —— **演示不会因为断网翻车**。

## ✅ 回传通道已经接上了（2026-08-04）

不用再等谁定接口 —— 队里 `feat/tracking` 分支的 `tracking/control_server.py`
已经把它做出来了，就是 **`POST /api/state`**：

```json
{"mode": "level|pitch", "domain": "ground|air",
 "follow_enabled": true, "height_m": 0.5~5.0}
```

回的是同一个 dict 加上遥测（`fps` / `tracker_state` / `error_x` / `error_y` /
`target_present`）。`downlink.StateApiDownlink` 就是接它的，**本地端到端实测跑通**：

| 说的话 | 打进去的 patch | 那块屏上发生什么 |
|---|---|---|
| 起飞跟着我，定高两米四 | `domain=air, mode=pitch, height_m=2.4, follow=true` | 切空中、定高俯拍、滑条到 2.4m、开始跟随 |
| 镜头放平，正常跟拍 | `mode=level, follow=true` | 切云台锁平，**继续跟同一个人** |
| 贴地走着跟 | `domain=ground, follow=true` | 切地面模式（先停跟随再重开，跟前端一致） |
| 停一下 | `follow=false` | 停止跟随 |

接线时踩到的三件必须处理的事（都在 `StateApiDownlink` 的注释里）：

1. **心跳**。他们有 2 秒 watchdog（`LINK_TIMEOUT_S`），只有 POST 会刷新。地面大脑
   不像手机页面那样 500ms 轮询，不自己发心跳的话**下发完 2 秒跟随就自己断**。
   实测对照：不开心跳 → 3 秒后 `follow=false`；开心跳 → 一直 `true`；停掉心跳 → 又变 `false`。
2. **切 domain 要先关 follow**，跟他们前端 `chooseDomain()` 的做法保持一致，
   否则会出现「手机上切换会停、语音切换不停」两个客户端行为不一样。
3. **`return` / `orbit` / `shoot` 这个接口表达不了**（只有四个字段）。这种情况如实
   返回 unsupported 并停跟随，**不许当成下发成功**。

### 还缺的一个字段：`target_id`

这个接口没有「跟谁」的入口，板上 tracker 是 `select_mode="largest"`（锁画面里
**最大**的那个人）。所以 grounding 已经能从一句「跟穿红衣服的那个人」里挑对人，
但那个选择**目前传不过去**。

需要队里加的最小改动 —— `FollowState` 加一个字段、`apply()` 里跟着收一下，
tracker 那边在 `_select_target` 之前先看有没有外部指定的目标。加完这一个字段，
`downlink.to_patch()` 里一行映射就通（`patch["target_id"] = intent.target_id`）。

## 一次指令的流水

```
一句话 → grounding(听懂+挑对人) → 安全/决策层 → 回传板子
        LLM 主路 / 规则降级       低电返航·丢失搜索   Mock/HTTP/MAVLink
```

三个 AI 价值点，对应 pitch 里「不止是聪明遥控」：
1. **语言落地**（grounding.py）：一句话 → 该跟谁、用什么形态。
2. **偏好记忆**（memory.py）：记得主人、记得习惯机位。「跟我」直接锁对人。
3. **决策**（agent.py 安全层）：电量低强制返航、目标丢了转搜索。

## 模块

| 文件 | 干什么 | 真实数据接哪 |
|---|---|---|
| `perception.py` | YOLO 检测帧 | 徐梓阳的 RTSP + YOLO 输出 → `RtspYoloSource` |
| `telemetry.py` | 遥测（电量/高度/形态） | 板上 MAVLink → `MavlinkTelemetry` |
| `grounding.py` | 一句话 → Intent（核心） | 无需硬件 |
| `memory.py` | 主人/习惯偏好 | 接 Memory Core |
| `downlink.py` | 回传板子 | ✅ `StateApiDownlink` → 队里的 `POST /api/state` |
| `agent.py` | 串起来 + 安全/决策层 | — |
| `intents.py` | 指令契约 | — |
| `demo_live.py` | 真接那块屏的 demo | — |

## 接真机还差一样

**样例 RTSP/MJPEG 录像 + 一份 YOLO 输出 JSON** → 换掉 `mock_data/`，
`MockYoloSource` 改成真的源。上层 `agent.py` 一行不用改。

⚠️ 注意板上的图传已经从 RTSP 换成 **MJPEG** 了（`tracking/main.py`：RTSP 延迟没法修），
`perception.py` 里那些「rtsp://…:8554/live」的注释是 7/26 的旧路线，要跟着改。

> mock 数据里的目标外观（颜色/caption）在真机是「抠 bbox 取主色 / 小 VLM」补出来的，见 `perception.enrich()`。
