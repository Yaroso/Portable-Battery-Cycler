import sys
import time
import csv
import serial
import queue
import os
from datetime import datetime
from collections import deque
from PyQt6 import QtWidgets, QtCore
from PyQt6.QtWidgets import QMessageBox
from PyQt6.QtGui import QIntValidator
import pyqtgraph as pg

# --- LIMITS DICTIONARY ---
LOAD_LIMITS = {
    "SUPERCAP": {"floor": 0.05, "ceil": 2.30},
    "NIMH": {"floor": 1.00, "ceil": 1.45},
    "RESISTOR": {"floor": -2.30, "ceil": 2.30},
    "CUSTOM": {"floor": -4.80, "ceil": 4.80}
}

# --- THREAD 1: Serial I/O & Logging ---
class SerialWorker(QtCore.QThread):
    new_data_signal = QtCore.pyqtSignal(dict)

    def __init__(self, port, dut_serial, baudrate=115200, log_file="cycler_log.csv"):
        super().__init__()
        self.port = port
        self.dut_serial = dut_serial
        self.baudrate = baudrate
        self.log_file = log_file
        self.is_running = True
        self.command_queue = queue.Queue()
        
    def send_command(self, cmd):
        """Called by GUI thread to queue commands to send to Arduino"""
        self.command_queue.put(cmd)

    def run(self):
        try:
            # Timeout is critical: allows checking the command queue even if no data is received
            ser = serial.Serial(self.port, self.baudrate, timeout=0.1)
        except Exception as e:
            print(f"Failed to connect to {self.port}: {e}")
            return

        with open(self.log_file, 'a', newline='') as f:
            csv_writer = csv.writer(f)
            # Write metadata and header if file is empty
            if f.tell() == 0:
                csv_writer.writerow(["# METADATA"])
                csv_writer.writerow([f"# Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"])
                csv_writer.writerow([f"# DUT Serial: {self.dut_serial}"])
                csv_writer.writerow(["# --------------------------------------------------"])
                csv_writer.writerow(["Timestamp", "State", "LoadType", "TargetV", "CellV", "Current_mA", "Charge_mAh", "Discharge_mAh"])

            start_time = time.time()

            while self.is_running:
                # 1. Write any pending commands to Serial
                while not self.command_queue.empty():
                    cmd = self.command_queue.get()
                    ser.write(f"{cmd}\n".encode('utf-8'))
                    print(f"Sent: {cmd}")

                # 2. Read incoming data
                try:
                    line = ser.readline().decode('utf-8').strip()
                    if line.startswith("DATA,"):
                        parts = line.split(',')
                        if len(parts) >= 8:
                            timestamp = time.time() - start_time
                            state = parts[1].strip()
                            load_type = parts[2].strip()
                            target_v = float(parts[3])
                            cell_v = float(parts[4])
                            current_ma = float(parts[5])
                            charge_mah = float(parts[6])
                            discharge_mah = float(parts[7])

                            # Write to CSV
                            csv_writer.writerow([timestamp, state, load_type, target_v, cell_v, current_ma, charge_mah, discharge_mah])
                            f.flush() # Ensure data writes to disk immediately

                            # Emit to GUI
                            self.new_data_signal.emit({
                                "time": timestamp,
                                "state": state,
                                "load_type": load_type,
                                "target_v": target_v,
                                "cell_v": cell_v,
                                "current_ma": current_ma,
                                "charge_mah": charge_mah,
                                "discharge_mah": discharge_mah
                            })
                except Exception as e:
                    print(f"Serial Read/Parse Error: {e}")

        ser.close()

    def stop(self):
        self.is_running = False
        self.wait()


