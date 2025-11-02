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
# Import only when plotting is needed to avoid errors in headless environments
plt = None
animation = None

# --- Frame Structure Definition (must match STM32 code) ---
FRAME_HEADER = b'\xA5\x5A'
EXPECTED_PAYLOAD_SIZE = 1292
NUM_CHANNELS = 10
SAMPLES_PER_PACKET = 64
PAYLOAD_UNPACK_FORMAT = f'<{SAMPLES_PER_PACKET * NUM_CHANNELS}H III'
PLOT_BUFFER_SIZE = 512
BAUD_RATE = 921600

# --- Global Variables ---
data_lock = threading.Lock()
plot_buffers = [deque(maxlen=PLOT_BUFFER_SIZE) for _ in range(NUM_CHANNELS)]
stop_event = threading.Event()
packets_received = 0
last_sync_ts = 0
last_trigger_ts = 0
LOGGING_ENABLED = True

# --- Helper Functions ---
def logger(message):
    """Prints log messages based on the global flag."""
    if LOGGING_ENABLED:
        print(message, flush=True)

# --- Core Functions ---
def read_serial_data(com_port, csv_filepath):
    """
    Runs in a separate thread, parses the data stream, writes to CSV, and prints info based on the logging switch.
    """
    global packets_received, last_sync_ts, last_trigger_ts
    try:
        ser = serial.Serial(com_port, BAUD_RATE, timeout=1)
        logger(f"Successfully opened serial port {com_port}...")
    except serial.SerialException as e:
        print(f"Fatal Error: Could not open serial port {com_port}. Please check the connection or port number.", file=sys.stderr)
        print(e, file=sys.stderr)
        stop_event.set() # Send stop signal to terminate the main program
        return

    try:
        csv_file = open(csv_filepath, 'w', newline='', encoding='utf-8')
        csv_writer = csv.writer(csv_file)
        header = [f'adc{i}' for i in range(NUM_CHANNELS)] + ['send_timestamp_us', 'sync_ttl_timestamp_us', 'trigger_ttl_timestamp_us']
        csv_writer.writerow(header)
        logger(f"Data will be logged to: {csv_filepath}")
    except IOError as e:
        print(f"Fatal Error: Could not write to CSV file path '{csv_filepath}'. Please check permissions or path.", file=sys.stderr)
        print(e, file=sys.stderr)
        ser.close()
        stop_event.set()
        return

    sync_state = 0
    while not stop_event.is_set():
        # --- Synchronization State Machine ---
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
                            logger(f"--- SYNC TTL Triggered --- Timestamp: {sync_ts} us")
                            last_sync_ts = sync_ts
                        if trigger_ts != 0xFFFFFFFF:
                            logger(f"--- TRIGGER TTL Triggered --- Timestamp: {trigger_ts} us")
                            last_trigger_ts = trigger_ts
                        adc_array = np.array(adc_values_flat).reshape(SAMPLES_PER_PACKET, NUM_CHANNELS).T
                        for i in range(SAMPLES_PER_PACKET):
                            row = list(adc_array[:, i]) + [send_ts, sync_ts, trigger_ts]
                            csv_writer.writerow(row)
                        with data_lock:
                            for i in range(NUM_CHANNELS):
                                plot_buffers[i].extend(adc_array[i])
                    except struct.error:
                        logger("Payload parsing failed. Resynchronizing...")
                else:
                    logger("Incomplete payload read. Resynchronizing...")
            else:
                logger("Incorrect length field. Resynchronizing...")
            sync_state = 0
        else:
            sync_state = 0

    ser.close()
    csv_file.close()
    logger("Serial port closed, file saved.")

def update_plot(frame, lines, ax, title_text, ts_text):
    """Callback function to update the plot."""
    with data_lock:
        for i, line in enumerate(lines):
            line.set_data(range(len(plot_buffers[i])), plot_buffers[i])
    title_text.set_text(f'STM32 ADC Real-time Data Stream (Packets Received: {packets_received})')
    ts_text.set_text(f'Sync TTL: {last_sync_ts if last_sync_ts != 0 else "N/A"} | Trigger TTL: {last_trigger_ts if last_trigger_ts != 0 else "N/A"}')
    ax.relim()
    ax.autoscale_view(scalex=False, scaley=True)
    return lines + [title_text, ts_text]

