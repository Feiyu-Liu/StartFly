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
    def __init__(self, port="COM5", baudrate=230400, save_to_csv=False):
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

    def _csv_writer(self):
        """一个将队列中的数据写入CSV文件的线程。"""
        print(f"开始将数据实时保存到 {self.csv_filename}")
        with open(self.csv_filename, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['ADC', 'Sync Timestamp', 'Magnet Timestamp'])
            while True:
                try:
                    timestamp, val1, sync_ts, magnet_ts = self.csv_queue.get(timeout=1)
                    self.csv_writer.writerow([timestamp, val1, sync_ts, magnet_ts])
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
    
    def read_data(self):
        """读取Arduino数据的线程函数"""
        FRAME_SIZE = 11  # 0xAA + uint16(ADC) + uint16(sync_ts) + uint16(magnet_ts) = 7 bytes
        
        while self.running:
            try:
                if self.ser and self.ser.in_waiting >= FRAME_SIZE:
                    header = self.ser.read(1)
                    if header == b'\xAA':
                        raw = self.ser.read(10) # 2 (ADC) + 4 (sync) + 4 (magnet)
                        val1, sync_ts, magnet_ts = struct.unpack('<HLL', raw)

                        if sync_ts != 0xFFFF:
                            print(f"SYNC detected! Timestamp: {sync_ts}")
                        if magnet_ts != 0xFFFF:
                            print(f"MAGNET detected! Timestamp: {magnet_ts}")

                        timestamp = time.time()
                        try:
                            self.data_queue.put((timestamp, val1, sync_ts, magnet_ts), timeout=0.001)
                            if self.save_to_csv:
                                self.csv_queue.put((val1, sync_ts, magnet_ts))
                        except queue.Full:
                            try:
                                self.data_queue.get_nowait()
                                self.data_queue.put((timestamp, val1, ts), timeout=0.001)
                            except:
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

        self.line2, = self.ax2.plot([], [], 'g-', label='Sync Timestamp')
        self.ax2.set_ylabel('Sync TS')
        self.ax2.legend(loc='upper left')

        self.line3, = self.ax3.plot([], [], 'r-', label='Magnet Timestamp')
        self.ax3.set_xlabel('Time (s)')
        self.ax3.set_ylabel('Magnet TS')
        self.ax3.legend(loc='upper left')
        
        self.start_time = time.time()
    
    def update_plot(self, frame):
        new_data = []
        while True:
            try:
                data = self.data_reader.data_queue.get_nowait()
                new_data.append(data)
            except queue.Empty:
                break
        
        if new_data:
            for timestamp, val1, sync_ts, magnet_ts in new_data:
                relative_time = timestamp - self.start_time
                self.timestamps.append(relative_time)
                self.val1_data.append(val1)
                self.sync_ts_data.append(np.nan if sync_ts == 0 else sync_ts)
                self.magnet_ts_data.append(np.nan if magnet_ts == 0 else magnet_ts)
        
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
                self.ax2.set_ylim(min(valid_sync_ts) - 10, max(valid_sync_ts) + 10)

            valid_magnet_ts = [v for v in self.magnet_ts_data if not np.isnan(v)]
            if len(valid_magnet_ts) > 0:
                self.ax3.set_ylim(min(valid_magnet_ts) - 10, max(valid_magnet_ts) + 10)
        
        return [self.line1, self.line2, self.line3]
    
    def start(self):
        self.ani = FuncAnimation(self.fig, self.update_plot, interval=20, 
                                blit=False, cache_frame_data=False)
        
        plt.tight_layout()
        plt.show()

def main():
    """Main function"""
    # Create data reader
    reader = ArduinoDataReader(port="COM5", baudrate=230400, save_to_csv=True)
    
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