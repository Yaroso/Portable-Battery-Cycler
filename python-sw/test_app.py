import sys
import time
import csv
import queue
import os
from datetime import datetime
from collections import deque
import serial
from PyQt6 import QtWidgets, QtCore
from PyQt6.QtWidgets import QMessageBox
from PyQt6.QtGui import QIntValidator, QFont
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

    def __init__(self, port, dut_serial, mode_folder, baudrate=115200):
        super().__init__()
        self.port = port
        self.dut_serial = dut_serial
        self.mode_folder = mode_folder
        self.baudrate = baudrate
        self.is_running = True
        self.command_queue = queue.Queue()
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = f"{self.mode_folder}/DUT_{self.dut_serial}_{timestamp}.csv"

    def send_command(self, cmd):
        self.command_queue.put(cmd)

    def run(self):
        try:
            ser = serial.Serial(self.port, self.baudrate, timeout=0.1)
            time.sleep(2.0)
            ser.reset_input_buffer() 
            ser.reset_output_buffer()
        except Exception as e:
            print(f"Failed to connect to {self.port}: {e}")
            return

        with open(self.log_file, 'a', newline='') as f:
            csv_writer = csv.writer(f)
            if f.tell() == 0:
                csv_writer.writerow(["# METADATA"])
                csv_writer.writerow([f"# Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"])
                csv_writer.writerow([f"# DUT Serial: {self.dut_serial}"])
                csv_writer.writerow([f"# Mode: {self.mode_folder}"])
                csv_writer.writerow(["# --------------------------------------------------"])
                csv_writer.writerow(["Timestamp", "State", "LoadType", "TargetV", "CellV", "Current_mA", "Charge_mAh", "Discharge_mAh"])

            start_time = time.time()

            while self.is_running:
                while not self.command_queue.empty():
                    cmd = self.command_queue.get()
                    ser.write(f"{cmd}\n".encode('utf-8'))
                    print(f"Sent: {cmd}")
                    time.sleep(0.05) 

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

                            csv_writer.writerow([timestamp, state, load_type, target_v, cell_v, current_ma, charge_mah, discharge_mah])
                            f.flush()

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
                except Exception:
                    pass

            while not self.command_queue.empty():
                cmd = self.command_queue.get()
                ser.write(f"{cmd}\n".encode('utf-8'))
                print(f"Sent (Shutdown): {cmd}")
                time.sleep(0.05)

        ser.close()

    def stop(self):
        self.is_running = False
        self.wait()

