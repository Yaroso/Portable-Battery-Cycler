#include <Arduino.h>

const int PIN_PWM_OUT = 9;      
const int PIN_VOLT_SENSE = A0;  
const int PIN_CURR_SENSE = A2;  
const int PIN_DIVIDER_EN = 6;   

void setupTimer1_31kHz() {
    TCCR1A = 0; TCCR1B = 0;
    TCCR1A = _BV(COM1A1) | _BV(WGM10);
    TCCR1B = _BV(CS10);
}

void setup() {
    Serial.begin(115200);
    pinMode(PIN_PWM_OUT, OUTPUT);
    pinMode(PIN_DIVIDER_EN, OUTPUT);
    digitalWrite(PIN_DIVIDER_EN, HIGH); 
    
    setupTimer1_31kHz();
    OCR1A = 127; // Command 2.5V (0mA)
    
    Serial.println("DIAGNOSTIC_MODE_ACTIVE");
    Serial.println("Raw_A0, Raw_A2, Volt_V, Curr_mA");
}

void loop() {
    int rawVolt = analogRead(PIN_VOLT_SENSE);
    int rawCurr = analogRead(PIN_CURR_SENSE);
    
    float v = rawVolt * (5.00 / 1023.0);
    float c = (rawCurr - 512) * 0.0718;
    
    Serial.print(rawVolt); Serial.print(", ");
    Serial.print(rawCurr); Serial.print(", ");
    Serial.print(v, 3); Serial.print(", ");
    Serial.println(c, 3);
    
    delay(200); // 5Hz readable stream
}