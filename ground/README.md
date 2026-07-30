# 地面端大脑（ground）

两栖跟拍机的「听懂人、挑对人、下发身体」这一层。跑在地面笔记本，**完全不占那块 1GB 板子**，不动板上跟随闭环。

这是骁翰卡位的那 30 秒 demo beat 的代码骨架：

> 对它说「跟穿红衣服的那个人」→ 它锁定那个人 → 把指令下发给板子。

## 现在就能跑（无硬件、无网也行）

```bash
cd ground
python3 demo_cli.py --scripted   # 自动播 30 秒 beat（现场彩排/录屏）
python3 demo_cli.py              # 交互：自己打字对它说话
```

grounding 有 key 时走 LLM（deepseek-v4-flash），没网/没 key 自动降级成规则匹配 —— **演示不会因为断网翻车**。

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
| `downlink.py` | 回传板子 | **跟徐梓阳定的唯一那条线** → `HttpIntentDownlink` / `MavlinkDownlink` |
| `agent.py` | 串起来 + 安全/决策层 | — |
| `intents.py` | 指令契约（回传 schema） | — |

## 接真机只差两样（等徐梓阳）

1. **样例 RTSP 录像 + 一份 YOLO 输出 JSON** → 换掉 `mock_data/`，`MockYoloSource` 改 `RtspYoloSource`。
2. **回传通道二选一**（他定，他最清楚板上）：
   - A. 板上 HTTP 加语义端点 `POST /api/intent`（倾向，解耦干净不碰飞控）
   - B. 走 MAVLink offboard 发高层 setpoint

上层 `agent.py` 一行都不用改 —— 数据源和回传都是可换实现的接口。

> mock 数据里的目标外观（颜色/caption）在真机是「抠 bbox 取主色 / 小 VLM」补出来的，见 `perception.enrich()`。
