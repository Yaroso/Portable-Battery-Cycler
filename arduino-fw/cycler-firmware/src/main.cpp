/**
 * PUDELstat Firmware - Calibrated Diagnostic Mode
 * Platform: Arduino Uno R3 (ATmega328P)
 * Features: Auto-Zero Calibration & Moving Average Filter
 */

#include <Arduino.h>

// ==========================================
// HARDWARE PIN DEFINITIONS
// ==========================================
const int PIN_PWM_OUT = 9;      
const int PIN_VOLT_SENSE = A0;  
const int PIN_CURR_SENSE = A2;  
const int PIN_DIVIDER_EN = 6;   

// ==========================================
// CALIBRATION GLOBALS
// ==========================================
float CURRENT_ZERO_ADC = 512.0; // Will be overwritten by setup() calibration
const float ADC_CURR_GAIN_MA = 0.0718; // Based on 68 Ohm R2
const float ADC_VOLT_GAIN = (5.00 / 1023.0);

void setupTimer1_31kHz();
void setPWMDuty(uint8_t duty);
void handleSerialCommands();

void setup() {
    Serial.begin(115200);
    pinMode(PIN_PWM_OUT, OUTPUT);
    pinMode(PIN_DIVIDER_EN, OUTPUT);
    digitalWrite(PIN_DIVIDER_EN, HIGH); 
    
    setupTimer1_31kHz();
    setPWMDuty(127); // Command 2.5V (0mA) to establish baseline
    
    Serial.println("SYSTEM BOOT: Calibrating Zero Point...");
    delay(500); // Allow hardware filters to settle
    
    // Auto-Zero Routine: Average 100 samples to find the true hardware resting point
    long sumA2 = 0;
    for(int i = 0; i < 100; i++) {
        sumA2 += analogRead(PIN_CURR_SENSE);
        delay(5);
    }
    CURRENT_ZERO_ADC = (float)sumA2 / 100.0;
    
    Serial.print("CALIBRATION COMPLETE. True Zero ADC: ");
    Serial.println(CURRENT_ZERO_ADC, 2);
    Serial.println("Send 'PWM <0-255>' to test different states.");
    Serial.println("Raw_A0, Raw_A2, Volt_V, Curr_mA");
}

unsigned long lastTelemetryTime = 0;

void loop() {
    // Moving Average Filter: 20 samples to crush high-frequency noise
    long sumVolt = 0;
    long sumCurr = 0;
    
    for(int i = 0; i < 20; i++) {
        sumVolt += analogRead(PIN_VOLT_SENSE);
        sumCurr += analogRead(PIN_CURR_SENSE);
        delay(2); // Short delay to spread samples over time
    }
    
    float avgVoltADC = (float)sumVolt / 20.0;
    float avgCurrADC = (float)sumCurr / 20.0;
    
    // Calculate final telemetry
    float v = avgVoltADC * ADC_VOLT_GAIN;
    float c = (avgCurrADC - CURRENT_ZERO_ADC) * ADC_CURR_GAIN_MA;
    
    // Print telemetry only every 250ms (4 Hz)
    if (millis() - lastTelemetryTime >= 250) {
        Serial.print(avgVoltADC, 1); Serial.print(", ");
        Serial.print(avgCurrADC, 1); Serial.print(", ");
        Serial.print(v, 3); Serial.print(", ");
        Serial.println(c, 3);
        lastTelemetryTime = millis();
    }
    
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
            int val = cmd.substring(4).toInt();
            if (val >= 0 && val <= 255) {
                setPWMDuty(val);
                Serial.print(">>> COMMAND ACCEPTED: PWM = ");
                Serial.println(val);
            } else {
                Serial.println(">>> ERROR: PWM must be 0-255");
            }
        }
    }
}


// ==========================================
// EMPIRICAL CALIBRATION CONSTANTS
// TODO: Map PWM vs DMM Current to find true linear transfer function (y = mx + c)
// Current_mA = (ADC_Reading * CURRENT_SLOPE_M) + CURRENT_INTERCEPT_C
// ==========================================
//UNCOMMENT const float CURRENT_SLOPE_M = 0.0718;       // Replace with empirical 'm'
//UNCOMMENT const float CURRENT_INTERCEPT_C = 0.0000;   // Replace with empirical 'c'
// Note: Supercaps have extremely low ESR. Current will spike hard. 
// Ensure PID/Control loop is sufficiently damped before connecting one.