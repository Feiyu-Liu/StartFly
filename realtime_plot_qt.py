import os
import threading
import queue
import numpy as np
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.parse as urlparse
from PySide6 import QtWidgets, QtCore
import pyqtgraph as pg
from datetime import datetime
from realtime_plot_ui import ArduinoDataReader, STM32DataReader

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
        return
    def do_GET(self):
        parsed = urlparse.urlparse(self.path)
        path = parsed.path
        app = ControlRequestHandler.app_ref
        if app is None:
            self._ok('NO_APP')
            return
        if path == '/start_all':
            QtCore.QTimer.singleShot(0, lambda: (not app.is_running) and app.toggle_run())
            self._ok('STARTED')
        elif path == '/stop_all':
            QtCore.QTimer.singleShot(0, lambda: app.is_running and app.toggle_run())
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

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Arduino/STM32 Real-time Monitor (Qt + pyqtgraph)')
        self.is_running = False
        self.is_connected = False
        self.reader = None
        self.stm32_reader = None
        self.arduino_timer = None
        self.stm32_timer = None
        self.debounce_timer = QtCore.QTimer(self)
        self.debounce_timer.setSingleShot(True)
        self.debounce_timer.timeout.connect(self._resume_timers)
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        self.layout = QtWidgets.QVBoxLayout(central)
        control = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(control)
        self.layout.addWidget(control)
        grid.addWidget(QtWidgets.QLabel('port'), 0, 0)
        self.port_edit = QtWidgets.QLineEdit('COM5')
        grid.addWidget(self.port_edit, 0, 1)
        grid.addWidget(QtWidgets.QLabel('no-csv'), 0, 2)
        self.no_csv_edit = QtWidgets.QLineEdit('0')
        grid.addWidget(self.no_csv_edit, 0, 3)
        grid.addWidget(QtWidgets.QLabel('delay(s)'), 0, 4)
        self.delay_edit = QtWidgets.QLineEdit('4')
        grid.addWidget(self.delay_edit, 0, 5)
        self.connect_btn = QtWidgets.QPushButton('连接')
        self.connect_btn.clicked.connect(self.connect_devices)
        grid.addWidget(self.connect_btn, 0, 6)
        self.start_btn = QtWidgets.QPushButton('开始')
        self.start_btn.clicked.connect(self.toggle_run)
        grid.addWidget(self.start_btn, 1, 6)
        grid.addWidget(QtWidgets.QLabel('csv_dir'), 1, 0)
        self.csv_dir_edit = QtWidgets.QLineEdit(os.getcwd())
        grid.addWidget(self.csv_dir_edit, 1, 1, 1, 5)
        grid.addWidget(QtWidgets.QLabel('stm32_port'), 2, 0)
        self.stm32_port_edit = QtWidgets.QLineEdit('COM14')
        grid.addWidget(self.stm32_port_edit, 2, 1)
        self.arduino_plot = pg.PlotWidget()
        self.stm32_plot = pg.PlotWidget()
        self.layout.addWidget(self.arduino_plot)
        self.layout.addWidget(self.stm32_plot)
        self._init_plots()
        self.installEventFilter(self)
        try:
            ctrl_port = int(os.environ.get('PY_UI_CTRL_PORT', '8765'))
        except Exception:
            ctrl_port = 8765
        self.ctrl_server = start_control_server(self, ctrl_port)

    def eventFilter(self, obj, event):
        if event.type() in (QtCore.QEvent.Resize, QtCore.QEvent.Move):
            self._pause_timers()
            self.debounce_timer.start(200)
        return super().eventFilter(obj, event)

    def _init_plots(self):
        self.arduino_plot.setTitle('Real-time Data Monitor')
        self.arduino_plot.showGrid(x=True, y=True)
        self.ax1_curve = self.arduino_plot.plot(pen=pg.mkPen('b', width=2))
        self.sync_curve = self.arduino_plot.plot(pen=pg.mkPen('g', width=2))
        self.magnet_curve = self.arduino_plot.plot(pen=pg.mkPen('r', width=2))
        self.arduino_plot.setLabel('bottom', 'Time', units='s')
        self.arduino_plot.setLabel('left', 'Analog Value')
        self.arduino_plot.setYRange(0, 1023)
        self.stm32_plot.setTitle('STM32 ADC Real-time Data Stream')
        self.stm32_plot.showGrid(x=True, y=True)
        self.stm32_plot.setLabel('bottom', 'Sample Points')
        self.stm32_plot.setLabel('left', 'ADC Reading')
        self.stm32_plot.setXRange(0, 512)
        self.stm32_plot.setYRange(0, 4095)
        self.stm32_lines = [self.stm32_plot.plot(pen=pg.mkPen(pg.intColor(i, hues=10), width=1.5)) for i in range(10)]
        self.stm32_status = pg.LabelItem(justify='left')
        self.stm32_plot.addItem(self.stm32_status)
        self.timestamps = []
        self.val1_data = []
        self.sync_flags = []
        self.magnet_flags = []
        self.window_seconds = 10

    def parse_bool_text(self, s):
        v = (s or '').strip().lower()
        return v in ('1', 'true', 't', 'yes', 'y', 'on')

    def connect_devices(self):
        if self.is_running:
            print('当前正在采集数据，请先停止再重新连接')
            return
        self.is_connected = False
        self.connect_btn.setText('连接')
        port = self.port_edit.text().strip()
        no_csv_text = self.no_csv_edit.text().strip()
        delay_text = self.delay_edit.text().strip()
        csv_dir_text = self.csv_dir_edit.text().strip()
        stm32_port = self.stm32_port_edit.text().strip()
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
        self.connect_btn.setText('已连接')
        print('已成功连接 Arduino 和 STM32，等待开始采集...')

    def toggle_run(self):
        if not self.is_running:
            if self.reader is None or self.stm32_reader is None:
                self.connect_devices()
                if not self.is_connected:
                    return
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
            self.arduino_timer = QtCore.QTimer(self)
            self.arduino_timer.timeout.connect(self._update_arduino_plot)
            self.arduino_timer.start(10)
            self.stm32_timer = QtCore.QTimer(self)
            self.stm32_timer.timeout.connect(self._update_stm32_plot)
            self.stm32_timer.start(10)
            self.is_running = True
            self.start_btn.setText('停止')
            self.port_edit.setEnabled(False)
            self.no_csv_edit.setEnabled(False)
            self.delay_edit.setEnabled(False)
            self.csv_dir_edit.setEnabled(False)
            self.stm32_port_edit.setEnabled(False)
        else:
            try:
                if self.arduino_timer:
                    self.arduino_timer.stop()
            except Exception:
                pass
            try:
                if self.reader:
                    self.reader.stop()
            except Exception:
                pass
            try:
                if self.stm32_timer:
                    self.stm32_timer.stop()
            except Exception:
                pass
            try:
                if self.stm32_reader:
                    self.stm32_reader.stop()
            except Exception:
                pass
            self.is_running = False
            self.start_btn.setText('开始')
            self.port_edit.setEnabled(True)
            self.no_csv_edit.setEnabled(True)
            self.delay_edit.setEnabled(True)
            self.csv_dir_edit.setEnabled(True)
            self.stm32_port_edit.setEnabled(True)

    def _update_arduino_plot(self):
        new_data = []
        while True:
            try:
                data = self.reader.data_queue.get_nowait()
                new_data.append(data)
            except queue.Empty:
                break
        if not new_data:
            return
        for frame_us32, val1, sync_us32, magnet_us32 in new_data:
            t_seconds = frame_us32 / 1_000_000.0
            self.timestamps.append(t_seconds)
            self.val1_data.append(val1)
            self.sync_flags.append(1 if sync_us32 is not None else np.nan)
            self.magnet_flags.append(1 if magnet_us32 is not None else np.nan)
        if not self.timestamps:
            return
        t = np.array(self.timestamps, dtype=float)
        y = np.array(self.val1_data, dtype=float)
        self.ax1_curve.setData(t, y)
        sx = []
        sy = []
        for tt, f in zip(t, self.sync_flags):
            if not np.isnan(f):
                sx.extend([tt, tt, np.nan])
                sy.extend([0, 1, np.nan])
        mx = []
        my = []
        for tt, f in zip(t, self.magnet_flags):
            if not np.isnan(f):
                mx.extend([tt, tt, np.nan])
                my.extend([0, 1, np.nan])
        if sx:
            self.sync_curve.setData(np.array(sx, dtype=float), np.array(sy, dtype=float))
        else:
            self.sync_curve.setData([], [])
        if mx:
            self.magnet_curve.setData(np.array(mx, dtype=float), np.array(my, dtype=float))
        else:
            self.magnet_curve.setData([], [])
        last_t = t[-1]
        first_t = last_t - self.window_seconds
        self.arduino_plot.setXRange(first_t, last_t, padding=0)

    def _update_stm32_plot(self):
        with self.stm32_reader.lock:
            for i, line in enumerate(self.stm32_lines):
                buf = list(self.stm32_reader.buffers[i])
                x = np.arange(len(buf), dtype=float)
                y = np.array(buf, dtype=float)
                line.setData(x, y)
        self.stm32_status.setText(f'Packets: {self.stm32_reader.packets_received}  Sync TTL: {self.stm32_reader.last_sync_ts or "N/A"}  Trigger TTL: {self.stm32_reader.last_trigger_ts or "N/A"}')

    def _pause_timers(self):
        if self.is_running:
            try:
                if self.arduino_timer and self.arduino_timer.isActive():
                    self.arduino_timer.stop()
                if self.stm32_timer and self.stm32_timer.isActive():
                    self.stm32_timer.stop()
            except Exception:
                pass

    def _resume_timers(self):
        if self.is_running:
            try:
                if self.arduino_timer and not self.arduino_timer.isActive():
                    self.arduino_timer.start(20)
                if self.stm32_timer and not self.stm32_timer.isActive():
                    self.stm32_timer.start(50)
            except Exception:
                pass

    def closeEvent(self, event):
        if self.is_running:
            try:
                if self.arduino_timer:
                    self.arduino_timer.stop()
                if self.reader:
                    self.reader.stop()
                if self.stm32_timer:
                    self.stm32_timer.stop()
                if self.stm32_reader:
                    self.stm32_reader.stop()
            except Exception:
                pass
        try:
            if hasattr(self, 'ctrl_server') and self.ctrl_server:
                self.ctrl_server.shutdown()
                self.ctrl_server.server_close()
        except Exception:
            pass
        super().closeEvent(event)

def main():
    app = QtWidgets.QApplication([])
    w = MainWindow()
    w.resize(900, 800)
    w.show()
    app.exec()

if __name__ == '__main__':
    main()