# --- THREAD 2: GUI & Orchestrator ---
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Battery Cycler DAQ - Phase 2 with Analytics")
        self.resize(1400, 950)

        self.plot_points = 1000
        self.time_data = deque(maxlen=self.plot_points)
        self.volt_data = deque(maxlen=self.plot_points)
        self.curr_data = deque(maxlen=self.plot_points)
        
        self.vi_v_data = []
        self.vi_i_data = []

        self.cycle_nums = []
        self.discharge_caps = []
        self.ce_percents = []
        self.last_charge_cap = 0.0
        
        self.latest_v_cell = 0.0
        self.pre_pwm = 130 # Cache for pre-conditioning direction

        os.makedirs("test-data", exist_ok=True)
        os.makedirs("cycle-data", exist_ok=True)
        
        self.auto_mode_active = False
        self.sm_state = "IDLE"
        self.sm_cycles_total = 0
        self.sm_cycles_done = 0
        self.sm_rest_duration = 0
        self.sm_timer_start = 0

        self.setup_ui()

    def setup_ui(self):
        central_widget = QtWidgets.QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QtWidgets.QVBoxLayout(central_widget)

        config_layout = QtWidgets.QHBoxLayout()
        self.serial_input = QtWidgets.QLineEdit()
        self.serial_input.setPlaceholderText("7-digit Serial (e.g. 0000001)")
        self.serial_input.setMaxLength(7)
        self.serial_input.setValidator(QIntValidator(0, 9999999, self))
        
        self.load_dropdown = QtWidgets.QComboBox()
        self.load_dropdown.addItems(["SUPERCAP", "NIMH", "RESISTOR", "CUSTOM"])
        
        config_layout.addWidget(QtWidgets.QLabel("DUT Serial:"))
        config_layout.addWidget(self.serial_input)
        config_layout.addWidget(QtWidgets.QLabel("Global Load Type:"))
        config_layout.addWidget(self.load_dropdown)
        main_layout.addLayout(config_layout)

        self.tabs = QtWidgets.QTabWidget()
        self.tab_manual = QtWidgets.QWidget()
        self.tab_auto = QtWidgets.QWidget()
        self.tabs.addTab(self.tab_manual, "Mode 1: Manual Control")
        self.tabs.addTab(self.tab_auto, "Mode 2: Auto-Cycle Profiler")
        main_layout.addWidget(self.tabs)

        self.setup_manual_tab()
        self.setup_auto_tab()

        self.readout_label = QtWidgets.QLabel("System Idle. Awaiting test start...")
        self.readout_label.setFont(QFont("Monospace", 12, QFont.Weight.Bold))
        self.readout_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(self.readout_label)

        pg.setConfigOptions(antialias=True)
        plots_layout = QtWidgets.QGridLayout()
        
        self.plot_v = pg.PlotWidget(title="Cell Voltage vs Time")
        self.plot_v.showGrid(x=True, y=True)
        self.line_v = self.plot_v.plot(pen=pg.mkPen('y', width=2))
        plots_layout.addWidget(self.plot_v, 0, 0)

        self.plot_i = pg.PlotWidget(title="Current vs Time")
        self.plot_i.showGrid(x=True, y=True)
        self.line_i = self.plot_i.plot(pen=pg.mkPen('c', width=2))
        plots_layout.addWidget(self.plot_i, 1, 0)

        self.plot_vi = pg.PlotWidget(title="Real-Time V-I Curve")
        self.plot_vi.setLabel('left', 'Current', units='mA')
        self.plot_vi.setLabel('bottom', 'Voltage', units='V')
        self.plot_vi.showGrid(x=True, y=True)
        self.line_vi = self.plot_vi.plot(pen=None, symbol='o', symbolSize=4, symbolPen=None, symbolBrush='m')
        plots_layout.addWidget(self.plot_vi, 0, 1, 2, 1) 
        
        plots_layout.setColumnStretch(0, 2)
        plots_layout.setColumnStretch(1, 1)
        main_layout.addLayout(plots_layout)

        self.load_dropdown.currentTextChanged.connect(self.update_spinbox_limits)
        self.update_spinbox_limits(self.load_dropdown.currentText())

    def setup_manual_tab(self):
        layout = QtWidgets.QVBoxLayout(self.tab_manual)
        
        btn_layout = QtWidgets.QHBoxLayout()
        self.btn_start_manual = QtWidgets.QPushButton("START MANUAL LOGGING")
        self.btn_start_manual.setStyleSheet("background-color: #2b5c8f; color: white; font-weight: bold; padding: 10px;")
        self.btn_start_manual.clicked.connect(self.start_manual_test)
        
        self.btn_stop_manual = QtWidgets.QPushButton("STOP LOGGING")
        self.btn_stop_manual.setStyleSheet("background-color: darkred; color: white; font-weight: bold; padding: 10px;")
        self.btn_stop_manual.setEnabled(False)
        self.btn_stop_manual.clicked.connect(self.end_current_session)

        btn_layout.addWidget(self.btn_start_manual)
        btn_layout.addWidget(self.btn_stop_manual)
        layout.addLayout(btn_layout)

        ctrl_layout = QtWidgets.QHBoxLayout()
        self.target_input = QtWidgets.QDoubleSpinBox()
        self.target_input.setSingleStep(0.1)
        self.btn_target = QtWidgets.QPushButton("Set TARGET")
        self.btn_target.clicked.connect(self.send_target_command)
        ctrl_layout.addWidget(QtWidgets.QLabel("Target V:"))
        ctrl_layout.addWidget(self.target_input)
        ctrl_layout.addWidget(self.btn_target)

        self.pwm_input = QtWidgets.QSpinBox()
        self.pwm_input.setRange(0, 255)
        self.pwm_input.setValue(130)
        self.btn_pwm = QtWidgets.QPushButton("Set PWM")
        self.btn_pwm.clicked.connect(self.send_pwm_command)
        ctrl_layout.addWidget(QtWidgets.QLabel("PWM (0-255):"))
        ctrl_layout.addWidget(self.pwm_input)
        ctrl_layout.addWidget(self.btn_pwm)
        
        layout.addLayout(ctrl_layout)
        layout.addStretch()

    def setup_auto_tab(self):
        master_layout = QtWidgets.QHBoxLayout(self.tab_auto)
        
        controls_widget = QtWidgets.QWidget()
        controls_layout = QtWidgets.QVBoxLayout(controls_widget)
        
        param_layout = QtWidgets.QGridLayout()
        self.chg_v = QtWidgets.QDoubleSpinBox()
        self.chg_v.setSingleStep(0.1)
        self.chg_pwm = QtWidgets.QSpinBox()
        self.chg_pwm.setRange(0, 129)
        self.chg_pwm.setValue(80)
        
        self.dchg_v = QtWidgets.QDoubleSpinBox()
        self.dchg_v.setSingleStep(0.1)
        self.dchg_pwm = QtWidgets.QSpinBox()
        self.dchg_pwm.setRange(131, 255)
        self.dchg_pwm.setValue(180)

        self.rest_t = QtWidgets.QSpinBox()
        self.rest_t.setRange(0, 3600)
        self.rest_t.setValue(10)
        self.cycles_n = QtWidgets.QSpinBox()
        self.cycles_n.setRange(1, 10000)
        self.cycles_n.setValue(5)

        param_layout.addWidget(QtWidgets.QLabel("Charge Target (V):"), 0, 0)
        param_layout.addWidget(self.chg_v, 0, 1)
        param_layout.addWidget(QtWidgets.QLabel("Charge PWM:"), 0, 2)
        param_layout.addWidget(self.chg_pwm, 0, 3)

        param_layout.addWidget(QtWidgets.QLabel("Dischg Target (V):"), 1, 0)
        param_layout.addWidget(self.dchg_v, 1, 1)
        param_layout.addWidget(QtWidgets.QLabel("Dischg PWM:"), 1, 2)
        param_layout.addWidget(self.dchg_pwm, 1, 3)

        param_layout.addWidget(QtWidgets.QLabel("Rest Time (s):"), 2, 0)
        param_layout.addWidget(self.rest_t, 2, 1)
        param_layout.addWidget(QtWidgets.QLabel("Total Cycles:"), 2, 2)
        param_layout.addWidget(self.cycles_n, 2, 3)
        controls_layout.addLayout(param_layout)

        btn_layout = QtWidgets.QHBoxLayout()
        self.btn_start_auto = QtWidgets.QPushButton("START AUTO-CYCLE")
        self.btn_start_auto.setStyleSheet("background-color: darkgreen; color: white; font-weight: bold; padding: 10px;")
        self.btn_start_auto.clicked.connect(self.start_auto_test)
        
        self.btn_estop = QtWidgets.QPushButton("EMERGENCY STOP")
        self.btn_estop.setStyleSheet("background-color: darkred; color: white; font-weight: bold; padding: 10px;")
        self.btn_estop.clicked.connect(self.end_current_session) 
        
        btn_layout.addWidget(self.btn_start_auto)
        btn_layout.addWidget(self.btn_estop)
        controls_layout.addLayout(btn_layout)

        self.orchestrator_log = QtWidgets.QTextEdit()
        self.orchestrator_log.setReadOnly(True)
        self.orchestrator_log.setMaximumHeight(100)
        controls_layout.addWidget(QtWidgets.QLabel("Orchestrator Status:"))
        controls_layout.addWidget(self.orchestrator_log)
        
        master_layout.addWidget(controls_widget)

        analytics_widget = QtWidgets.QWidget()
        analytics_layout = QtWidgets.QVBoxLayout(analytics_widget)
        
        self.plot_cap = pg.PlotWidget(title="Discharge Capacity Degradation")
        self.plot_cap.setLabel('left', 'Capacity', units='mAh')
        self.plot_cap.setLabel('bottom', 'Cycle Number')
        self.plot_cap.showGrid(x=True, y=True)
        self.line_cap = self.plot_cap.plot(pen=pg.mkPen('g', width=2), symbol='s')
        analytics_layout.addWidget(self.plot_cap)

        self.plot_ce = pg.PlotWidget(title="Coulombic Efficiency")
        self.plot_ce.setLabel('left', 'Efficiency', units='%')
        self.plot_ce.setLabel('bottom', 'Cycle Number')
        self.plot_ce.showGrid(x=True, y=True)
        self.line_ce = self.plot_ce.plot(pen=pg.mkPen('b', width=2), symbol='t')
        analytics_layout.addWidget(self.plot_ce)
        
        master_layout.addWidget(analytics_widget)
        master_layout.setStretch(0, 1) 
        master_layout.setStretch(1, 1) 

    def update_spinbox_limits(self, load_type):
        limits = LOAD_LIMITS[load_type]
        safe_floor = limits["floor"] + 0.02
        safe_ceil = limits["ceil"] - 0.02
        
        self.target_input.setRange(safe_floor, safe_ceil)
        self.chg_v.setRange(safe_floor, safe_ceil)
        self.dchg_v.setRange(safe_floor, safe_ceil)

    def log_orchestrator(self, msg):
        self.orchestrator_log.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    def validate_serial(self):
        dut_serial = self.serial_input.text()
        if len(dut_serial) != 7:
            QMessageBox.warning(self, "Validation Error", "Please enter exactly a 7-digit Serial Number.")
            return None
        return dut_serial

    def clear_all_live_plots(self):
        self.time_data.clear()
        self.volt_data.clear()
        self.curr_data.clear()
        self.line_v.setData([], [])
        self.line_i.setData([], [])
        self.vi_v_data.clear()
        self.vi_i_data.clear()
        self.line_vi.setData([], [])

    def start_manual_test(self):
        dut_serial = self.validate_serial()
        if not dut_serial: return

        self.clear_all_live_plots()

        self.worker = SerialWorker(port='COM6', dut_serial=dut_serial, mode_folder='test-data')
        self.worker.new_data_signal.connect(self.update_gui)
        self.worker.start()
        
        self.worker.send_command(f"LOAD {self.load_dropdown.currentText()}")
        
        self.btn_start_manual.setEnabled(False)
        self.btn_stop_manual.setEnabled(True)
        self.serial_input.setEnabled(False)
        self.load_dropdown.setEnabled(False)
        self.tabs.setTabEnabled(1, False)

    def start_auto_test(self):
        # Mathematical Interlock: PEBKAC Safety Check
        if self.chg_v.value() <= self.dchg_v.value():
            QMessageBox.critical(self, "Mathematical Logic Error", "Charge Target must be strictly greater than Discharge Target to cycle safely.")
            return

        dut_serial = self.validate_serial()
        if not dut_serial: return

        self.clear_all_live_plots()

        load_type = self.load_dropdown.currentText()
        self.worker = SerialWorker(port='COM6', dut_serial=dut_serial, mode_folder='cycle-data')
        self.worker.new_data_signal.connect(self.update_gui)
        self.worker.start()

        self.cycle_nums.clear()
        self.discharge_caps.clear()
        self.ce_percents.clear()
        self.line_cap.setData([], [])
        self.line_ce.setData([], [])

        self.worker.send_command(f"LOAD {load_type}")
        self.auto_mode_active = True
        
        self.sm_state = "WAIT_FOR_LOAD_ACK"
        self.sm_cycles_total = self.cycles_n.value()
        self.sm_cycles_done = 0
        self.sm_rest_duration = self.rest_t.value()
        
        self.btn_start_auto.setEnabled(False)
        self.serial_input.setEnabled(False)
        self.load_dropdown.setEnabled(False)
        self.tabs.setTabEnabled(0, False)
        self.log_orchestrator(f"Starting Profile. Total Cycles: {self.sm_cycles_total}")

    def end_current_session(self):
        if hasattr(self, 'worker') and self.worker.is_running:
            self.worker.send_command("PWM 130")
            self.worker.send_command("TARGET 0.0")
            self.worker.stop()
            
        self.auto_mode_active = False
        self.sm_state = "IDLE"
        self.log_orchestrator("Session terminated. Hardware returned to safe state.")
        self.readout_label.setText("System Idle. Hardware disconnected.")

        self.btn_start_manual.setEnabled(True)
        self.btn_stop_manual.setEnabled(False)
        self.btn_start_auto.setEnabled(True)
        self.serial_input.setEnabled(True)
        self.load_dropdown.setEnabled(True)
        self.tabs.setTabEnabled(0, True)
        self.tabs.setTabEnabled(1, True)

    def send_target_command(self):
        if not hasattr(self, 'worker') or not self.worker.is_running:
            QMessageBox.warning(self, "Error", "Please start manual logging first.")
            return
            
        target_v = self.target_input.value()
        self.vi_v_data.clear()
        self.vi_i_data.clear()
        self.line_vi.setData([], [])
        
        self.pwm_input.setValue(130)
        self.worker.send_command("PWM 130")
        self.worker.send_command(f"TARGET {target_v:.2f}")

    def send_pwm_command(self):
        if not hasattr(self, 'worker') or not self.worker.is_running:
            QMessageBox.warning(self, "Error", "Please start manual logging first.")
            return
            
        target_v = self.target_input.value()
        pwm_val = self.pwm_input.value()
        
        if target_v < self.latest_v_cell and pwm_val < 130:
            QMessageBox.critical(self, "PWM Interlock Error", f"Target ({target_v:.2f}V) is below actual cell voltage ({self.latest_v_cell:.2f}V).\n\nYou must DISCHARGE. PWM must be 130 to 255.")
            return
        elif target_v > self.latest_v_cell and pwm_val > 130:
            QMessageBox.critical(self, "PWM Interlock Error", f"Target ({target_v:.2f}V) is above actual cell voltage ({self.latest_v_cell:.2f}V).\n\nYou must CHARGE. PWM must be 0 to 130.")
            return
        
        self.vi_v_data.clear()
        self.vi_i_data.clear()
        self.line_vi.setData([], [])
        self.worker.send_command(f"PWM {pwm_val}")

    def update_gui(self, data):
        self.latest_v_cell = data['cell_v']
        
        status_text = (f"STATE: {data['state']} | LOAD: {data['load_type']} | "
                       f"V_cell: {data['cell_v']:.3f}V (Tgt: {data['target_v']:.2f}V) | "
                       f"I: {data['current_ma']:.1f}mA")
        self.readout_label.setText(status_text)

        self.time_data.append(data['time'])
        self.volt_data.append(data['cell_v'])
        self.curr_data.append(data['current_ma'])

        self.line_v.setData(self.time_data, self.volt_data)
        self.line_i.setData(self.time_data, self.curr_data)

        self.vi_v_data.append(data['cell_v'])
        self.vi_i_data.append(data['current_ma'])
        self.line_vi.setData(self.vi_v_data, self.vi_i_data)

        if self.auto_mode_active:
            self.tick_state_machine(data)

    def tick_state_machine(self, data):
        hw_state = data['state']

        # --- SYSTEM INITIALIZATION ---
        if self.sm_state == "WAIT_FOR_LOAD_ACK":
            if data['load_type'] == self.load_dropdown.currentText() and hw_state == "WAIT_TGT":
                self.sm_state = "START_PRE_CONDITIONING"

        # --- PRE-CONDITIONING SEQUENCE ---
        elif self.sm_state == "START_PRE_CONDITIONING":
            self.pre_target = self.dchg_v.value()
            self.worker.send_command("PWM 130") # Safely halt hardware
            
            # Determine if we need to pre-charge or pre-discharge to hit baseline
            if data['cell_v'] < (self.pre_target - 0.05):
                self.pre_pwm = self.chg_pwm.value()
                action_text = "Charging"
            elif data['cell_v'] > (self.pre_target + 0.05):
                self.pre_pwm = self.dchg_pwm.value()
                action_text = "Discharging"
            else:
                self.log_orchestrator(f"Cell already at baseline ({data['cell_v']:.3f}V). Skipping pre-conditioning.")
                self.sm_state = "START_CHARGE"
                return

            self.worker.send_command(f"TARGET {self.pre_target:.2f}")
            self.worker.send_command(f"PWM {self.pre_pwm}")
            
            self.log_orchestrator(f"Pre-conditioning: {action_text} to baseline ({self.pre_target:.2f}V)...")
            self.sm_timer_start = time.time()
            self.sm_state = "WAIT_PRE_COND_ACK"
            
        elif self.sm_state == "WAIT_PRE_COND_ACK":
            if abs(data['target_v'] - self.pre_target) < 0.05:
                self.sm_state = "WAIT_PRE_COND_DONE"
            elif time.time() - self.sm_timer_start > 3.0: 
                self.log_orchestrator("Command dropped. Retrying pre-conditioning commands...")
                self.worker.send_command("PWM 130")
                self.worker.send_command(f"TARGET {self.pre_target:.2f}")
                self.worker.send_command(f"PWM {self.pre_pwm}")
                self.sm_timer_start = time.time()
                
        elif self.sm_state == "WAIT_PRE_COND_DONE":
            if hw_state == "DONE":
                self.worker.send_command("PWM 130") 
                self.log_orchestrator(f"Baseline reached. Resting for {self.sm_rest_duration}s before Cycle 1...")
                self.sm_timer_start = time.time()
                self.sm_state = "REST_POST_PRE_COND"
                
        elif self.sm_state == "REST_POST_PRE_COND":
            if time.time() - self.sm_timer_start >= self.sm_rest_duration:
                self.sm_state = "START_CHARGE"

        # --- STANDARD CYCLING SEQUENCE ---
        elif self.sm_state == "START_CHARGE":
            self.vi_v_data.clear()
            self.vi_i_data.clear()
            self.line_vi.setData([], [])
            self.chg_target_cache = self.chg_v.value()
            
            self.worker.send_command("PWM 130")
            self.worker.send_command(f"TARGET {self.chg_target_cache:.2f}")
            self.worker.send_command(f"PWM {self.chg_pwm.value()}")
            
            self.log_orchestrator(f"Cycle {self.sm_cycles_done + 1}/{self.sm_cycles_total}: Charging...")
            self.sm_timer_start = time.time()
            self.sm_state = "WAIT_CHARGE_ACK"

        elif self.sm_state == "WAIT_CHARGE_ACK":
            if abs(data['target_v'] - self.chg_target_cache) < 0.05:
                self.sm_state = "WAIT_CHARGE_DONE"
            elif time.time() - self.sm_timer_start > 3.0: 
                self.worker.send_command("PWM 130")
                self.worker.send_command(f"TARGET {self.chg_target_cache:.2f}")
                self.worker.send_command(f"PWM {self.chg_pwm.value()}")
                self.sm_timer_start = time.time()

        elif self.sm_state == "WAIT_CHARGE_DONE":
            if hw_state == "DONE":
                self.last_charge_cap = data['charge_mah'] 
                self.worker.send_command("PWM 130") 
                self.sm_timer_start = time.time()
                self.log_orchestrator(f"Charge complete. Resting for {self.sm_rest_duration}s...")
                self.sm_state = "REST_POST_CHARGE"

        elif self.sm_state == "REST_POST_CHARGE":
            if (time.time() - self.sm_timer_start) >= self.sm_rest_duration:
                self.sm_state = "START_DISCHARGE"

        elif self.sm_state == "START_DISCHARGE":
            self.vi_v_data.clear()
            self.vi_i_data.clear()
            self.line_vi.setData([], [])
            self.dchg_target_cache = self.dchg_v.value()
            
            self.worker.send_command("PWM 130")
            self.worker.send_command(f"TARGET {self.dchg_target_cache:.2f}")
            self.worker.send_command(f"PWM {self.dchg_pwm.value()}")
            
            self.log_orchestrator(f"Cycle {self.sm_cycles_done + 1}/{self.sm_cycles_total}: Discharging...")
            self.sm_timer_start = time.time()
            self.sm_state = "WAIT_DISCHARGE_ACK"

        elif self.sm_state == "WAIT_DISCHARGE_ACK":
            if abs(data['target_v'] - self.dchg_target_cache) < 0.05:
                self.sm_state = "WAIT_DISCHARGE_DONE"
            elif time.time() - self.sm_timer_start > 3.0: 
                self.worker.send_command("PWM 130")
                self.worker.send_command(f"TARGET {self.dchg_target_cache:.2f}")
                self.worker.send_command(f"PWM {self.dchg_pwm.value()}")
                self.sm_timer_start = time.time()

        elif self.sm_state == "WAIT_DISCHARGE_DONE":
            if hw_state == "DONE":
                final_dchg_cap = data['discharge_mah'] 
                self.worker.send_command("PWM 130")
                
                self.sm_cycles_done += 1
                ce = (final_dchg_cap / self.last_charge_cap * 100.0) if self.last_charge_cap > 0 else 0.0
                
                self.cycle_nums.append(self.sm_cycles_done)
                self.discharge_caps.append(final_dchg_cap)
                self.ce_percents.append(ce)
                
                self.line_cap.setData(self.cycle_nums, self.discharge_caps)
                self.line_ce.setData(self.cycle_nums, self.ce_percents)
                
                self.sm_timer_start = time.time()
                self.log_orchestrator(f"Discharge complete. CE: {ce:.1f}%. Resting...")
                self.sm_state = "REST_POST_DISCHARGE"

        elif self.sm_state == "REST_POST_DISCHARGE":
            if (time.time() - self.sm_timer_start) >= self.sm_rest_duration:
                if self.sm_cycles_done < self.sm_cycles_total:
                    self.sm_state = "START_CHARGE"
                else:
                    self.log_orchestrator("All cycles completed successfully.")
                    self.end_current_session()

    def closeEvent(self, event):
        self.end_current_session()
        event.accept()

if __name__ == '__main__':
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    main_win = MainWindow()
    main_win.show()
    sys.exit(app.exec())