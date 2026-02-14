import serial
import time
import sys

# CONFIGURATION
# REPLACE 'COM3' with your actual port (e.g., 'COM3' on Windows, '/dev/ttyACM0' on Linux/Mac)
SERIAL_PORT = 'COM6' 
BAUD_RATE = 115200

def run_test():
    print(f"Attempting to connect to {SERIAL_PORT}...")
    
    try:
        # Open Serial Connection
        arduino = serial.Serial(port=SERIAL_PORT, baudrate=BAUD_RATE, timeout=2)
        time.sleep(2) # Wait for Arduino to reset
        
        print("Connected! Waiting for messages (Press Ctrl+C to stop)...\n")
        
        while True:
            # Read a line from Arduino
            if arduino.in_waiting > 0:
                line = arduino.readline().decode('utf-8').strip()
                print(f"Received: {line}")
                
    except serial.SerialException:
        print(f"ERROR: Could not open {SERIAL_PORT}. Check your cable and port name.")
    except KeyboardInterrupt:
        print("\nTest stopped by user.")
    finally:
        if 'arduino' in locals() and arduino.is_open:
            arduino.close()
            print("Serial connection closed.")

if __name__ == "__main__":
    run_test()