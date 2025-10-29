import serial
import struct
import threading
import queue
import time
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
import numpy as np
from collections import deque

class ArduinoDataReader:
    def __init__(self, port="COM5", baudrate=230400):
        self.port = port
        self.baudrate = baudrate
        self.ser = None
        self.data_queue = queue.Queue(maxsize=1000)
        self.running = False
        
    def connect(self):
        """连接Arduino并发送换行符"""
        try:
            self.ser = serial.Serial(self.port, self.baudrate, timeout=0.01)
            time.sleep(2)  # 等待Arduino重启
            self.ser.write(b'\n')  # 发送换行符启动数据传输
            print(f"已连接到 {self.port}，发送换行符启动数据传输...")
            return True
        except Exception as e:
            print(f"连接失败: {e}")
            return False
    
    def read_data(self):
        """读取Arduino数据的线程函数"""
        FRAME_SIZE = 5  # 0xAA + uint16(ADC) + uint16(timestamp) = 1 + 2 + 2 = 5字节
        
        while self.running:
            try:
                if self.ser and self.ser.in_waiting >= FRAME_SIZE:
                    header = self.ser.read(1)
                    if header == b'\xAA':
                        raw = self.ser.read(4)  # 读取剩余的4字节
                        val1 = struct.unpack("<H", raw[0:2])[0]  # 小端2字节无符号整数（ADC 0-1023）
                        ts = struct.unpack("<H", raw[2:4])[0]   # 小端2字节无符号整数（Timer1计数）
                        
                        # 将数据放入队列
                        timestamp = time.time()
                        try:
                            self.data_queue.put((timestamp, val1, ts), timeout=0.001)
                        except queue.Full:
                            # 如果队列满了，移除最旧的数据
                            try:
                                self.data_queue.get_nowait()
                                self.data_queue.put((timestamp, val1, ts), timeout=0.001)
                            except:
                                pass
                    else:
                        # 同步失败，继续找帧头
                        continue
            except Exception as e:
                print(f"读取数据错误: {e}")
                time.sleep(0.001)
    
    def start(self):
        """启动数据读取线程"""
        if self.connect():
            self.running = True
            self.read_thread = threading.Thread(target=self.read_data, daemon=True)
            self.read_thread.start()
            return True
        return False
    
    def stop(self):
        """停止数据读取"""
        self.running = False
        if self.read_thread:
            self.read_thread.join(timeout=1)
        if self.ser:
            self.ser.close()

class RealtimePlotter:
    def __init__(self, data_reader, max_points=1000):
        self.data_reader = data_reader
        self.max_points = max_points
        
        # 数据缓冲区
        self.timestamps = deque(maxlen=max_points)
        self.val1_data = deque(maxlen=max_points)
        self.ts_data = deque(maxlen=max_points)
        
        # 创建图形 - 使用单个子图和双Y轴
        self.fig, self.ax1 = plt.subplots(figsize=(12, 6))
        self.ax2 = self.ax1.twinx()  # 创建第二个Y轴
        self.fig.suptitle('Arduino Real-time Data Monitor')
        
        # 初始化曲线
        self.line1, = self.ax1.plot([], [], 'b-', label='Analog (A0)', linewidth=2)
        self.line2, = self.ax2.plot([], [], 'r-', label='Trigger Timestamp (Timer1)', linewidth=2)
        
        # 设置坐标轴
        self.ax1.set_xlabel('Time (s)')
        self.ax1.set_ylabel('Analog Value (0-1023)', color='blue')
        self.ax1.set_ylim(0, 1023)
        self.ax1.tick_params(axis='y', labelcolor='blue')
        self.ax1.grid(True, alpha=0.3)
        
        self.ax2.set_ylabel('Trigger Timestamp (ticks)', color='red')
        self.ax2.tick_params(axis='y', labelcolor='red')
        
        # 组合图例
        lines1, labels1 = self.ax1.get_legend_handles_labels()
        lines2, labels2 = self.ax2.get_legend_handles_labels()
        self.ax1.legend(lines1 + lines2, labels1 + labels2, loc='upper left')
        
        self.start_time = time.time()
    
    def update_plot(self, frame):
        """更新图形的动画函数"""
        # 从队列读取所有可用数据
        new_data = []
        while True:
            try:
                data = self.data_reader.data_queue.get_nowait()
                new_data.append(data)
            except queue.Empty:
                break
        
        # 如果有新数据，更新缓冲区
        if new_data:
            for timestamp, val1, ts in new_data:
                relative_time = timestamp - self.start_time
                self.timestamps.append(relative_time)
                self.val1_data.append(val1)
                # 将未触发的固定时间(0xFFFF)映射为NaN以便在图中显示间隙
                self.ts_data.append(np.nan if ts == 0xFFFF else ts)
        
        # 更新曲线数据
        if len(self.timestamps) > 0:
            times = list(self.timestamps)
            self.line1.set_data(times, list(self.val1_data))
            self.line2.set_data(times, list(self.ts_data))
            
            # 更新坐标轴范围
            if len(times) > 1:
                time_range = times[-1] - times[0]
                if time_range > 0:
                    self.ax1.set_xlim(times[0], times[-1])
            
            # 更新Y轴范围（时间戳，忽略NaN）
            if len(self.ts_data) > 0:
                valid_ts = [v for v in self.ts_data if not np.isnan(v)]
                if len(valid_ts) > 0:
                    ts_min, ts_max = min(valid_ts), max(valid_ts)
                    if ts_max == ts_min:
                        self.ax2.set_ylim(max(0, ts_min - 5), ts_min + 5)
                    else:
                        ts_padding = max(2, (ts_max - ts_min) * 0.2)
                        self.ax2.set_ylim(max(0, ts_min - ts_padding), ts_max + ts_padding)
        
        return [self.line1, self.line2]
    
    def start(self):
        """启动实时绘图"""
        # 创建动画
        self.ani = FuncAnimation(self.fig, self.update_plot, interval=20, 
                                blit=True, cache_frame_data=False)
        
        plt.tight_layout()
        plt.show()

def main():
    """Main function"""
    # Create data reader
    reader = ArduinoDataReader(port="COM5", baudrate=230400)
    
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