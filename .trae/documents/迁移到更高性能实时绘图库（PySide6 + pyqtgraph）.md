## 技术栈选择
- 使用 `PySide6`（Qt）作为 GUI 框架；使用 `pyqtgraph` 进行高性能实时绘制
- 保留现有串口采集与CSV/HTTP控制逻辑，复用 `ArduinoDataReader` 与 `STM32DataReader` 线程
- 目标：在窗口拖拽/缩放时保持流畅；每秒多次绘制不阻塞主线程；降低坐标轴重算与布局开销

## 依赖与兼容
- 依赖：`PySide6`、`pyqtgraph>=0.13`、保留现有 `numpy`、`pyserial`
- Windows 环境下通过 `pip` 安装，不改变现有工程结构；初期保留原脚本以便回滚

## 总体架构
- 用一个 Qt 主窗口替代 Tk：上半区 Arduino 图， 下半区 STM32 图；顶部工具栏保留 `port/no-csv/delay/csv_dir/stm32_port`、连接/开始/停止按钮
- 绘图刷新用 `QTimer` 调度；窗口交互通过 Qt 事件过滤器实现防抖暂停与恢复
- 数据缓冲：使用 `deque`/`numpy` 环形缓冲，避免频繁扩容与复制

## UI 布局
- 顶部：`QWidget + QGridLayout` 放置输入框与按钮；与 Tk 当前控件一一对应（`realtime_plot_ui.py:565-600`）
- 中部：两个 `pyqtgraph.PlotWidget`，分别用于 Arduino 与 STM32 实时图；支持 `plotItem` 的图例、网格与坐标设置

## 数据采集复用
- 保留 `ArduinoDataReader` 与 `STM32DataReader` 的线程模型与队列接口（`data_queue`、`buffers` 等），不改采集协议
- 在 Qt 中用 `QTimer` 周期性拉取队列数据并刷新曲线，替代 `FuncAnimation`（`realtime_plot_ui.py:434-437`、`488-491`）

## 绘图实现（Arduino）
- 模拟量曲线：用 `PlotDataItem` 更新 `x=timestamps, y=val1`；固定 y 轴 `[0,1023]`
- 事件可视化：
  - 优先方案：用 `ScatterPlotItem` 在事件发生时间画顶端标记（轻量）；或用极窄 `BarGraphItem` 作“竖线”效果
  - 如需真实垂线：使用少量 `InfiniteLine`（事件不密集时）；事件多时切换为散点方案以避免对象过多
- X 轴：固定滑动窗口宽度（如最近 `N` 秒），仅在越界时平移窗口，避免每帧 `setXRange`（替代 `realtime_plot_ui.py:428` 的每帧更新）

## 绘图实现（STM32）
- 10 路曲线：预先创建 10 个 `PlotDataItem`，每次 `setData(x=fixed_range, y=buffer[i])`
- 轴范围：固定 `x=[0,512]`、`y=[0,4095]`，不做 `relim()/autoscale`（替代 `realtime_plot_ui.py:484-485`）
- 顶部状态文本：用 `LabelItem` 或状态栏显示包计数与 TTL 时间戳

## 刷新调度与节流
- 正常刷新：Arduino `20–33ms`，STM32 `50–80ms`（两个 `QTimer`）
- 节流策略：窗口交互期间自动提高 `interval` 或暂停刷新；交互结束防抖恢复
- 队列清理：每次拉取尽量批量消费，无新数据时跳过绘制

## 窗口交互优化
- 安装事件过滤器监听 `Resize`/`Move`，进入交互立即 `pause` 两个定时器；最后一次事件后 `150–300ms` 防抖恢复
- 避免频繁图例/布局/坐标对象创建与销毁；仅更新数据

## CSV 与 HTTP 控制保留
- 保留 CSV 保存线程与路径输入（`csv_dir`）；与原逻辑一致
- 保留 HTTP 控制服务（`/start_all`、`/stop_all`、`/status`），在 Qt 主线程用 `QTimer.singleShot` 触发 UI 安全回调（替代 Tk 的 `after`，参照 `realtime_plot_ui.py:523-543`）

## 迁移步骤
1. 新建 Qt 主窗口类，移植顶部控制面板与事件处理（连接/开始/停止）
2. 添加两个 `PlotWidget`，初始化曲线与轴范围；建立 `QTimer` 刷新钩子
3. 接入现有数据读取类：连接时启动线程；开始时启动定时器；停止时完整清理
4. 实现事件绘制（散点/BarGraph/少量线）与滑动窗口 x 轴平移
5. 添加窗口交互防抖暂停/恢复；引入日志开关减少高频 `print`
6. 保留 HTTP 控制服务，并适配到 Qt 线程安全调用
7. 端到端验证：在拖动/缩放、数据高频输入下保持 UI 流畅；对比原版卡顿

## 验收与性能指标
- 窗口拖动/缩放时 UI 保持响应，CPU 利用率显著低于原版
- 数据刷新稳定，无明显掉帧；曲线与事件同步正确
- 停止/开始/断开设备流程稳定，无资源泄漏

## 风险与回滚
- 若依赖安装或驱动异常，保留原 Tk+Matplotlib 版本作为回退
- 事件“竖线”过多时改用散点或条柱以避免对象爆炸

请确认以上迁移方案与技术栈选择。一旦确认，我将按上述步骤实现并在本仓库中交付新的 Qt+pyqtgraph 版本，同时保留旧版文件以便对比与回滚。