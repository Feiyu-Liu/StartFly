import serial
import struct
import csv
import threading
from collections import deque
import numpy as np
import time
import argparse
import os
import sys

# --- Matplotlib (optional import) ---
# 仅在需要绘图时才导入，避免在无图形界面环境中出错
plt = None
animation = None

# --- 帧结构定义 (与STM32代码严格匹配) ---
FRAME_HEADER = b'\xA5\x5A'
EXPECTED_PAYLOAD_SIZE = 1292
NUM_CHANNELS = 10
SAMPLES_PER_PACKET = 64
PAYLOAD_UNPACK_FORMAT = f'<{SAMPLES_PER_PACKET * NUM_CHANNELS}H III'
PLOT_BUFFER_SIZE = 512
BAUD_RATE = 921600

# --- 全局变量 ---
data_lock = threading.Lock()
plot_buffers = [deque(maxlen=PLOT_BUFFER_SIZE) for _ in range(NUM_CHANNELS)]
stop_event = threading.Event()
packets_received = 0
last_sync_ts = 0
last_trigger_ts = 0
LOGGING_ENABLED = True

# --- 辅助函数 ---
def logger(message):
    """根据全局标志决定是否打印日志信息。"""
    if LOGGING_ENABLED:
        print(message, flush=True)

def configure_matplotlib_for_chinese():
    """配置Matplotlib以支持中文显示。"""
    try:
        plt.rcParams['font.sans-serif'] = ['SimHei']
        plt.rcParams['axes.unicode_minus'] = False
    except Exception as e:
        logger(f"中文字体 'SimHei' 设置失败, 请确保已安装该字体。错误: {e}")

# --- 核心功能 ---
def read_serial_data(com_port, csv_filepath):
    """
    在独立线程中运行，解析数据流，写入CSV，并根据日志开关打印信息。
    """
    global packets_received, last_sync_ts, last_trigger_ts
    try:
        ser = serial.Serial(com_port, BAUD_RATE, timeout=1)
        logger(f"成功打开串口 {com_port}...")
    except serial.SerialException as e:
        print(f"致命错误: 无法打开串口 {com_port}。请检查设备连接或端口号。", file=sys.stderr)
        print(e, file=sys.stderr)
        stop_event.set() # 发送停止信号以终止主程序
        return

    try:
        csv_file = open(csv_filepath, 'w', newline='', encoding='utf-8')
        csv_writer = csv.writer(csv_file)
        header = [f'adc{i}' for i in range(NUM_CHANNELS)] + ['send_timestamp_us', 'sync_ttl_timestamp_us', 'trigger_ttl_timestamp_us']
        csv_writer.writerow(header)
        logger(f"数据将记录到: {csv_filepath}")
    except IOError as e:
        print(f"致命错误: 无法写入CSV文件路径 '{csv_filepath}'。请检查权限或路径。", file=sys.stderr)
        print(e, file=sys.stderr)
        ser.close()
        stop_event.set()
        return

    sync_state = 0
    while not stop_event.is_set():
        # --- 同步状态机 ---
        byte = ser.read(1)
        if not byte: continue
        
        if sync_state == 0 and byte == FRAME_HEADER[0:1]:
            sync_state = 1
        elif sync_state == 1 and byte == FRAME_HEADER[1:2]:
            len_bytes = ser.read(2)
            if len(len_bytes) < 2:
                sync_state = 0; continue
            payload_len = struct.unpack('>H', len_bytes)[0]
            if payload_len == EXPECTED_PAYLOAD_SIZE:
                payload = ser.read(payload_len)
                if len(payload) == payload_len:
                    try:
                        packets_received += 1
                        unpacked_data = struct.unpack(PAYLOAD_UNPACK_FORMAT, payload)
                        adc_values_flat, send_ts, sync_ts, trigger_ts = unpacked_data[:-3], unpacked_data[-3], unpacked_data[-2], unpacked_data[-1]
                        if sync_ts != 0xFFFFFFFF:
                            logger(f"--- SYNC TTL 触发 --- 时间戳: {sync_ts} us")
                            last_sync_ts = sync_ts
                        if trigger_ts != 0xFFFFFFFF:
                            logger(f"--- TRIGGER TTL 触发 --- 时间戳: {trigger_ts} us")
                            last_trigger_ts = trigger_ts
                        adc_array = np.array(adc_values_flat).reshape(SAMPLES_PER_PACKET, NUM_CHANNELS).T
                        for i in range(SAMPLES_PER_PACKET):
                            row = list(adc_array[:, i]) + [send_ts, sync_ts, trigger_ts]
                            csv_writer.writerow(row)
                        with data_lock:
                            for i in range(NUM_CHANNELS):
                                plot_buffers[i].extend(adc_array[i])
                    except struct.error:
                        logger("载荷解析失败。重新同步...")
                else:
                    logger("载荷读取不完整。重新同步...")
            else:
                logger("长度字段错误。重新同步...")
            sync_state = 0
        else:
            sync_state = 0

    ser.close()
    csv_file.close()
    logger("串口已关闭，文件已保存。")

