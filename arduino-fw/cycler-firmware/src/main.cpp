/**
 * PUDELstat Firmware - Full System Integration
 * Features: Auto-Zero, Moving Avg, Hardware Latch, Graceful Software OVP
 */

#include <Arduino.h>

const int PIN_PWM_OUT = 9;      
const int PIN_VOLT_SENSE = A0;  
const int PIN_CURR_SENSE = A2;  
const int PIN_DIVIDER_EN = 6;   
const int PIN_RELAY_DRIVE = 3;     
const int PIN_FAULT_INTERRUPT = 2; 

float CURRENT_ZERO_ADC = 512.0; 
const float ADC_CURR_GAIN_MA = 0.0718; 
const float ADC_VOLT_GAIN = (5.00 / 1023.0);

enum SystemState { AWAITING_TARGET, IDLE, RUNNING, ERROR, CHARGE_COMPLETE };
SystemState currentState = AWAITING_TARGET;

float targetVoltage = 0.0;
uint8_t currentPWMCommand = 127;

volatile bool hardwareFaultDetected = false;
unsigned long lastTelemetryTime = 0;

void hardwareFaultISR() {
    OCR1A = 127; 
    hardwareFaultDetected = true;
}

void setupTimer1_31kHz();
void setPWMDuty(uint8_t duty);
void handleSerialCommands();

void setup() {
    Serial.begin(115200);
    
    pinMode(PIN_RELAY_DRIVE, OUTPUT);
    digitalWrite(PIN_RELAY_DRIVE, HIGH);
    
    pinMode(PIN_FAULT_INTERRUPT, INPUT); 
    
    pinMode(PIN_PWM_OUT, OUTPUT);
    pinMode(PIN_DIVIDER_EN, OUTPUT);
    digitalWrite(PIN_DIVIDER_EN, HIGH); 
    
    setupTimer1_31kHz();
    setPWMDuty(127); 
    
    Serial.println("SYSTEM BOOT: Allowing analog hardware to settle...");
    delay(1000); 
    
    Serial.println("SYSTEM BOOT: Calibrating Zero Point...");
    long sumA2 = 0;
    for(int i = 0; i < 100; i++) {
        sumA2 += analogRead(PIN_CURR_SENSE);
        delay(5);
    }
    CURRENT_ZERO_ADC = (float)sumA2 / 100.0;
    
    EIFR = bit(INTF0); 
    attachInterrupt(digitalPinToInterrupt(PIN_FAULT_INTERRUPT), hardwareFaultISR, FALLING);
    
    Serial.print("CALIBRATION COMPLETE. True Zero ADC: ");
    Serial.println(CURRENT_ZERO_ADC, 2);
    Serial.println("\n>>> SYSTEM LOCKED. Set target voltage first.");
    Serial.println(">>> Send 'TARGET <volts>' (e.g., 'TARGET 2.3')");
}

void loop() {
    // 1. HARDWARE LATCH CHECK
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
    
    float absolute_ce = (sumVolt / 20.0) * ADC_VOLT_GAIN;
    float absolute_we = (sumCurr / 20.0) * ADC_VOLT_GAIN;
    float c = ((sumCurr / 20.0) - CURRENT_ZERO_ADC) * ADC_CURR_GAIN_MA;
    float true_cap_voltage = absolute_ce - absolute_we;
    
    // 3. GRACEFUL SOFTWARE CUTOFFS (Direction-Aware)
    if (currentState == RUNNING) {
        // CHARGE CUTOFF: Current flowing IN (PWM < 127)
        if (currentPWMCommand < 127 && true_cap_voltage >= targetVoltage) {
            setPWMDuty(127);
            currentPWMCommand = 127;
            currentState = CHARGE_COMPLETE;
            Serial.println("\n>>> GRACEFUL STOP: Target Charge Voltage Reached! <<<");
            Serial.println(">>> Current Halted. System CHARGE_COMPLETE.\n");
        }
        // DISCHARGE CUTOFF: Current flowing OUT (PWM > 127)
        // Stops discharge at 0.05V to prevent negative polarity across the cap
        else if (currentPWMCommand > 127 && true_cap_voltage <= 0.05) {
            setPWMDuty(127);
            currentPWMCommand = 127;
            currentState = IDLE;
            Serial.println("\n>>> GRACEFUL STOP: Safe Discharge Floor (0.05V) Reached! <<<");
            Serial.println(">>> Current Halted. System IDLE.\n");
        }
    }
    
    // 4. PRINT TELEMETRY
    if (millis() - lastTelemetryTime >= 250) {
        if (currentState == ERROR) {
            Serial.print(absolute_ce, 3); Serial.print("V (CE), ");
            Serial.print(absolute_we, 3); Serial.print("V (WE), LOCKED_ERROR");
        } else {
            Serial.print("State: "); 
            if (currentState == AWAITING_TARGET) Serial.print("WAIT_TGT | ");
            else if (currentState == IDLE) Serial.print("IDLE | ");
            else if (currentState == CHARGE_COMPLETE) Serial.print("DONE | ");
            else if (currentState == RUNNING && currentPWMCommand < 127) Serial.print("CHARGING | ");
            else if (currentState == RUNNING && currentPWMCommand > 127) Serial.print("DISCHARGING | ");
            else Serial.print("RUNNING | ");
            
            Serial.print("Target: "); Serial.print(targetVoltage, 2); Serial.print("V | ");
            Serial.print("Cell: "); Serial.print(true_cap_voltage, 3); Serial.print("V | ");
            Serial.print("Current: "); Serial.print(c, 3); Serial.print("mA");
        }
        Serial.println();
        lastTelemetryTime = millis();
    }
    
    handleSerialCommands();
}

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
        
        if (currentState == ERROR) {
            Serial.println(">>> REJECTED: SYSTEM IS IN ERROR LOCKOUT.");
            return;
        }

        if (cmd.startsWith("TARGET ")) {
            targetVoltage = cmd.substring(7).toFloat();
            if (targetVoltage > 0.1 && targetVoltage <= 4.8) {
                currentState = IDLE;
                Serial.print("\n>>> UPPER TARGET SET: "); 
                Serial.print(targetVoltage, 2); 
                Serial.println("V");
                Serial.println(">>> Send 'PWM <0-126>' to Charge. Send 'PWM <128-255>' to Discharge.\n");
            } else {
                Serial.println(">>> ERROR: Invalid target. Must be > 0.1V and <= 4.8V");
            }
        } 
        else if (cmd.startsWith("PWM ")) {
            if (currentState == AWAITING_TARGET) {
                Serial.println(">>> REJECTED: You must set a TARGET voltage first.");
                return;
            }
            int val = cmd.substring(4).toInt();
            if (val >= 0 && val <= 255) {
                currentPWMCommand = val;
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