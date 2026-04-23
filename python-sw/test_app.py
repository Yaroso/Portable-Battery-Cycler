import sys
import time
import csv
import serial
import queue
from collections import deque
from PyQt6 import QtWidgets, QtCore
import pyqtgraph as pg

# --- THREAD 1: Serial I/O & Logging ---
class SerialWorker(QtCore.QThread):
    new_data_signal = QtCore.pyqtSignal(dict)

    def __init__(self, port, baudrate=115200, log_file="cycler_log.csv"):
        super().__init__()
        self.port = port
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
            # Write header if file is empty
            if f.tell() == 0:
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

        self.setup_ui()
        
        # Initialize and start Serial Worker
        # IMPORTANT: Change 'COM6' to your actual Arduino port
        self.worker = SerialWorker(port='COM6', baudrate=115200)
        self.worker.new_data_signal.connect(self.update_gui)
        self.worker.start()

    def setup_ui(self):
        central_widget = QtWidgets.QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QtWidgets.QVBoxLayout(central_widget)

        # --- Control Panel ---
        control_layout = QtWidgets.QHBoxLayout()
        
        # Load Command
        self.load_input = QtWidgets.QLineEdit("SUPERCAP")
        self.btn_load = QtWidgets.QPushButton("Set LOAD")
        self.btn_load.clicked.connect(lambda: self.worker.send_command(f"LOAD {self.load_input.text()}"))
        control_layout.addWidget(QtWidgets.QLabel("Load Type:"))
        control_layout.addWidget(self.load_input)
        control_layout.addWidget(self.btn_load)

        # Target Voltage Command
        self.target_input = QtWidgets.QDoubleSpinBox()
        self.target_input.setRange(0, 10.0)
        self.target_input.setSingleStep(0.1)
        self.target_input.setValue(2.30)
        self.btn_target = QtWidgets.QPushButton("Set TARGET")
        self.btn_target.clicked.connect(lambda: self.worker.send_command(f"TARGET {self.target_input.value():.2f}"))
        control_layout.addWidget(QtWidgets.QLabel("Target V:"))
        control_layout.addWidget(self.target_input)
        control_layout.addWidget(self.btn_target)

        # PWM Command
        self.pwm_input = QtWidgets.QSpinBox()
        self.pwm_input.setRange(0, 255)
        self.pwm_input.setValue(130) # Hardware off state
        self.btn_pwm = QtWidgets.QPushButton("Set PWM")
        self.btn_pwm.clicked.connect(lambda: self.worker.send_command(f"PWM {self.pwm_input.value()}"))
        control_layout.addWidget(QtWidgets.QLabel("PWM (0-255):"))
        control_layout.addWidget(self.pwm_input)
        control_layout.addWidget(self.btn_pwm)

        main_layout.addLayout(control_layout)

        # --- Live Readouts ---
        self.readout_label = QtWidgets.QLabel("Waiting for data...")
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
        self.worker.stop()
        event.accept()

if __name__ == '__main__':
    app = QtWidgets.QApplication(sys.argv)
    
    # Set dark theme for better visibility with Pyqtgraph default colors
    app.setStyle("Fusion")
    
    main_win = MainWindow()
    main_win.show()
    sys.exit(app.exec())