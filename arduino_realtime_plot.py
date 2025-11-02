import serial
import struct
import threading
import queue
import time
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
import numpy as np
from collections import deque
import csv
from datetime import datetime

class ArduinoDataReader:
    def __init__(self, port="COM5", baudrate=921600, save_to_csv=False, start_delay_s=2):
        self.port = port
        self.baudrate = baudrate
        self.ser = None
        self.data_queue = queue.Queue(maxsize=1000)
        self.running = False
        self.start_delay_s = start_delay_s

        self.save_to_csv = save_to_csv
        if self.save_to_csv:
            self.csv_queue = queue.Queue(maxsize=30000) # 注意修改
            self.csv_filename = f"arduino_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            self.csv_thread = threading.Thread(target=self._csv_writer, daemon=True)


    def _csv_writer(self):
        """一个将队列中的数据写入CSV文件的线程。"""
        print(f"开始将数据实时保存到 {self.csv_filename}")
        with open(self.csv_filename, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['FrameUs', 'ADC', 'SyncUs', 'MagnetUs'])
            while True:
                try:
                    data = self.csv_queue.get(timeout=1)
                    writer.writerow(data)
                    self.csv_queue.task_done()
                except queue.Empty:
                    if not self.running:
                        break
        print(f"数据保存完成: {self.csv_filename}")
        
    def connect(self):
        """连接Arduino并发送换行符"""
        try:
            self.ser = serial.Serial(self.port, self.baudrate, timeout=0.01)
            print(f"已连接到 {self.port}")
            return True
        except Exception as e:
            print(f"连接失败: {e}")
            return False

    # 发送端改为32位微秒时间戳后，不再需要16位展开逻辑
    
    def read_data(self):
        """读取Arduino数据的线程函数"""
        FRAME_SIZE = 16  # 0xAA + uint16(ADC) + uint32(sync_us) + uint32(magnet_us) + uint32(frame_us) + uint8(flags)

        while self.running:
            try:
                if self.ser and self.ser.in_waiting >= FRAME_SIZE:
                    header = self.ser.read(1)
                    if header == b'\xAA':
                        raw = self.ser.read(15)
                        val1, sync_us32_raw, magnet_us32_raw, frame_us32, flags = struct.unpack("<HIIIB", raw)

                        # 根据标志位判定事件是否有效
                        sync_us32 = None if not (flags & 0x01) else (sync_us32_raw if sync_us32_raw != 0 else None)
                        magnet_us32 = None if not (flags & 0x02) else (magnet_us32_raw if magnet_us32_raw != 0 else None)

                        csv_row_to_queue = None
                        if self.save_to_csv:
                            csv_row_to_queue = (frame_us32, val1,
                                                0 if sync_us32 is None else sync_us32,
                                                0 if magnet_us32 is None else magnet_us32)

                        if sync_us32 is not None:
                            print(f"SYNC detected! Us: {sync_us32}. Preparing CSV row: {csv_row_to_queue}")
                        if magnet_us32 is not None:
                            print(f"MAGNET detected! Us: {magnet_us32}")

                        try:
                            self.data_queue.put((frame_us32, val1, sync_us32, magnet_us32), timeout=0.001)
                            if self.save_to_csv:
                                self.csv_queue.put(csv_row_to_queue)
                        except queue.Full:
                            # 丢弃最老数据并重试
                            try:
                                self.data_queue.get_nowait()
                                self.data_queue.put((frame_us32, val1, sync_us32, magnet_us32), timeout=0.001)
                            except Exception:
                                pass
                    else:
                        continue
            except Exception as e:
                print(f"读取数据错误: {e}")
                time.sleep(0.001)
    
    def start(self):
        """启动数据读取和CSV写入线程,并延迟发送启动信号"""
        if self.connect():
            self.running = True
            if self.save_to_csv:
                self.csv_thread.start()

            # 立即启动读取线程，这样可以立刻开始绘图和保存
            self.read_thread = threading.Thread(target=self.read_data, daemon=True)
            self.read_thread.start()
            print("数据读取、保存和绘图已立即开始...")

            # 启动一个独立的线程来延迟发送换行符
            def delayed_send():
                print(f"将在 {self.start_delay_s} 秒后发送启动信号 (换行符)...")
                time.sleep(self.start_delay_s)
                if self.running and self.ser:
                    try:
                        self.ser.write(b'\n')
                        print("启动信号 (换行符) 已发送。")
                    except Exception as e:
                        print(f"发送启动信号失败: {e}")

            send_thread = threading.Thread(target=delayed_send, daemon=True)
            send_thread.start()

            return True
        return False
    
    def stop(self):
        """停止数据读取和写入"""
        self.running = False
        if self.read_thread:
            self.read_thread.join(timeout=1)
        
        if self.save_to_csv and self.csv_thread:
            print("等待数据写入完成...")
            self.csv_queue.join()
            self.csv_thread.join(timeout=2)

        if self.ser:
            self.ser.close()

