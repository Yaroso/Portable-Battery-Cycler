/**
 * Relay Core Hardware Verification
 * Toggles Digital Pin 7 to test 2N2222 and TQ2-5V switching.
 */

#include <Arduino.h>

const int PIN_RELAY_TEST = 7;

void setup() {
    Serial.begin(115200);
    pinMode(PIN_RELAY_TEST, OUTPUT);
    Serial.println("RELAY TEST INITIALIZED");
}

void loop() {
    // Turn Relay ON (Energize Coil)
    digitalWrite(PIN_RELAY_TEST, HIGH);
    Serial.println("COMMAND: RELAY ON (Circuit CLOSED)");
    delay(3000);

    // Turn Relay OFF (De-energize Coil)
    digitalWrite(PIN_RELAY_TEST, LOW);
    Serial.println("COMMAND: RELAY OFF (Circuit BROKEN)");
    delay(3000);
}