# --- THREAD 2: GUI ---
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Battery Cycler DAQ")
        self.resize(1000, 800)

        # Buffers for plotting (maxlen prevents memory leaks)
        self.plot_points = 1000
        self.time_data = deque(maxlen=self.plot_points)
        self.volt_data = deque(maxlen=self.plot_points)
        self.curr_data = deque(maxlen=self.plot_points)

        # Ensure test-data directory exists
        os.makedirs("test-data", exist_ok=True)
        self.setup_ui()
        
    def setup_ui(self):
        central_widget = QtWidgets.QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QtWidgets.QVBoxLayout(central_widget)

        # --- Test Configuration Panel ---
        config_layout = QtWidgets.QHBoxLayout()
        
        self.serial_input = QtWidgets.QLineEdit()
        self.serial_input.setPlaceholderText("7-digit Serial (e.g. 0000001)")
        self.serial_input.setMaxLength(7)
        # Force integer input only
        self.serial_input.setValidator(QIntValidator(0, 9999999, self)) 
        
        self.btn_start_test = QtWidgets.QPushButton("START TEST RUN")
        self.btn_start_test.setStyleSheet("background-color: darkgreen; color: white; font-weight: bold;")
        self.btn_start_test.clicked.connect(self.start_new_test)

        config_layout.addWidget(QtWidgets.QLabel("DUT Serial:"))
        config_layout.addWidget(self.serial_input)
        config_layout.addWidget(self.btn_start_test)
        
        main_layout.addLayout(config_layout)

        # --- Control Panel ---
        control_layout = QtWidgets.QHBoxLayout()
        
        # Load Command (Dropdown)
        self.load_dropdown = QtWidgets.QComboBox()
        self.load_dropdown.addItems(["SUPERCAP", "NIMH", "RESISTOR", "CUSTOM"])
        self.btn_load = QtWidgets.QPushButton("Set LOAD")
        self.btn_load.clicked.connect(self.send_load_command)
        
        control_layout.addWidget(QtWidgets.QLabel("Load Type:"))
        control_layout.addWidget(self.load_dropdown)
        control_layout.addWidget(self.btn_load)

        # Target Voltage Command
        self.target_input = QtWidgets.QDoubleSpinBox()
        self.target_input.setRange(-5.0, 10.0) # Broad range, restricted by Poka-yoke logic
        self.target_input.setSingleStep(0.1)
        self.target_input.setValue(2.30)
        self.btn_target = QtWidgets.QPushButton("Set TARGET")
        self.btn_target.clicked.connect(self.send_target_command)
        
        control_layout.addWidget(QtWidgets.QLabel("Target V:"))
        control_layout.addWidget(self.target_input)
        control_layout.addWidget(self.btn_target)

        # PWM Command
        self.pwm_input = QtWidgets.QSpinBox()
        self.pwm_input.setRange(0, 255)
        self.pwm_input.setValue(130) # Hardware off state
        self.btn_pwm = QtWidgets.QPushButton("Set PWM")
        self.btn_pwm.clicked.connect(self.send_pwm_command)
        
        control_layout.addWidget(QtWidgets.QLabel("PWM (0-255):"))
        control_layout.addWidget(self.pwm_input)
        control_layout.addWidget(self.btn_pwm)

        main_layout.addLayout(control_layout)

        # --- Live Readouts ---
        self.readout_label = QtWidgets.QLabel("Waiting for test to start...")
        self.readout_label.setFont(pg.QtGui.QFont("Monospace", 12, pg.QtGui.QFont.Weight.Bold))
        self.readout_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(self.readout_label)

        # --- Plots ---
        pg.setConfigOptions(antialias=True)
        
        # Voltage Plot
        self.plot_v = pg.PlotWidget(title="Cell Voltage vs Time")
        self.plot_v.setLabel('left', 'Voltage', units='V')
        self.plot_v.setLabel('bottom', 'Time', units='s')
        self.plot_v.showGrid(x=True, y=True)
        self.line_v = self.plot_v.plot(pen=pg.mkPen('y', width=2))
        main_layout.addWidget(self.plot_v)

        # Current Plot
        self.plot_i = pg.PlotWidget(title="Current vs Time")
        self.plot_i.setLabel('left', 'Current', units='mA')
        self.plot_i.setLabel('bottom', 'Time', units='s')
        self.plot_i.showGrid(x=True, y=True)
        self.line_i = self.plot_i.plot(pen=pg.mkPen('c', width=2))
        main_layout.addWidget(self.plot_i)

    def start_new_test(self):
        """Initializes logging with strict traceability"""
        dut_serial = self.serial_input.text()
        if len(dut_serial) != 7:
            QMessageBox.warning(self, "Validation Error", "Please enter exactly a 7-digit Serial Number.")
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"test-data/DUT_{dut_serial}_{timestamp}.csv"
        
        # Start the worker thread with the new file and serial number
        self.worker = SerialWorker(port='COM6', dut_serial=dut_serial, baudrate=115200, log_file=filename)
        self.worker.new_data_signal.connect(self.update_gui)
        self.worker.start()
        
        # Lock the config UI so they can't change it mid-test
        self.serial_input.setEnabled(False)
        self.btn_start_test.setEnabled(False)
        self.btn_start_test.setText(f"LOGGING TO: DUT_{dut_serial}")

    def send_load_command(self):
        if not hasattr(self, 'worker') or not self.worker.is_running:
            QMessageBox.warning(self, "Error", "Please start a test run first.")
            return
            
        load_type = self.load_dropdown.currentText()
        self.worker.send_command(f"LOAD {load_type}")

    def send_target_command(self):
        if not hasattr(self, 'worker') or not self.worker.is_running:
            QMessageBox.warning(self, "Error", "Please start a test run first.")
            return
            
        target_v = self.target_input.value()
        load_type = self.load_dropdown.currentText()
        limits = LOAD_LIMITS[load_type]

        # Software-side Poka-yoke limit checking
        if target_v > limits["ceil"]:
            QMessageBox.critical(self, "Limit Exceeded", f"Target Voltage ({target_v}V) exceeds the maximum ceiling ({limits['ceil']}V) for {load_type}!\n\nCommand Aborted.")
            self.target_input.setValue(limits["ceil"]) # Snap back to safe limit
            return
            
        if target_v < limits["floor"]:
            QMessageBox.critical(self, "Limit Exceeded", f"Target Voltage ({target_v}V) is below the safe floor ({limits['floor']}V) for {load_type}!\n\nCommand Aborted.")
            self.target_input.setValue(limits["floor"]) # Snap back to safe limit
            return

        # If it passes checks, send it
        self.worker.send_command(f"TARGET {target_v:.2f}")

    def send_pwm_command(self):
        if not hasattr(self, 'worker') or not self.worker.is_running:
            QMessageBox.warning(self, "Error", "Please start a test run first.")
            return
            
        self.worker.send_command(f"PWM {self.pwm_input.value()}")

    def update_gui(self, data):
        """Slot called every time the worker thread emits new data."""
        # Update text readout
        status_text = (f"STATE: {data['state']} | LOAD: {data['load_type']} | "
                       f"V_cell: {data['cell_v']:.3f}V (Tgt: {data['target_v']:.2f}V) | "
                       f"I: {data['current_ma']:.1f}mA | "
                       f"Chg: {data['charge_mah']:.4f}mAh | Dchg: {data['discharge_mah']:.4f}mAh")
        self.readout_label.setText(status_text)

        # Update plot buffers
        self.time_data.append(data['time'])
        self.volt_data.append(data['cell_v'])
        self.curr_data.append(data['current_ma'])

        # Redraw plots
        self.line_v.setData(self.time_data, self.volt_data)
        self.line_i.setData(self.time_data, self.curr_data)

    def closeEvent(self, event):
        """Safely shutdown the thread when window is closed."""
        if hasattr(self, 'worker') and self.worker.is_running:
            self.worker.stop()
        event.accept()

if __name__ == '__main__':
    app = QtWidgets.QApplication(sys.argv)
    
    # Set dark theme for better visibility with Pyqtgraph default colors
    app.setStyle("Fusion")
    
    main_win = MainWindow()
    main_win.show()
    sys.exit(app.exec())