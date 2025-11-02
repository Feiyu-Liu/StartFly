% 可选：指定控制端口
setenv('PY_UI_CTRL_PORT','8765');  % 修改端口时也需在 webread 中同步


% 开始两路采集与绘图
webread('http://127.0.0.1:8765/start_all');

% 运行一段时间
pause(10);

% 停止两路采集与绘图
webread('http://127.0.0.1:8765/stop_all');

% 查询状态（可选）
status = webread('http://127.0.0.1:8765/status');
disp(status);