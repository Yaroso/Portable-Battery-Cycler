/**
 * PUDELstat Firmware - Full System Integration
 * Platform: Arduino Uno R3 (ATmega328P)
 * Features: Auto-Zero, Moving Average, Hardware Interrupt Latch, Bypass Ready
 */

#include <Arduino.h>

// ==========================================
// HARDWARE PIN DEFINITIONS
// ==========================================
const int PIN_PWM_OUT = 9;      
const int PIN_VOLT_SENSE = A0;  
const int PIN_CURR_SENSE = A2;  
const int PIN_DIVIDER_EN = 6;   
const int PIN_RELAY_DRIVE = 3;     // J8 Pin 1 (Bypass drive)
const int PIN_FAULT_INTERRUPT = 2; // Connected to FAULT_N

// ==========================================
// CALIBRATION GLOBALS
// ==========================================
float CURRENT_ZERO_ADC = 512.0; 
const float ADC_CURR_GAIN_MA = 0.0718; 
const float ADC_VOLT_GAIN = (5.00 / 1023.0);

enum SystemState { IDLE, RUNNING, ERROR };
SystemState currentState = IDLE;

// Volatile flag for the interrupt service routine
volatile bool hardwareFaultDetected = false;
unsigned long lastTelemetryTime = 0;

// ==========================================
// HARDWARE INTERRUPT SERVICE ROUTINE (ISR)
// ==========================================
// Fires in microseconds the moment FAULT_N goes LOW (Falling edge)
void hardwareFaultISR() {
    OCR1A = 127; // Instantly command 0mA (2.5V Virtual Ground)
    hardwareFaultDetected = true;
}

void setupTimer1_31kHz();
void setPWMDuty(uint8_t duty);
void handleSerialCommands();
void calculateTrueCellVoltage(float v_ce, float v_we);

void setup() {
    Serial.begin(115200);
    
    // 1. INITIALIZE PINS
    pinMode(PIN_RELAY_DRIVE, OUTPUT);
    digitalWrite(PIN_RELAY_DRIVE, HIGH);
    
    pinMode(PIN_FAULT_INTERRUPT, INPUT); // Do NOT attach interrupt yet
    
    pinMode(PIN_PWM_OUT, OUTPUT);
    pinMode(PIN_DIVIDER_EN, OUTPUT);
    digitalWrite(PIN_DIVIDER_EN, HIGH); 
    
    // 2. STARTUP HARDWARE & WAIT FOR SETTLING
    setupTimer1_31kHz();
    setPWMDuty(127); // Command 0mA baseline
    
    Serial.println("SYSTEM BOOT: Allowing analog hardware to settle...");
    delay(1000); // Relay chatters and hardware stabilizes here
    
    // 3. CALIBRATE
    Serial.println("SYSTEM BOOT: Calibrating Zero Point...");
    long sumA2 = 0;
    for(int i = 0; i < 100; i++) {
        sumA2 += analogRead(PIN_CURR_SENSE);
        delay(5);
    }
    CURRENT_ZERO_ADC = (float)sumA2 / 100.0;
    
    // 4. ARM THE SAFETY INTERRUPT (LAST STEP)
    // Clear any phantom falling edges that occurred during the settling delay
    EIFR = bit(INTF0); 
    // Now safely attach the interrupt to Pin 2
    attachInterrupt(digitalPinToInterrupt(PIN_FAULT_INTERRUPT), hardwareFaultISR, FALLING);
    
    Serial.print("CALIBRATION COMPLETE. True Zero ADC: ");
    Serial.println(CURRENT_ZERO_ADC, 2);
    Serial.println("SYSTEM READY. Send 'PWM <0-255>' to test.");
}

void loop() {
    // 1. CHECK FOR HARDWARE FAULTS (THE LATCH)
    if (hardwareFaultDetected && currentState != ERROR) {
        currentState = ERROR;
        Serial.println("\n==================================================");
        Serial.println(">>> CRITICAL ALARM: HARDWARE FAULT TRIPPED <<<");
        Serial.println(">>> RELAY OPENED. PWM LATCHED TO 127 (0mA). <<<");
        Serial.println(">>> SYSTEM LOCKED. PRESS RESET BUTTON TO CLEAR. <<<");
        Serial.println("==================================================\n");
    }

    // 2. TELEMETRY & FILTERING
    long sumVolt = 0;
    long sumCurr = 0;
    
    for(int i = 0; i < 20; i++) {
        sumVolt += analogRead(PIN_VOLT_SENSE);
        sumCurr += analogRead(PIN_CURR_SENSE);
        delay(2); 
    }
    
    float avgVoltADC = (float)sumVolt / 20.0;
    float avgCurrADC = (float)sumCurr / 20.0;
    
    // Absolute voltages relative to Arduino GND
    float absolute_ce = avgVoltADC * ADC_VOLT_GAIN;
    float absolute_we = avgCurrADC * ADC_VOLT_GAIN;
    float c = (avgCurrADC - CURRENT_ZERO_ADC) * ADC_CURR_GAIN_MA;
    
    // True cell voltage calculation
    float true_cap_voltage = absolute_ce - absolute_we;
    
    // Print telemetry at 4 Hz
    if (millis() - lastTelemetryTime >= 250) {
        if (currentState == ERROR) {
            Serial.print(absolute_ce, 3); Serial.print("V (CE), ");
            Serial.print(absolute_we, 3); Serial.print("V (WE), ");
            Serial.println("LOCKED_ERROR");
        } else {
            Serial.print("True Cell: "); Serial.print(true_cap_voltage, 3); Serial.print("V | ");
            Serial.print("Current: "); Serial.print(c, 3); Serial.println("mA");
        }
        lastTelemetryTime = millis();
    }
    
    // 3. COMMAND HANDLING
    handleSerialCommands();
}

// ==========================================
// HELPER FUNCTIONS
// ==========================================
void setupTimer1_31kHz() {
    TCCR1A = 0; TCCR1B = 0;
    TCCR1A = _BV(COM1A1) | _BV(WGM10);
    TCCR1B = _BV(CS10);
}

void setPWMDuty(uint8_t duty) {
    OCR1A = duty;
}

void handleSerialCommands() {
    if (Serial.available() > 0) {
        String cmd = Serial.readStringUntil('\n');
        cmd.trim();
        
        if (cmd.startsWith("PWM ")) {
            if (currentState == ERROR) {
                Serial.println(">>> REJECTED: SYSTEM IS IN ERROR LOCKOUT. RESET MCU TO CLEAR.");
                return;
            }
            
            int val = cmd.substring(4).toInt();
            if (val >= 0 && val <= 255) {
                setPWMDuty(val);
                currentState = RUNNING;
                Serial.print(">>> COMMAND ACCEPTED: PWM = ");
                Serial.println(val);
            } else {
                Serial.println(">>> ERROR: PWM must be 0-255");
            }
        }
    }
}