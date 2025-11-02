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
    def __init__(self, port="COM5", baudrate=921600, save_to_csv=False):
        self.port = port
        self.baudrate = baudrate
        self.ser = None
        self.data_queue = queue.Queue(maxsize=1000)
        self.running = False

        self.save_to_csv = save_to_csv
        if self.save_to_csv:
            self.csv_queue = queue.Queue(maxsize=10000)
            self.csv_filename = f"arduino_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            self.csv_thread = threading.Thread(target=self._csv_writer, daemon=True)

        # 用于将16位micros()展开为单调递增的32位微秒
        self.last_us16 = None
        self.us_base = 0  # 累积回绕的基数（每次加65536）

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
            time.sleep(2)
            self.ser.write(b'\n')
            print(f"已连接到 {self.port}，发送换行符启动数据传输...")
            return True
        except Exception as e:
            print(f"连接失败: {e}")
            return False

    def _unwrap_frame_us(self, us16):
        """将帧携带的16位微秒展开为32位绝对微秒。"""
        if self.last_us16 is None:
            self.last_us16 = us16
        else:
            # 检测回绕：当前值小于上一帧值（16位微秒约每65.536ms回绕）
            if us16 < self.last_us16:
                self.us_base += 65536
            self.last_us16 = us16
        return self.us_base + us16

    def _unwrap_event_us(self, evt16, cur_frame_us16):
        """将事件携带的16位微秒转换到与当前帧同一时间基，返回None表示无事件。"""
        if evt16 == 0xFFFE:
            return None
        # 如果事件的16位值不大于当前帧值，属于当前基；否则属于上一个基
        if evt16 <= cur_frame_us16:
            return self.us_base + evt16
        else:
            return (self.us_base - 65536) + evt16
    
    def read_data(self):
        """读取Arduino数据的线程函数"""
        FRAME_SIZE = 9  # 0xAA + uint16(ADC) + uint16(sync_us) + uint16(magnet_us) + uint16(frame_us)

        while self.running:
            try:
                if self.ser and self.ser.in_waiting >= FRAME_SIZE:
                    header = self.ser.read(1)
                    if header == b'\xAA':
                        raw = self.ser.read(8)
                        val1, sync_us16, magnet_us16, frame_us16 = struct.unpack("<HHHH", raw)

                        # 展开为32位微秒
                        frame_us32 = self._unwrap_frame_us(frame_us16)
                        sync_us32 = self._unwrap_event_us(sync_us16, frame_us16)
                        magnet_us32 = self._unwrap_event_us(magnet_us16, frame_us16)

                        if sync_us32 is not None:
                            print(f"SYNC detected! Us: {sync_us32}")
                        if magnet_us32 is not None:
                            print(f"MAGNET detected! Us: {magnet_us32}")

                        try:
                            self.data_queue.put((frame_us32, val1, sync_us32, magnet_us32), timeout=0.001)
                            if self.save_to_csv:
                                self.csv_queue.put((frame_us32, val1,
                                                    "" if sync_us32 is None else sync_us32,
                                                    "" if magnet_us32 is None else magnet_us32))
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
        """启动数据读取和CSV写入线程"""
        if self.connect():
            self.running = True
            self.read_thread = threading.Thread(target=self.read_data, daemon=True)
            self.read_thread.start()
            if self.save_to_csv:
                self.csv_thread.start()
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

        self.line2, = self.ax2.plot([], [], 'g-', label='Sync Us')
        self.ax2.set_ylabel('Sync Ms')
        self.ax2.legend(loc='upper left')

        self.line3, = self.ax3.plot([], [], 'r-', label='Magnet Us')
        self.ax3.set_xlabel('Time (s)')
        self.ax3.set_ylabel('Magnet Us')
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
                self.sync_ts_data.append(np.nan if sync_us32 is None else sync_us32)
                self.magnet_ts_data.append(np.nan if magnet_us32 is None else magnet_us32)
        
            if len(self.timestamps) > 0:
                times = list(self.timestamps)
                self.line1.set_data(times, list(self.val1_data))
                self.line2.set_data(times, list(self.sync_ts_data))
                self.line3.set_data(times, list(self.magnet_ts_data))
                
                if len(times) > 1:
                    self.ax1.set_xlim(times[0], times[-1])
                    self.ax2.set_xlim(times[0], times[-1])
                    self.ax3.set_xlim(times[0], times[-1])

                valid_sync_ts = [v for v in self.sync_ts_data if not np.isnan(v)]
                if len(valid_sync_ts) > 0:
                    self.ax2.set_ylim(min(valid_sync_ts) - 1000, max(valid_sync_ts) + 1000)  # 预留±1ms可视范围

                valid_magnet_ts = [v for v in self.magnet_ts_data if not np.isnan(v)]
                if len(valid_magnet_ts) > 0:
                    self.ax3.set_ylim(min(valid_magnet_ts) - 1000, max(valid_magnet_ts) + 1000)  # 预留±1ms可视范围
        
        return [self.line1, self.line2, self.line3]
    
    def start(self):
        self.ani = FuncAnimation(self.fig, self.update_plot, interval=20, 
                                blit=False, cache_frame_data=False)
        
        plt.tight_layout()
        plt.show()

def main():
    """Main function"""
    # Create data reader
    reader = ArduinoDataReader(port="/dev/tty.usbmodem11401", baudrate=921600, save_to_csv=True)
    
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