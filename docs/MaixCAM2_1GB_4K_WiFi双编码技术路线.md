# MaixCAM2 1GB 4K 视觉与 Wi‑Fi 图传技术方案

## 1. 项目目标

使用 MaixCAM2 1GB 4K 版本实现以下功能：

- 4K 摄像头采集；
- 4K 视频保存到板载 eMMC；
- 720p 视频通过 MaixCAM2 自带 Wi‑Fi 回传到电脑；
- YOLO 使用 `448×448` 输入进行目标识别；
- MaixCAM2 通过 UART 与 PX4 飞控通信；
- 电脑端可以查看实时画面，并控制本地录像开始和停止。

## 2. 系统结构

```text
4K摄像头
    │
    ▼
MaixCAM2 1GB
    ├── 4K H.265编码 ──> eMMC本地录像
    ├── 720p H.264编码 ──> 自带Wi-Fi ──> 地面电脑
    ├── 448×448图像 ──> YOLO目标识别
    └── UART/MAVLink ──> PX4飞控
```

地面电脑负责：

- 接收和显示 720p 实时视频；
- 查看目标识别结果；
- 发送开始录像、停止录像和状态查询命令；
- 调试 MaixCAM2 程序；
- 查看 PX4 飞行状态。

## 3. 硬件配置

```text
主控：MaixCAM2 1GB
摄像头：MaixCAM2官方适配4K摄像头
存储：板载eMMC
无线通信：MaixCAM2板载Wi-Fi
飞控：PX4兼容飞控
飞控通信：UART + MAVLink
供电：独立稳定5V电源
```

MaixCAM2 和飞控连接：

```text
MaixCAM2 UART_TX ──> PX4 TELEM_RX
MaixCAM2 UART_RX <── PX4 TELEM_TX
MaixCAM2 GND     <──> PX4 GND
```

建议初始串口波特率：

```text
115200
```

## 4. 网络连接

推荐让 MaixCAM2 和地面电脑连接同一个 5GHz Wi‑Fi 网络。

### 方案：MaixCAM2开启热点

```text
MaixCAM2热点
    ))) Wi-Fi (((
地面电脑
```

该方案适合近距离测试。正式使用更推荐外接天线条件较好的 5GHz 机载路由器。

## 5. 视频处理路线

目标视频管线：

```text
4K摄像头原始画面
        │
        ├── 主通道：4K NV21
        │       └── H.265编码
        │               └── eMMC录像
        │
        ├── 辅通道：1280×720 NV21
        │       └── H.264编码
        │               └── Wi-Fi视频回传
        │
        └── AI通道：448×448 RGB
                └── YOLO推理
```

不能先把 4K 视频编码后再简单降低码率得到 720p。4K 本地录像和 720p 图传是两种不同分辨率、不同码率的视频流，需要两个独立编码通道。

## 6. 双编码实施方案

正式程序使用 MaixCDK/C++ 或底层视频 SDK 实现：

```text
VENC0：
4K
H.265
写入eMMC

VENC1：
1280×720
H.264
通过Wi-Fi推流
```

推荐初始参数：

### 本地录像

```text
分辨率：摄像头实际4K分辨率
帧率：20fps
编码：H.265
码率：10～12Mbps
录像分段：5分钟
存储位置：eMMC
```

### Wi‑Fi图传

```text
分辨率：1280×720
帧率：20fps
编码：H.264
码率：1.5～2Mbps
GOP：20
```

### YOLO

```text
输入尺寸：448×448
模型：YOLO轻量量化模型
推理频率：15～20fps
处理方式：只处理最新帧
```

稳定后再逐步增加录像和图传帧率。

## 7. 双编码验证要求

MaixPy 高层接口不用于最终双编码程序。开发时先确认当前系统镜像和底层 SDK 是否允许同时创建两个硬件编码通道。

验证顺序：

1. 单独运行 4K H.265 录像；
2. 单独运行 720p H.264 图传；
3. 同时创建 VENC0 和 VENC1；
4. 检查两路编码是否正常输出；
5. 加入 448×448 YOLO；
6. 连续运行至少 1 小时；
7. 检查内存、温度、掉帧和录像文件完整性。

如果第二路硬件编码接口不可用，则不能仅通过 MaixPy 强行实现双编码。

## 8. Wi‑Fi视频回传

双编码成功后，720p H.264 视频使用标准网络流传输。

推荐使用：

```text
RTSP
```

视频地址示例：

```text
rtsp://192.168.30.10:8554/live
```

电脑端可使用 VLC、ffplay、QGroundControl 或自研地面端程序查看。

ffplay 测试命令：

```bash
ffplay -fflags nobuffer \
       -flags low_delay \
       -framedrop \
       rtsp://192.168.30.10:8554/live
```

Wi‑Fi网络拥塞时允许主动丢弃图传帧，但不得阻塞本地录像、YOLO和飞控通信。

## 9. YOLO处理流程