def main():
    """Main function, parses arguments, initializes and starts all processes."""
    global LOGGING_ENABLED, plt, animation

    # --- 1. Argument Parsing ---
    parser = argparse.ArgumentParser(description="Receive, log, and optionally display ADC data from an STM32 device.")
    parser.add_argument('-nograph', action='store_true', help='No GUI mode, do not display the real-time plot window.')
    parser.add_argument('-nolog', action='store_true', help='No log mode, do not print trigger events, etc. to the console.')
    parser.add_argument('-comid', type=str, default='COM7', help='Specify the serial port (e.g., COM7 or /dev/ttyUSB0).')
    parser.add_argument('-savein', type=str, default=None, help="Specify the save directory for the CSV log file. Defaults to the script's current directory.")
    args = parser.parse_args()

    # --- 2. Apply Arguments ---
    if args.nolog:
        LOGGING_ENABLED = False

    # --- 3. Handle File Save Path ---
    csv_filename = f'data_log_{time.strftime("%Y%m%d_%H%M%S")}.csv'
    if args.savein:
        save_dir = os.path.abspath(args.savein)
        try:
            if not os.path.isdir(save_dir):
                logger(f"Directory '{save_dir}' does not exist, attempting to create...")
                os.makedirs(save_dir, exist_ok=True)
            csv_filepath = os.path.join(save_dir, csv_filename)
        except OSError as e:
            print(f"Fatal Error: Could not create directory '{save_dir}'. Please check permissions. Error: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        csv_filepath = csv_filename

    # --- 4. Start Core Logic ---
    serial_thread = threading.Thread(target=read_serial_data, args=(args.comid, csv_filepath), daemon=True)
    serial_thread.start()

    # --- 5. Select Foreground Task (GUI or Headless Wait) ---
    if not args.nograph:
        # --- GUI Mode ---
        try:
            global plt, animation
            import matplotlib.pyplot as plt
            import matplotlib.animation as animation
        except ImportError:
            print("Fatal Error: matplotlib library not found, cannot display graphics. Please run 'pip install matplotlib' or use '-nograph' mode.", file=sys.stderr)
            stop_event.set()
            serial_thread.join()
            sys.exit(1)

        fig, ax = plt.subplots(figsize=(15, 8))
        plt.subplots_adjust(bottom=0.1, top=0.9)
        title_text = ax.set_title('STM32 ADC Real-time Data Stream (Waiting for data...)', fontsize=16)
        ts_text = ax.text(0.01, 0.95, '', transform=ax.transAxes, fontsize=12, verticalalignment='top', bbox=dict(boxstyle='round,pad=0.5', fc='wheat', alpha=0.5))
        ax.set_xlabel('Sample Points (last 512)', fontsize=12)
        ax.set_ylabel('ADC Reading (0-4095)', fontsize=12)
        ax.set_xlim(0, PLOT_BUFFER_SIZE)
        ax.set_ylim(0, 4096)
        ax.grid(True)
        colors = plt.cm.get_cmap('tab10', NUM_CHANNELS)
        lines = [ax.plot([], [], lw=1.5, color=colors(i), label=f'ADC {i}')[0] for i in range(NUM_CHANNELS)]
        ax.legend(loc='upper right')
        
        ani = animation.FuncAnimation(fig, update_plot, fargs=(lines, ax, title_text, ts_text), interval=50, blit=True)

        def on_close(event):
            logger("Plot window closed, stopping program...")
            stop_event.set()

        fig.canvas.mpl_connect('close_event', on_close)
        plt.show()
    else:
        # --- Headless Mode ---
        logger("Entering headless mode. Press Ctrl+C to stop.")
        try:
            # Keep the main thread alive until the serial thread exits due to an error or an external signal
            while serial_thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            logger("Received Ctrl+C, stopping program...")
            stop_event.set()
    
    # --- 6. Cleanup ---
    serial_thread.join(timeout=2)
    logger("Program exited.")

if __name__ == '__main__':
    main()
