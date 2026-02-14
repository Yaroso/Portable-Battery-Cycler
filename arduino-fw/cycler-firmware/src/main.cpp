#include <Arduino.h>

void setup() {
    // Start Serial Communication at 115200 baud
    Serial.begin(115200);
    
    // Set Built-in LED (Pin 13) as Output
    pinMode(LED_BUILTIN, OUTPUT);
}

void loop() {
    // 1. Turn LED ON
    digitalWrite(LED_BUILTIN, HIGH);
    
    // 2. Send message to PC
    Serial.println("Hello from Arduino! (LED ON)");
    
    // 3. Wait 1 second
    delay(5000);
    
    // 4. Turn LED OFF
    digitalWrite(LED_BUILTIN, LOW);
    Serial.println("Hello from Arduino! (LED OFF)");
    delay(5000);
}