import serial
import struct
import time

port = "COM5"   # ⚠️ 修改为你的Arduino串口号
baudrate = 921600

def monitor_arduino_frame_rate(duration=5):
    """
    连接Arduino，发送换行符，计算并打印帧率
    
    Args:
        duration: 监控时长（秒），默认为5秒
    """
    try:
        # 连接Arduino
        print(f"正在连接Arduino，端口: {port}")
        ser = serial.Serial(port, baudrate, timeout=0.01)
        time.sleep(2)  # 等待Arduino重启
        print("成功连接到Arduino")
        
        # 发送换行符
        ser.write(b'\n')
        print("已发送换行符，开始接收数据...")
        
        # 开始监控帧率
        frame_count = 0
        last_time = time.perf_counter()
        start_time = last_time
        
        print(f"开始监控帧率，持续{duration}秒... 按Ctrl+C停止")
        
        while time.perf_counter() - start_time < duration:
            if ser.in_waiting >= 3:  # 帧头0xAA + 2字节数据
                header = ser.read(1)
                if header == b'\xAA':
                    raw = ser.read(2)
                    if len(raw) == 2:
                        val = struct.unpack("<H", raw)[0]  # 小端2字节无符号
                        frame_count += 1

                        # 每秒统计一次帧率
                        now = time.perf_counter()
                        if now - last_time >= 1.0:
                            fps = frame_count / (now - last_time)
                            elapsed = now - start_time
                            avg_fps = frame_count / elapsed if elapsed > 0 else 0
                            print(f"瞬时帧率: {fps:.1f} Hz, 平均帧率: {avg_fps:.1f} Hz, 帧数: {frame_count}, 数据值: {val}")
                            frame_count = 0
                            last_time = now
                else:
                    # 同步失败，继续找帧头
                    continue
                    
        # 最终统计
        total_time = time.perf_counter() - start_time
        final_fps = frame_count / total_time if total_time > 0 else 0
        
        print(f"\n监控完成!")
        print(f"总时间: {total_time:.2f} 秒")
        print(f"总帧数: {frame_count}")
        print(f"平均帧率: {final_fps:.2f} FPS")
        print(f"期望帧率: 2500.00 FPS")
        print(f"帧率达成率: {(final_fps/2500)*100:.2f}%")
        
    except serial.SerialException as e:
        print(f"串口连接失败: {e}")
    except KeyboardInterrupt:
        print("\n用户中断监控")
    except Exception as e:
        print(f"发生错误: {e}")
    finally:
        if 'ser' in locals() and ser.is_open:
            ser.close()
            print("已断开与Arduino的连接")

if __name__ == "__main__":
    # 可以根据需要修改参数
    monitor_arduino_frame_rate(duration=10)