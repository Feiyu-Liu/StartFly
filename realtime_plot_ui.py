import serial
import struct
import threading
import queue
import time
from matplotlib.animation import FuncAnimation
import numpy as np
from collections import deque
import csv
from datetime import datetime
import os
import tkinter as tk
from tkinter import ttk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.parse as urlparse

class ArduinoDataReader:
    def __init__(self, port, baudrate=921600, save_to_csv=False, start_delay_s=2, csv_dir=None):
        self.port = port
        self.baudrate = baudrate
        self.ser = None
        self.data_queue = queue.Queue(maxsize=1000)
        self.running = False
        self.start_delay_s = start_delay_s
        self.csv_dir = csv_dir

        self.save_to_csv = save_to_csv
        if self.save_to_csv:
            self.csv_queue = queue.Queue(maxsize=30000) # 注意修改
            # 构建CSV保存路径
            dir_path = self.csv_dir.strip() if (self.csv_dir and isinstance(self.csv_dir, str)) else os.getcwd()
            try:
                os.makedirs(dir_path, exist_ok=True)
            except Exception as e:
                print(f"创建CSV目录失败，改用当前目录: {e}")
                dir_path = os.getcwd()

            file_name = f"arduino_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            self.csv_filename = os.path.join(dir_path, file_name)
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
            if self.ser and getattr(self.ser, 'is_open', False):
                print(f"已连接到 {self.port}")
                return True
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
                self.csv_queue = queue.Queue(maxsize=30000)
                dir_path = self.csv_dir.strip() if (self.csv_dir and isinstance(self.csv_dir, str)) else os.getcwd()
                try:
                    os.makedirs(dir_path, exist_ok=True)
                except Exception as e:
                    print(f"创建CSV目录失败，改用当前目录: {e}")
                    dir_path = os.getcwd()

                file_name = f"arduino_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
                self.csv_filename = os.path.join(dir_path, file_name)
                self.csv_thread = threading.Thread(target=self._csv_writer, daemon=True)
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
        try:
            if hasattr(self, 'read_thread') and self.read_thread:
                self.read_thread.join(timeout=1)
        except Exception:
            pass

        if self.save_to_csv and hasattr(self, 'csv_thread') and self.csv_thread:
            print("等待数据写入完成...")
            try:
                if hasattr(self, 'csv_queue') and self.csv_queue:
                    self.csv_queue.join()
            except Exception:
                pass
            try:
                self.csv_thread.join(timeout=2)
            except Exception:
                pass

        try:
            while True:
                self.data_queue.get_nowait()
        except queue.Empty:
            pass

