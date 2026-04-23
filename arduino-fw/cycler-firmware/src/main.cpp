/**
 * PUDELstat Firmware - Coulomb Counting Edition
 * Features: A3 Diff Current, HW Latch, Dual-Direction Capacity Integration
 * Calibrated Zero: PWM 130 | Calibrated Gain: 0.0730
 */

#include <Arduino.h>

const int PIN_PWM_OUT = 9;      
const int PIN_VOLT_SENSE = A0;  
const int PIN_CURR_SENSE = A2;  
const int PIN_VIRT_GND_SENSE = A3; 
const int PIN_DIVIDER_EN = 6;   
const int PIN_RELAY_DRIVE = 3;     
const int PIN_FAULT_INTERRUPT = 2; 

float ZERO_OFFSET_ADC = 0.0; 
const float ADC_CURR_GAIN_MA = 0.0730; 
const float ADC_VOLT_GAIN = (5.00 / 1023.0);

enum SystemState { AWAITING_LOAD, AWAITING_TARGET, IDLE, RUNNING, ERROR, TARGET_REACHED };
SystemState currentState = AWAITING_LOAD;

String currentLoadName = "NONE";
float maxAllowableCeiling = 0.0;
float safeFloorVoltage = 0.0;
float targetVoltage = 0.0;
uint8_t currentPWMCommand = 130; 

// --- COULOMB COUNTING VARIABLES ---
float totalCharge_mAh = 0.0;
float totalDischarge_mAh = 0.0;
unsigned long lastIntegrationTime = 0;

volatile bool hardwareFaultDetected = false;
unsigned long lastTelemetryTime = 0;

void hardwareFaultISR() {
    OCR1A = 130; 
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
    setPWMDuty(130); 
    
    Serial.println("SYSTEM BOOT: Allowing analog hardware to settle...");
    delay(1000); 
    
    Serial.println("SYSTEM BOOT: Calibrating Differential Zero Point...");
    long sumOffset = 0;
    for(int i = 0; i < 100; i++) {
        sumOffset += (analogRead(PIN_CURR_SENSE) - analogRead(PIN_VIRT_GND_SENSE));
        delay(5);
    }
    ZERO_OFFSET_ADC = (float)sumOffset / 100.0;
    
    EIFR = bit(INTF0); 
    attachInterrupt(digitalPinToInterrupt(PIN_FAULT_INTERRUPT), hardwareFaultISR, FALLING);
    
    Serial.print("CALIBRATION COMPLETE. Base Offset ADC: ");
    Serial.println(ZERO_OFFSET_ADC, 2);
    Serial.println("\n>>> SYSTEM LOCKED. Select Load Type First.");
    Serial.println(">>> Send 'LOAD SUPERCAP', 'LOAD NIMH', 'LOAD RESISTOR', or 'LOAD CUSTOM'.");
}