def update_plot(frame, lines, ax, title_text, ts_text):
    """更新绘图的回调函数。"""
    with data_lock:
        for i, line in enumerate(lines):
            line.set_data(range(len(plot_buffers[i])), plot_buffers[i])
    title_text.set_text(f'STM32 ADC 实时数据流 (已接收: {packets_received} 包)')
    ts_text.set_text(f'Sync TTL: {last_sync_ts if last_sync_ts != 0 else "N/A"} | Trigger TTL: {last_trigger_ts if last_trigger_ts != 0 else "N/A"}')
    ax.relim()
    ax.autoscale_view(scalex=False, scaley=True)
    return lines + [title_text, ts_text]

def main():
    """主函数，解析参数，初始化并启动所有流程。"""
    global LOGGING_ENABLED, plt, animation

    # --- 1. 参数解析 ---
    parser = argparse.ArgumentParser(description="从STM32设备接收、记录并可选地显示ADC数据。")
    parser.add_argument('-nograph', action='store_true', help='无图形界面模式，不显示实时绘图窗口。')
    parser.add_argument('-nolog', action='store_true', help='无日志模式，不在控制台打印触发事件等信息。')
    parser.add_argument('-comid', type=str, default='COM7', help='指定串口号 (例如: COM7 或 /dev/ttyUSB0)。')
    parser.add_argument('-savein', type=str, default=None, help='指定CSV日志文件的保存目录。默认为脚本当前目录。')
    args = parser.parse_args()

    # --- 2. 应用参数 ---
    if args.nolog:
        LOGGING_ENABLED = False

    # --- 3. 处理文件保存路径 ---
    csv_filename = f'data_log_{time.strftime("%Y%m%d_%H%M%S")}.csv'
    if args.savein:
        save_dir = os.path.abspath(args.savein)
        try:
            if not os.path.isdir(save_dir):
                logger(f"目录 '{save_dir}' 不存在，正在尝试创建...")
                os.makedirs(save_dir, exist_ok=True)
            csv_filepath = os.path.join(save_dir, csv_filename)
        except OSError as e:
            print(f"致命错误: 无法创建目录 '{save_dir}'。请检查权限。错误: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        csv_filepath = csv_filename

    # --- 4. 启动核心逻辑 ---
    serial_thread = threading.Thread(target=read_serial_data, args=(args.comid, csv_filepath), daemon=True)
    serial_thread.start()

    # --- 5. 根据模式选择前台任务 (GUI 或 无头等待) ---
    if not args.nograph:
        # --- GUI模式 ---
        try:
            global plt, animation
            import matplotlib.pyplot as plt
            import matplotlib.animation as animation
        except ImportError:
            print("致命错误: 缺少matplotlib库，无法显示图形。请运行'pip install matplotlib'或使用'-nograph'模式。", file=sys.stderr)
            stop_event.set()
            serial_thread.join()
            sys.exit(1)

        configure_matplotlib_for_chinese()
        fig, ax = plt.subplots(figsize=(15, 8))
        plt.subplots_adjust(bottom=0.1, top=0.9)
        title_text = ax.set_title('STM32 ADC 实时数据流 (等待数据...)', fontsize=16)
        ts_text = ax.text(0.01, 0.95, '', transform=ax.transAxes, fontsize=12, verticalalignment='top', bbox=dict(boxstyle='round,pad=0.5', fc='wheat', alpha=0.5))
        ax.set_xlabel('采样点 (最近512个)', fontsize=12)
        ax.set_ylabel('ADC读数 (0-4095)', fontsize=12)
        ax.set_xlim(0, PLOT_BUFFER_SIZE)
        ax.set_ylim(0, 4096)
        ax.grid(True)
        colors = plt.cm.get_cmap('tab10', NUM_CHANNELS)
        lines = [ax.plot([], [], lw=1.5, color=colors(i), label=f'ADC {i}')[0] for i in range(NUM_CHANNELS)]
        ax.legend(loc='upper right')
        
        ani = animation.FuncAnimation(fig, update_plot, fargs=(lines, ax, title_text, ts_text), interval=50, blit=True)

        def on_close(event):
            logger("绘图窗口关闭，正在停止程序...")
            stop_event.set()

        fig.canvas.mpl_connect('close_event', on_close)
        plt.show()
    else:
        # --- 无头模式 (Headless) ---
        logger("进入无图形界面模式。按 Ctrl+C 停止。")
        try:
            # 保持主线程活动，直到串口线程因错误退出或收到外部信号
            while serial_thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            logger("接收到 Ctrl+C，正在停止程序...")
            stop_event.set()
    
    # --- 6. 清理 ---
    serial_thread.join(timeout=2)
    logger("程序已退出。")

if __name__ == '__main__':
    main()