class STM32DataReader:
    def __init__(self, port, baudrate=921600, save_to_csv=False, csv_dir=None):
        self.port = port
        self.baudrate = baudrate
        self.ser = None
        self.running = False
        self.lock = threading.Lock()
        self.save_to_csv = save_to_csv
        self.csv_dir = csv_dir

        # Protocol parameters (must match STM32 firmware)
        self.FRAME_HEADER = b'\xA5\x5A'
        self.EXPECTED_PAYLOAD_SIZE = 1292
        self.NUM_CHANNELS = 10
        self.SAMPLES_PER_PACKET = 64
        self.PLOT_BUFFER_SIZE = 512
        self.PAYLOAD_UNPACK_FORMAT = f'<{self.SAMPLES_PER_PACKET * self.NUM_CHANNELS}H III'

        # Data buffers and stats
        self.buffers = [deque(maxlen=self.PLOT_BUFFER_SIZE) for _ in range(self.NUM_CHANNELS)]
        self.packets_received = 0
        self.last_sync_ts = 0
        self.last_trigger_ts = 0

        # CSV writer setup
        if self.save_to_csv:
            self.csv_queue = queue.Queue(maxsize=200000)
            dir_path = self.csv_dir.strip() if (self.csv_dir and isinstance(self.csv_dir, str)) else os.getcwd()
            try:
                os.makedirs(dir_path, exist_ok=True)
            except Exception as e:
                print(f"STM32 创建CSV目录失败，改用当前目录: {e}")
                dir_path = os.getcwd()
            file_name = f"stm32_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            self.csv_filename = os.path.join(dir_path, file_name)
            self.csv_thread = threading.Thread(target=self._csv_writer, daemon=True)

    def _csv_writer(self):
        try:
            with open(self.csv_filename, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                header = [f'adc{i}' for i in range(self.NUM_CHANNELS)] + ['send_timestamp_us', 'sync_ttl_timestamp_us', 'trigger_ttl_timestamp_us']
                writer.writerow(header)
                while self.running or (self.csv_queue and not self.csv_queue.empty()):
                    try:
                        row = self.csv_queue.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    writer.writerow(row)
                    self.csv_queue.task_done()
        except Exception as e:
            print(f"STM32 CSV写入错误: {e}")

    def connect(self):
        try:
            if self.ser and getattr(self.ser, 'is_open', False):
                print(f"STM32 已连接到 {self.port}")
                return True
            self.ser = serial.Serial(self.port, self.baudrate, timeout=1)
            print(f"STM32 已连接到 {self.port}")
            return True
        except Exception as e:
            print(f"STM32 连接失败: {e}")
            return False

    def read_loop(self):
        sync_state = 0
        while self.running:
            try:
                byte = self.ser.read(1)
                if not byte:
                    continue

                if sync_state == 0 and byte == self.FRAME_HEADER[0:1]:
                    sync_state = 1
                elif sync_state == 1 and byte == self.FRAME_HEADER[1:2]:
                    len_bytes = self.ser.read(2)
                    if len(len_bytes) < 2:
                        sync_state = 0
                        continue
                    payload_len = struct.unpack('>H', len_bytes)[0]
                    if payload_len == self.EXPECTED_PAYLOAD_SIZE:
                        payload = self.ser.read(payload_len)
                        if len(payload) == payload_len:
                            try:
                                unpacked = struct.unpack(self.PAYLOAD_UNPACK_FORMAT, payload)
                                adc_values_flat = unpacked[:self.SAMPLES_PER_PACKET * self.NUM_CHANNELS]
                                send_ts, sync_ts, trigger_ts = unpacked[-3], unpacked[-2], unpacked[-1]

                                if sync_ts != 0xFFFFFFFF:
                                    self.last_sync_ts = sync_ts
                                    print(f"STM32 --- SYNC TTL --- {sync_ts} us")
                                if trigger_ts != 0xFFFFFFFF:
                                    self.last_trigger_ts = trigger_ts
                                    print(f"STM32 --- TRIGGER TTL --- {trigger_ts} us")

                                adc_array = np.array(adc_values_flat).reshape(self.SAMPLES_PER_PACKET, self.NUM_CHANNELS).T
                                with self.lock:
                                    for i in range(self.NUM_CHANNELS):
                                        self.buffers[i].extend(adc_array[i])
                                self.packets_received += 1

                                # enqueue csv rows if enabled
                                if self.save_to_csv:
                                    try:
                                        for i in range(self.SAMPLES_PER_PACKET):
                                            row = list(adc_array[:, i]) + [send_ts, sync_ts, trigger_ts]
                                            self.csv_queue.put(row, timeout=0.1)
                                    except Exception:
                                        pass
                            except struct.error:
                                # parsing error, resync
                                pass
                        else:
                            # incomplete payload, resync
                            pass
                    # Regardless of success, reset state to search next frame
                    sync_state = 0
                else:
                    sync_state = 0
            except Exception as e:
                print(f"STM32 读取数据错误: {e}")
                time.sleep(0.005)

    def start(self):
        if not self.connect():
            return False
        self.running = True
        if self.save_to_csv:
            self.csv_queue = queue.Queue(maxsize=200000)
            dir_path = self.csv_dir.strip() if (self.csv_dir and isinstance(self.csv_dir, str)) else os.getcwd()
            try:
                os.makedirs(dir_path, exist_ok=True)
            except Exception as e:
                print(f"STM32 创建CSV目录失败，改用当前目录: {e}")
                dir_path = os.getcwd()
            file_name = f"stm32_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            self.csv_filename = os.path.join(dir_path, file_name)
            self.csv_thread = threading.Thread(target=self._csv_writer, daemon=True)
            self.csv_thread.start()
        self.thread = threading.Thread(target=self.read_loop, daemon=True)
        self.thread.start()
        return True

    def stop(self):
        self.running = False
        try:
            if hasattr(self, 'thread') and self.thread:
                self.thread.join(timeout=1)
        except Exception:
            pass
        if self.save_to_csv and hasattr(self, 'csv_thread'):
            try:
                if hasattr(self, 'csv_queue') and self.csv_queue:
                    self.csv_queue.join()
                self.csv_thread.join(timeout=2)
            except Exception:
                pass
        try:
            if hasattr(self, 'buffers'):
                for buf in self.buffers:
                    buf.clear()
        except Exception:
            pass

class RealtimePlotter:
    def __init__(self, data_reader, max_points=1000):
        self.data_reader = data_reader
        self.max_points = max_points

        self.timestamps = deque(maxlen=max_points)
        self.val1_data = deque(maxlen=max_points)
        self.sync_ts_data = deque(maxlen=max_points)
        self.magnet_ts_data = deque(maxlen=max_points)

        self.fig = Figure(figsize=(7, 5))
        self.ax1 = self.fig.add_subplot(3, 1, 1)
        self.ax2 = self.fig.add_subplot(3, 1, 2, sharex=self.ax1)
        self.ax3 = self.fig.add_subplot(3, 1, 3, sharex=self.ax1)
        self.fig.suptitle('Real-time Data Monitor')

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
        self.fig.tight_layout()

    def stop(self):
        try:
            if hasattr(self, 'ani') and self.ani and self.ani.event_source:
                self.ani.event_source.stop()
        except Exception:
            pass

class STM32Plotter:
    def __init__(self, data_reader):
        self.data_reader = data_reader
        self.fig = Figure(figsize=(7, 3))
        self.ax = self.fig.add_subplot(1, 1, 1)

        self.ax.set_xlabel('Sample Points (last 512)')
        self.ax.set_ylabel('ADC Reading (0-4095)')
        self.ax.set_xlim(0, self.data_reader.PLOT_BUFFER_SIZE)
        self.ax.set_ylim(0, 4096)
        self.ax.grid(True)

        colors = np.linspace(0.0, 1.0, self.data_reader.NUM_CHANNELS)
        cmap = None
        try:
            import matplotlib.pyplot as plt
            cmap = plt.cm.get_cmap('tab10', self.data_reader.NUM_CHANNELS)
        except Exception:
            cmap = None

        self.lines = []
        for i in range(self.data_reader.NUM_CHANNELS):
            color = (cmap(i) if cmap is not None else None)
            line, = self.ax.plot([], [], lw=1.5, label=f'ADC {i}', color=color)
            self.lines.append(line)
        self.ax.legend(loc='upper right')

        self.title_text = self.ax.set_title('STM32 ADC Real-time Data Stream (Waiting for data...)')
        self.ts_text = self.ax.text(0.01, 0.95, '', transform=self.ax.transAxes, fontsize=10,
                                    verticalalignment='top', bbox=dict(boxstyle='round,pad=0.4', fc='wheat', alpha=0.5))

    def update_plot(self, frame):
        with self.data_reader.lock:
            for i, line in enumerate(self.lines):
                line.set_data(range(len(self.data_reader.buffers[i])), self.data_reader.buffers[i])
        self.title_text.set_text(f'STM32 Packets: {self.data_reader.packets_received}')
        sync_str = (str(self.data_reader.last_sync_ts) if self.data_reader.last_sync_ts != 0 else 'N/A')
        trig_str = (str(self.data_reader.last_trigger_ts) if self.data_reader.last_trigger_ts != 0 else 'N/A')
        self.ts_text.set_text(f'Sync TTL: {sync_str} | Trigger TTL: {trig_str}')
        self.ax.relim()
        self.ax.autoscale_view(scalex=False, scaley=True)
        return self.lines + [self.title_text, self.ts_text]

    def start(self):
        self.ani = FuncAnimation(self.fig, self.update_plot, interval=50, blit=False)
        self.fig.tight_layout()

    def stop(self):
        try:
            if hasattr(self, 'ani') and self.ani and self.ani.event_source:
                self.ani.event_source.stop()
        except Exception:
            pass

class ControlRequestHandler(BaseHTTPRequestHandler):
    app_ref = None

    def _ok(self, content='OK'):
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.end_headers()
        try:
            self.wfile.write(content.encode('utf-8'))
        except Exception:
            pass

    def log_message(self, format, *args):
        # 静默HTTP日志，避免干扰控制台
        return

    def do_GET(self):
        parsed = urlparse.urlparse(self.path)
        path = parsed.path
        app = ControlRequestHandler.app_ref
        if app is None:
            self._ok('NO_APP')
            return

        if path == '/start_all':
            # 通过Tk主线程调度，安全触发UI回调
            app.root.after(0, lambda: (not app.is_running) and app.toggle_run())
            self._ok('STARTED')
        elif path == '/stop_all':
            app.root.after(0, lambda: app.is_running and app.toggle_run())
            self._ok('STOPPED')
        elif path == '/status':
            stm32_running = bool(getattr(app, 'stm32_reader', None) and app.stm32_reader.running)
            status = f"arduino={app.is_running}, stm32={stm32_running}"
            self._ok(status)
        else:
            self.send_error(404, 'Not Found')

def start_control_server(app, port=8765):
    ControlRequestHandler.app_ref = app
    server = HTTPServer(('127.0.0.1', port), ControlRequestHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    print(f'控制服务已启动: http://127.0.0.1:{port}')
    return server

class App:
    def __init__(self, root):
        self.root = root
        self.root.title('Arduino Real-time Data Monitor (UI)')

        # 状态
        self.is_running = False
        self.is_connected = False
        self.reader = None
        self.plotter = None
        self.canvas = None
        # STM32 相关
        self.stm32_reader = None
        self.stm32_plotter = None
        self.stm32_canvas = None

        # 顶部控制面板
        control_frame = ttk.Frame(self.root)
        control_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=10)

        # 输入：port
        ttk.Label(control_frame, text='port').grid(row=0, column=0, sticky='w')
        self.port_entry = ttk.Entry(control_frame, width=30)
        self.port_entry.grid(row=0, column=1, padx=5)
        self.port_entry.insert(0, 'COM5')

        # 输入：no-csv (文本框，填写 0/1 或 true/false)
        ttk.Label(control_frame, text='no-csv').grid(row=0, column=2, sticky='w')
        self.no_csv_entry = ttk.Entry(control_frame, width=10)
        self.no_csv_entry.grid(row=0, column=3, padx=5)
        self.no_csv_entry.insert(0, '0')

        # 输入：delay（秒）
        ttk.Label(control_frame, text='delay(s)').grid(row=0, column=4, sticky='w')
        self.delay_entry = ttk.Entry(control_frame, width=8)
        self.delay_entry.grid(row=0, column=5, padx=5)
        self.delay_entry.insert(0, '4')

        # 开始/停止按钮
        self.connect_button = ttk.Button(control_frame, text='连接', command=self.connect_devices)
        self.connect_button.grid(row=0, column=6, padx=10)
        self.start_button = ttk.Button(control_frame, text='开始', command=self.toggle_run)
        self.start_button.grid(row=1, column=6, padx=10)

        # CSV目录输入（新行）
        ttk.Label(control_frame, text='csv_dir').grid(row=1, column=0, sticky='w')
        self.csv_dir_entry = ttk.Entry(control_frame, width=50)
        self.csv_dir_entry.grid(row=1, column=1, columnspan=5, padx=5, sticky='we')
        self.csv_dir_entry.insert(0, os.getcwd())

        # STM32 端口输入（第三行）
        ttk.Label(control_frame, text='stm32_port').grid(row=2, column=0, sticky='w')
        self.stm32_port_entry = ttk.Entry(control_frame, width=30)
        self.stm32_port_entry.grid(row=2, column=1, padx=5)
        self.stm32_port_entry.insert(0, 'COM14')

        # 绘图区域：Arduino 在上，STM32 在下
        self.plot_frame = ttk.Frame(self.root)
        self.plot_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.stm32_plot_frame = ttk.Frame(self.root)
        self.stm32_plot_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 关闭窗口时清理资源
        self.root.protocol('WM_DELETE_WINDOW', self.on_close)

        # 启动HTTP控制服务（允许MATLAB远程控制开始/停止）
        try:
            ctrl_port = int(os.environ.get('PY_UI_CTRL_PORT', '8765'))
        except Exception:
            ctrl_port = 8765
        self.ctrl_server = start_control_server(self, ctrl_port)

    def parse_bool_text(self, s):
        v = (s or '').strip().lower()
        return v in ('1', 'true', 't', 'yes', 'y', 'on')
    
    def connect_devices(self):
        if self.is_running:
            print('当前正在采集数据，请先停止再重新连接')
            return
        self.is_connected = False
        try:
            self.connect_button.configure(text='连接')
        except Exception:
            pass
        port = self.port_entry.get().strip()
        no_csv_text = self.no_csv_entry.get().strip()
        delay_text = self.delay_entry.get().strip()
        csv_dir_text = self.csv_dir_entry.get().strip()
        stm32_port = self.stm32_port_entry.get().strip()
        if not port:
            print('错误: port 不能为空')
            return
        if not stm32_port:
            print('错误: stm32_port 不能为空')
            return
        no_csv = self.parse_bool_text(no_csv_text)
        try:
            delay = int(delay_text) if delay_text else 7
        except ValueError:
            print('警告: delay 非法，使用默认 7 秒')
            delay = 7
        csv_dir = csv_dir_text if csv_dir_text else None
        if (self.reader and getattr(self.reader, 'ser', None) and getattr(self.reader.ser, 'is_open', False) and
                self.stm32_reader and getattr(self.stm32_reader, 'ser', None) and getattr(self.stm32_reader.ser, 'is_open', False)):
            print('Arduino 和 STM32 已连接，无需重复连接')
            self.is_connected = True
            try:
                self.connect_button.configure(text='已连接')
            except Exception:
                pass
            return
        self.reader = ArduinoDataReader(port=port, save_to_csv=not no_csv, start_delay_s=delay, csv_dir=csv_dir)
        if not self.reader.connect():
            print('无法连接 Arduino，请检查端口或设备连接')
            self.reader = None
            return
        self.stm32_reader = STM32DataReader(port=stm32_port, save_to_csv=not no_csv, csv_dir=csv_dir)
        if not self.stm32_reader.connect():
            print('无法连接 STM32，请检查端口或设备连接')
            try:
                if self.reader and getattr(self.reader, 'ser', None):
                    self.reader.ser.close()
            except Exception:
                pass
            self.reader = None
            self.stm32_reader = None
            return
        self.is_connected = True
        try:
            self.connect_button.configure(text='已连接')
        except Exception:
            pass
        print('已成功连接 Arduino 和 STM32，等待开始采集...')

    def toggle_run(self):
        if not self.is_running:
            if self.reader is not None and self.stm32_reader is not None:
                if not self.reader.start():
                    print('无法启动数据读取，请检查端口或设备连接')
                    return
                if not self.stm32_reader.start():
                    print('无法启动 STM32 数据读取，请检查端口或设备连接')
                    try:
                        if self.reader:
                            self.reader.stop()
                    except Exception:
                        pass
                    return
            else:
                port = self.port_entry.get().strip()
                no_csv_text = self.no_csv_entry.get().strip()
                delay_text = self.delay_entry.get().strip()
                csv_dir_text = self.csv_dir_entry.get().strip()
                stm32_port = self.stm32_port_entry.get().strip()
                if not port:
                    print('错误: port 不能为空')
                    return
                if not stm32_port:
                    print('错误: stm32_port 不能为空')
                    return
                no_csv = self.parse_bool_text(no_csv_text)
                try:
                    delay = int(delay_text) if delay_text else 7
                except ValueError:
                    print('警告: delay 非法，使用默认 7 秒')
                    delay = 7
                csv_dir = csv_dir_text if csv_dir_text else None
                self.reader = ArduinoDataReader(port=port, save_to_csv=not no_csv, start_delay_s=delay, csv_dir=csv_dir)
                if not self.reader.start():
                    print('无法启动数据读取，请检查端口或设备连接')
                    self.reader = None
                    return
                self.stm32_reader = STM32DataReader(port=stm32_port, save_to_csv=not no_csv, csv_dir=csv_dir)
                if not self.stm32_reader.start():
                    print('无法启动 STM32 数据读取，请检查端口或设备连接')
                    try:
                        if self.reader:
                            self.reader.stop()
                    except Exception:
                        pass
                    self.reader = None
                    return

            # 创建绘图器并嵌入到 Tkinter
            self.plotter = RealtimePlotter(self.reader)
            self.canvas = FigureCanvasTkAgg(self.plotter.fig, master=self.plot_frame)
            self.canvas.draw()
            self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
            self.plotter.start()

            # STM32 绘图器并嵌入到 Tkinter
            self.stm32_plotter = STM32Plotter(self.stm32_reader)
            self.stm32_canvas = FigureCanvasTkAgg(self.stm32_plotter.fig, master=self.stm32_plot_frame)
            self.stm32_canvas.draw()
            self.stm32_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
            self.stm32_plotter.start()

            # 更新状态与UI
            self.is_running = True
            self.start_button.configure(text='停止')
            self.port_entry.configure(state='disabled')
            self.no_csv_entry.configure(state='disabled')
            self.delay_entry.configure(state='disabled')
            self.csv_dir_entry.configure(state='disabled')
            self.stm32_port_entry.configure(state='disabled')
        else:
            # 停止绘图与数据读取
            try:
                if self.plotter:
                    self.plotter.stop()
            except Exception:
                pass
            try:
                if self.reader:
                    self.reader.stop()
            except Exception:
                pass
            try:
                if self.stm32_plotter:
                    self.stm32_plotter.stop()
            except Exception:
                pass
            try:
                if self.stm32_reader:
                    self.stm32_reader.stop()
            except Exception:
                pass

            # 清理画布
            try:
                if self.canvas:
                    self.canvas.get_tk_widget().destroy()
            except Exception:
                pass
            self.canvas = None
            self.plotter = None

            try:
                if self.stm32_canvas:
                    self.stm32_canvas.get_tk_widget().destroy()
            except Exception:
                pass
            self.stm32_canvas = None
            self.stm32_plotter = None

            # 更新状态与UI
            self.is_running = False
            self.start_button.configure(text='开始')
            self.port_entry.configure(state='normal')
            self.no_csv_entry.configure(state='normal')
            self.delay_entry.configure(state='normal')
            self.csv_dir_entry.configure(state='normal')
            self.stm32_port_entry.configure(state='normal')

    def on_close(self):
        # 确保停止后再退出
        if self.is_running:
            try:
                if self.plotter:
                    self.plotter.stop()
            except Exception:
                pass
            try:
                if self.reader:
                    self.reader.stop()
            except Exception:
                pass
            try:
                if self.stm32_plotter:
                    self.stm32_plotter.stop()
            except Exception:
                pass
            try:
                if self.stm32_reader:
                    self.stm32_reader.stop()
            except Exception:
                pass
        # 关闭HTTP控制服务
        try:
            if hasattr(self, 'ctrl_server') and self.ctrl_server:
                self.ctrl_server.shutdown()
                self.ctrl_server.server_close()
        except Exception:
            pass
        self.root.destroy()

def main():
    """GUI entry point"""
    root = tk.Tk()
    app = App(root)
    root.mainloop()

if __name__ == "__main__":
    main()