void loop() {
    if (hardwareFaultDetected && currentState != ERROR) {
        currentState = ERROR;
        Serial.println("\n==================================================");
        Serial.println(">>> CRITICAL ALARM: HARDWARE FAULT TRIPPED <<<");
        Serial.println(">>> RELAY OPENED. PWM LATCHED TO 130 (0mA). <<<");
        Serial.println(">>> SYSTEM LOCKED. PRESS RESET BUTTON TO CLEAR. <<<");
        Serial.println("==================================================\n");
    }

    long sumVolt = 0;
    long sumCurr = 0;
    long sumVirtGnd = 0;
    for(int i = 0; i < 20; i++) {
        sumVolt += analogRead(PIN_VOLT_SENSE);
        sumCurr += analogRead(PIN_CURR_SENSE);
        sumVirtGnd += analogRead(PIN_VIRT_GND_SENSE); 
        delay(2); 
    }
    
    float avgVoltADC = sumVolt / 20.0;
    float avgCurrADC = sumCurr / 20.0;
    float avgVirtGndADC = sumVirtGnd / 20.0;

    float absolute_ce = avgVoltADC * ADC_VOLT_GAIN;
    float absolute_we = avgCurrADC * ADC_VOLT_GAIN;
    float true_cap_voltage = absolute_ce - absolute_we;
    
    float rawDiffADC = avgCurrADC - avgVirtGndADC;
    float c = (rawDiffADC - ZERO_OFFSET_ADC) * ADC_CURR_GAIN_MA;
    
    // --- COULOMB COUNTING INTEGRATION ---
    unsigned long currentTime = millis();
    if (currentState == RUNNING) {
        unsigned long deltaT_ms = currentTime - lastIntegrationTime;
        float hoursElapsed = deltaT_ms / 3600000.0;
        
        if (currentPWMCommand < 130) {
            totalCharge_mAh += (c * hoursElapsed); // Current is positive
        } else if (currentPWMCommand > 130) {
            totalDischarge_mAh += (abs(c) * hoursElapsed); // Current is negative
        }
    }
    lastIntegrationTime = currentTime;
    
    if (currentState == RUNNING) {
        if (currentPWMCommand < 130) {
            if (true_cap_voltage >= targetVoltage || true_cap_voltage >= maxAllowableCeiling) {
                setPWMDuty(130);
                currentPWMCommand = 130;
                currentState = TARGET_REACHED;
                Serial.println("\n>>> GRACEFUL STOP: Upper Target Reached! <<<");
                Serial.println(">>> Current Halted. Set new TARGET to continue.\n");
            }
        }
        else if (currentPWMCommand > 130) {
            if (true_cap_voltage <= targetVoltage || true_cap_voltage <= safeFloorVoltage) {
                setPWMDuty(130);
                currentPWMCommand = 130;
                currentState = TARGET_REACHED;
                Serial.println("\n>>> GRACEFUL STOP: Lower Target Reached! <<<");
                Serial.println(">>> Current Halted. Set new TARGET to continue.\n");
            }
        }
    }
    
    // 4. PRINT TELEMETRY (Machine-Readable CSV Format)
    if (millis() - lastTelemetryTime >= 250) {
        if (currentState == ERROR) {
            Serial.print("ERROR,");
            Serial.print(absolute_ce, 3); Serial.print(",");
            Serial.println(absolute_we, 3);
        } else {
            Serial.print("DATA,"); // Prefix to tell Python this is valid telemetry
            
            if (currentState == AWAITING_LOAD) Serial.print("WAIT_LOAD,");
            else if (currentState == AWAITING_TARGET) Serial.print("WAIT_TGT,");
            else if (currentState == IDLE) Serial.print("IDLE,");
            else if (currentState == TARGET_REACHED) Serial.print("DONE,");
            else if (currentState == RUNNING && currentPWMCommand < 130) Serial.print("CHARGING,");
            else if (currentState == RUNNING && currentPWMCommand > 130) Serial.print("DISCHARGING,");
            else Serial.print("RUNNING,");
            
            Serial.print(currentLoadName); Serial.print(",");
            Serial.print(targetVoltage, 2); Serial.print(",");
            Serial.print(true_cap_voltage, 3); Serial.print(",");
            Serial.print(c, 3); Serial.print(",");
            Serial.print(totalCharge_mAh, 4); Serial.print(",");
            Serial.println(totalDischarge_mAh, 4);
        }
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
        cmd.toUpperCase(); 
        
        if (currentState == ERROR) {
            Serial.println(">>> REJECTED: SYSTEM IS IN ERROR LOCKOUT.");
            return;
        }

        if (cmd.startsWith("LOAD ")) {
            String type = cmd.substring(5);
            if (type == "SUPERCAP") {
                currentLoadName = "SUPERCAP";
                safeFloorVoltage = 0.05;
                maxAllowableCeiling = 2.30;
            } else if (type == "NIMH") {
                currentLoadName = "NIMH";
                safeFloorVoltage = 1.00;
                maxAllowableCeiling = 1.45;
            } else if (type == "RESISTOR") {
                currentLoadName = "RESISTOR";
                safeFloorVoltage = -2.30;
                maxAllowableCeiling = 2.30;
            } else if (type == "CUSTOM") {
                currentLoadName = "CUSTOM";
                safeFloorVoltage = -4.80;
                maxAllowableCeiling = 4.80;
            } else {
                Serial.println(">>> ERROR: Unknown load type.");
                return;
            }
            
            currentState = AWAITING_TARGET;
            setPWMDuty(130); 
            currentPWMCommand = 130;
            Serial.print("\n>>> LOAD SET: "); Serial.println(currentLoadName);
            Serial.print(">>> Guardrails Locked -> Floor: "); Serial.print(safeFloorVoltage, 2);
            Serial.print("V, Ceiling: "); Serial.print(maxAllowableCeiling, 2); Serial.println("V");
            Serial.println(">>> Send 'TARGET <volts>' to set your destination.\n");
        }
        else if (cmd.startsWith("TARGET ")) {
            if (currentState == AWAITING_LOAD) {
                Serial.println(">>> REJECTED: You must select a LOAD type first.");
                return;
            }
            float requestedTarget = cmd.substring(7).toFloat();
            if (requestedTarget >= safeFloorVoltage && requestedTarget <= maxAllowableCeiling) {
                targetVoltage = requestedTarget;
                currentState = IDLE;
                
                // Reset capacity counters when a new cycle target is set
                totalCharge_mAh = 0.0;
                totalDischarge_mAh = 0.0;
                
                Serial.print("\n>>> TARGET SET: "); 
                Serial.print(targetVoltage, 2); 
                Serial.println("V");
                Serial.println(">>> Send 'PWM <0-129>' to Charge. Send 'PWM <131-255>' to Discharge.\n");
            } else {
                Serial.print(">>> ERROR: Target must be between ");
                Serial.print(safeFloorVoltage, 2); Serial.print("V and ");
                Serial.print(maxAllowableCeiling, 2); Serial.println("V for this load.");
            }
        } 
        else if (cmd.startsWith("PWM ")) {
            if (currentState == AWAITING_LOAD || currentState == AWAITING_TARGET) {
                Serial.println(">>> REJECTED: Setup LOAD and TARGET first.");
                return;
            }
            int val = cmd.substring(4).toInt();
            if (val >= 0 && val <= 255) {
                currentPWMCommand = val;
                setPWMDuty(val);
                currentState = RUNNING;
                lastIntegrationTime = millis(); // Reset timer right as current begins
                Serial.print(">>> COMMAND ACCEPTED: PWM = ");
                Serial.println(val);
            } else {
                Serial.println(">>> ERROR: PWM must be 0-255");
            }
        }
    }
}