class RealtimePlotter:
    def __init__(self, data_reader, max_points=1000):
        self.data_reader = data_reader
        self.max_points = max_points

        self.timestamps = deque(maxlen=max_points)
        self.val1_data = deque(maxlen=max_points)
        self.sync_ts_data = deque(maxlen=max_points)
        self.magnet_ts_data = deque(maxlen=max_points)

        self.fig, (self.ax1, self.ax2, self.ax3) = plt.subplots(3, 1, figsize=(12, 8), sharex=True)
        self.fig.suptitle('Arduino Real-time Data Monitor')

        self.line1, = self.ax1.plot([], [], 'b-', label='Analog (A0)')
        self.ax1.set_ylabel('Analog Value')
        self.ax1.set_ylim(0, 1023)
        self.ax1.legend(loc='upper left')

        # Use vertical lines for event timestamps
        self.line2, = self.ax2.plot([], [], 'g-', linewidth=2, label='Sync Event')
        self.ax2.set_ylabel('Sync Event')
        self.ax2.set_ylim(0, 1)
        self.ax2.set_yticks([]) # Hide y-axis ticks
        self.ax2.legend(loc='upper left')

        self.line3, = self.ax3.plot([], [], 'r-', linewidth=2, label='Magnet Event')
        self.ax3.set_xlabel('Time (s)')
        self.ax3.set_ylabel('Magnet Event')
        self.ax3.set_ylim(0, 1)
        self.ax3.set_yticks([]) # Hide y-axis ticks
        self.ax3.legend(loc='upper left')
        
    
    def update_plot(self, frame):
        new_data = []
        while True:
            try:
                data = self.data_reader.data_queue.get_nowait()
                new_data.append(data)
            except queue.Empty:
                break
        
        if new_data:
            for frame_us32, val1, sync_us32, magnet_us32 in new_data:
                # 以Arduino的微秒为时间轴（秒）
                t_seconds = frame_us32 / 1_000_000.0
                self.timestamps.append(t_seconds)
                self.val1_data.append(val1)
                # For events, store a 1, otherwise NaN
                self.sync_ts_data.append(1 if sync_us32 is not None else np.nan)
                self.magnet_ts_data.append(1 if magnet_us32 is not None else np.nan)
        
            if len(self.timestamps) > 0:
                times = list(self.timestamps)
                self.line1.set_data(times, list(self.val1_data))

                # Build data for vertical lines for sync events
                sync_x, sync_y = [], []
                for t, y_val in zip(times, self.sync_ts_data):
                    if not np.isnan(y_val):
                        sync_x.extend([t, t, np.nan])
                        sync_y.extend([0, 1, np.nan])
                self.line2.set_data(sync_x, sync_y)

                # Build data for vertical lines for magnet events
                magnet_x, magnet_y = [], []
                for t, y_val in zip(times, self.magnet_ts_data):
                    if not np.isnan(y_val):
                        magnet_x.extend([t, t, np.nan])
                        magnet_y.extend([0, 1, np.nan])
                self.line3.set_data(magnet_x, magnet_y)
                
                if len(times) > 1:
                    self.ax1.set_xlim(times[0], times[-1])
                    # The x-axis is shared, so this is sufficient
        
        return [self.line1, self.line2, self.line3]
    
    def start(self):
        self.ani = FuncAnimation(self.fig, self.update_plot, interval=20, 
                                blit=False, cache_frame_data=False)
        
        plt.tight_layout()
        plt.show()

def main():
    """Main function"""
    # --- 可配置参数 ---
    SERIAL_PORT = "/dev/tty.usbmodem11401"
    BAUD_RATE = 921600
    SAVE_TO_CSV = True
    START_DELAY_SECONDS = 7  # 在此处修改启动延迟
    # ------------------

    # Create data reader
    reader = ArduinoDataReader(port=SERIAL_PORT, baudrate=BAUD_RATE, 
                               save_to_csv=SAVE_TO_CSV, start_delay_s=START_DELAY_SECONDS)
    
    # 启动数据读取
    if not reader.start():
        print("无法启动数据读取，程序退出")
        return
    
    try:
        # 创建绘图器并启动
        plotter = RealtimePlotter(reader)
        plotter.start()
        
    except KeyboardInterrupt:
        print("\n用户中断，正在关闭...")
    except Exception as e:
        print(f"程序错误: {e}")
    finally:
        reader.stop()
        print("程序已关闭")

if __name__ == "__main__":
    main()