```text
摄像头AI通道
    │
    ▼
缩放到448×448
    │
    ▼
YOLO推理
    │
    ▼
目标筛选和跟踪
    │
    ├── 目标框发送到图传显示
    └── 目标误差通过MAVLink发送给PX4
```

输出数据：

```text
目标是否存在
目标类别
目标置信度
目标中心坐标
目标框宽度和高度
目标编号
图像时间戳
```

归一化误差：

```text
error_x = (目标中心X - 图像中心X) / 图像半宽
error_y = (目标中心Y - 图像中心Y) / 图像半高
```

## 10. 飞控通信

MaixCAM2 使用 UART + MAVLink 与 PX4 通信。

PX4 推荐参数：

```text
MAV_1_CONFIG  = TELEM2
MAV_1_MODE    = Onboard
SER_TEL2_BAUD = 115200
```

MaixCAM2 向 PX4 发送：

- 目标有效状态；
- 目标中心误差；
- 目标距离；
- 目标置信度；
- 速度或偏航设定值。

PX4 向 MaixCAM2 返回：

- 飞行模式；
- 姿态；
- 高度；
- 本地位置；
- 电池状态；
- 解锁状态。

MaixCAM2 不直接控制电机 PWM，最终控制由 PX4 内部控制器完成。

## 11. 远程录像控制

MaixCAM2 启动 HTTP 控制服务。

接口：

```text
POST /api/record/start
POST /api/record/stop
GET  /api/record/status
GET  /api/storage/status
GET  /api/record/files
```

访问地址示例：

```text
http://192.168.30.10:8000
```

电脑端开始录像：

```bash
curl -X POST http://192.168.30.10:8000/api/record/start
```

停止录像：

```bash
curl -X POST http://192.168.30.10:8000/api/record/stop
```

录像启停只控制 VENC0 编码数据是否写入 eMMC，不关闭摄像头、YOLO和图传。

## 12. 软件模块

```text
src/
├── camera_pipeline.cpp
├── record_encoder.cpp
├── stream_encoder.cpp
├── rtsp_server.cpp
├── yolo_worker.cpp
├── mavlink_manager.cpp
├── record_control.cpp
├── storage_manager.cpp
├── network_manager.cpp
├── watchdog.cpp
└── main.cpp
```

模块功能：

- `camera_pipeline`：初始化摄像头和三路图像通道；
- `record_encoder`：4K H.265编码和eMMC录像；
- `stream_encoder`：720p H.264编码；
- `rtsp_server`：Wi‑Fi视频输出；
- `yolo_worker`：448×448目标识别；
- `mavlink_manager`：与PX4通信；
- `record_control`：远程录像控制；
- `storage_manager`：录像分段和容量管理；
- `network_manager`：Wi‑Fi状态和断线恢复；
- `watchdog`：内存、温度和程序异常监控。

## 13. 任务优先级

```text
最高优先级：PX4通信和安全状态
高优先级：4K本地录像
中优先级：YOLO识别
低优先级：720p Wi-Fi图传
```

当 Wi‑Fi信号差或网络拥堵时：

```text
允许图传降低帧率或丢帧
不允许阻塞4K录像
不允许阻塞YOLO
不允许阻塞飞控通信
```

## 14. 回退方案

如果 MaixCAM2 1GB 无法稳定同时运行双编码和YOLO，则回退为：

```text
4K H.265 ──> eMMC本地录像
448×448 ──> YOLO识别
低分辨率图像 ──> MaixVision调试预览
UART/MAVLink ──> PX4
```

回退方案不使用第二路标准 H.264 编码，仅提供低帧率调试画面。

另一种回退方式是使用单一码流：

```text
1080p H.264
    ├── 本地录像
    └── Wi-Fi图传
```

## 15. 实施步骤

1. 完成4K摄像头和eMMC录像测试；
2. 完成720p H.264单路图传测试；
3. 验证底层双VENC通道；
4. 完成双编码同时运行；
5. 加入448×448 YOLO；
6. 加入UART/MAVLink；
7. 加入HTTP录像控制；
8. 完成Wi‑Fi断线恢复；
9. 连续满载运行1小时；
10. 完成静态、电机工作和系留飞行测试。

## 16. 最终配置

```text
设备：MaixCAM2 1GB 4K版
存储：板载eMMC
AI输入：448×448
本地录像：4K H.265，20fps，10～12Mbps
Wi-Fi图传：720p H.264，20fps，1.5～2Mbps
飞控通信：UART + MAVLink，115200
录像控制：HTTP接口
软件实现：MaixCDK/C++双编码
回退预览：MaixVision低分辨率预览
```

最终路线：

```text
4K摄像头
    │
    ▼
MaixCAM2 1GB
    ├── VENC0：4K H.265 ──> eMMC录像
    ├── VENC1：720p H.264 ──> Wi-Fi图传
    ├── 448×448 ──> YOLO识别
    └── UART/MAVLink ──> PX4飞控
```
