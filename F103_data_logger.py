import serial
import struct
import time

def main(port="COM5", baudrate=230400):
    """
    Connects to an Arduino, reads 5-byte data frames, and prints the parsed data.
    Frame format: 0xAA (header) + 2-byte ADC value + 2-byte timestamp
    """
    FRAME_SIZE = 5
    print(f"Attempting to connect to {port} at {baudrate} baud...")

    try:
        ser = serial.Serial(port, baudrate, timeout=1)
        time.sleep(2)  # Wait for Arduino to reset
        print("Connection successful. Sending start signal (newline)...")
        ser.write(b'\n')

        print("Starting data log. Press Ctrl+C to stop.")
        print("-" * 40)
        print("{:<15} | {:<10} | {:<10}".format("Time", "ADC Value", "Timestamp"))
        print("-" * 40)

        triggered_once = False
        while True:
            if ser.in_waiting >= FRAME_SIZE:
                # Look for the header byte 0xAA
                header = ser.read(1)
                if header == b'\xAA':
                    # Read the rest of the frame
                    payload = ser.read(FRAME_SIZE - 1)
                    if len(payload) == FRAME_SIZE - 1:
                        # Unpack the data: <H is little-endian unsigned short (2 bytes)
                        adc_val = struct.unpack('<H', payload[0:2])[0]
                        timestamp_val = struct.unpack('<H', payload[2:4])[0]
                        
                        if not triggered_once and timestamp_val != 0xFFFF:
                            triggered_once = True
                        
                        current_time = time.strftime('%H:%M:%S', time.localtime())
                        print(f"\r{current_time:<15} | {adc_val:<10} | {timestamp_val:<10}", end='')
                        
                        if triggered_once:
                            print(" (TRIGGER!)   ", end='')
                        else:
                            print(" (No trigger) ", end='')

            # Small delay to prevent busy-waiting, but allow fast reading
            time.sleep(0.0001)

    except serial.SerialException as e:
        print(f"\nError: Could not open port {port}. {e}")
    except KeyboardInterrupt:
        print("\n\nStopping data logger...")
    finally:
        if 'ser' in locals() and ser.is_open:
            ser.close()
            print("Serial port closed.")

if __name__ == "__main__":
    main()