/**
 * PUDELstat Firmware - Hardware Closed-Loop Mode
 * Platform: Arduino Uno R3 (ATmega328P)
 * Frequency: 31.25 kHz (Timer 1 Phase Correct)
 * * [USER ACTION REQUIRED]:
 * 1. Tune ADC_CURR_GAIN_MA and PWM_PER_MA using a multimeter and dummy load.
 * 2. Verify CURRENT_ZERO_PWM is exactly the duty cycle that results in 0.00mA.
 */

#include <Arduino.h>

// ==========================================
// 1. HARDWARE PIN DEFINITIONS
// ==========================================
const int PIN_PWM_OUT = 9;      // PB1 / OC1A
const int PIN_VOLT_SENSE = A0;  // Battery Voltage
const int PIN_CURR_SENSE = A2;  // Current Sense (Jumper from U1B Pin 6)
const int PIN_DIVIDER_EN = 6;   // Digital Switch for Voltage Divider

// ==========================================
// 2. CALIBRATION & CONSTANTS (TUNE THESE)
// ==========================================
const float V_REF = 5.00; 

// The PWM value that perfectly equals 2.5V (Virtual Ground)
// Start at 127, but analog offsets might mean true zero is 126 or 128.
const int CURRENT_ZERO_PWM = 127; 
const int CURRENT_ZERO_ADC = 512; 

// Voltage Calibration
const float ADC_VOLT_GAIN = (V_REF / 1023.0); 

// Current Calibration (Feedback via A2)
// Hardware context: R8 is 250 Ohms. 1mA = 0.25V drop. 
// 0.25V / (5V/1024) = ~51.2 ADC steps per mA. 
// Gain = 1 / 51.2 = 0.0195
const float ADC_CURR_GAIN_MA = 0.0195; 

// PWM Command Calibration (Feed-forward)
// If 5V = 255 PWM steps, 0.25V (1mA) = 12.75 PWM steps.
const float PWM_PER_MA = 12.75; 

// Safety Limits
const float MAX_VOLTAGE_V = 2.60;
const float MAX_CURRENT_MA = 12.0;
const unsigned long WATCHDOG_TIMEOUT_MS = 5000;

// ==========================================
// 3. SYSTEM STATE & GLOBALS
// ==========================================
enum SystemState {
    IDLE,
    CHARGE,
    DISCHARGE,
    ERROR
};

SystemState currentState = IDLE;
float targetCurrent_mA = 0.0;
unsigned long lastCommandTime = 0;
unsigned long lastTelemetryTime = 0;

// ==========================================
// 4. FUNCTION DECLARATIONS
// ==========================================
void setupTimer1_31kHz();
void setPWMDuty(uint8_t duty);
void handleSerialCommands();
void sendTelemetry(float v, float c);
void safetyCheck(float voltage, float current);
void emergencyStop(const char* reason);

void setup() {
    Serial.begin(115200);
    
    pinMode(PIN_PWM_OUT, OUTPUT);
    pinMode(PIN_DIVIDER_EN, OUTPUT);
    
    digitalWrite(PIN_DIVIDER_EN, HIGH); 
    setupTimer1_31kHz();

    // IDLE STATE: Command 0mA (2.5V / PWM 127)
    setPWMDuty(CURRENT_ZERO_PWM);
    
    lastCommandTime = millis();
    Serial.println("SYSTEM_READY");
}

void loop() {
    unsigned long now = millis();

    // 1. WATCHDOG
    if (currentState != IDLE && currentState != ERROR) {
        if (now - lastCommandTime > WATCHDOG_TIMEOUT_MS) {
            emergencyStop("ERR_WATCHDOG_TIMEOUT");
        }
    }

    // 2. SENSOR READING (Averaging)
    long rawVolt = 0; 
    long rawCurr = 0;
    for(int i=0; i<10; i++) {
        rawVolt += analogRead(PIN_VOLT_SENSE);
        rawCurr += analogRead(PIN_CURR_SENSE);
    }
    rawVolt /= 10;
    rawCurr /= 10;

    float batteryVoltage = rawVolt * ADC_VOLT_GAIN;
    
    // Calculate signed actual current
    float measuredCurrent_mA = (rawCurr - CURRENT_ZERO_ADC) * ADC_CURR_GAIN_MA; 

    // 3. SAFETY CHECKS
    safetyCheck(batteryVoltage, abs(measuredCurrent_mA));

    // 4. COMMS & TELEMETRY
    handleSerialCommands();

    if (now - lastTelemetryTime >= 100) {
        sendTelemetry(batteryVoltage, measuredCurrent_mA);
        lastTelemetryTime = now;
    }
}

// ==========================================
// HELPER FUNCTIONS
// ==========================================

void setupTimer1_31kHz() {
    TCCR1A = 0;
    TCCR1B = 0;
    TCCR1A = _BV(COM1A1) | _BV(WGM10);
    TCCR1B = _BV(CS10);
}

void setPWMDuty(uint8_t duty) {
    OCR1A = duty;
}

void safetyCheck(float voltage, float current) {
    if (voltage > MAX_VOLTAGE_V) emergencyStop("ERR_OVERVOLTAGE");
    if (current > MAX_CURRENT_MA) emergencyStop("ERR_OVERCURRENT");
}

void emergencyStop(const char* reason) {
    currentState = ERROR;
    // SAFEST STATE: 127 equals ~2.5V, which equals 0 current across the 2.5V virtual ground.
    setPWMDuty(CURRENT_ZERO_PWM); 
    Serial.print("STOP: ");
    Serial.println(reason);
}

void handleSerialCommands() {
    if (Serial.available() > 0) {
        String cmd = Serial.readStringUntil('\n');
        cmd.trim();
        lastCommandTime = millis(); 

        if (cmd == "STOP") {
            currentState = IDLE;
            setPWMDuty(CURRENT_ZERO_PWM);
            Serial.println("OK: IDLE");
        } 
        else if (cmd.startsWith("SET_CURRENT ")) {
            float val = cmd.substring(12).toFloat();
            
            if (val >= -10.0 && val <= 10.0) { 
                targetCurrent_mA = val;
                
                // Calculate open-loop PWM target
                // [USER ACTION]: If direction is backwards, change '+' to '-'
                int targetPWM = CURRENT_ZERO_PWM + (val * PWM_PER_MA);
                
                // Clamp limits
                if (targetPWM > 255) targetPWM = 255;
                if (targetPWM < 0) targetPWM = 0;
                
                setPWMDuty((uint8_t)targetPWM);

                if (val > 0) currentState = CHARGE;
                else if (val < 0) currentState = DISCHARGE;
                else currentState = IDLE;
                
                Serial.println("OK: UPDATED");
            } else {
                Serial.println("ERR: INVALID_RANGE");
            }
        }
    }
}

void sendTelemetry(float v, float c) {
    Serial.print(millis());
    Serial.print(",");
    Serial.print(v, 3);
    Serial.print(",");
    Serial.print(c, 3);
    Serial.print(",");
    
    switch(currentState) {
        case IDLE: Serial.println("IDLE"); break;
        case CHARGE: Serial.println("CHARGE"); break;
        case DISCHARGE: Serial.println("DISCHARGE"); break;
        case ERROR: Serial.println("ERROR"); break;
    }
}