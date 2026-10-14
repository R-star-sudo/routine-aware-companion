/**
 * @file main.cpp
 * @brief Base Station ESP32 Node - ESP-NOW Reception & USB Serial Bridge (Step
 * 3)
 * @project Routine-Aware Companion - Hacktivate 2026
 *
 * This firmware:
 *   1. Receives ESP-NOW broadcast packets from the Wearable on Channel 1.
 *   2. Validates incoming frames (magic header 0xAA55 + XOR checksum).
 *   3. Passes validated packets to the main loop via a FreeRTOS queue
 *      (safe cross-task synchronization — no race conditions).
 *   4. Reads local sensors: DHT11 (ambient temp/humidity), Hall Effect
 * (pillbox).
 *   5. Detects wearable staleness (no valid packet within timeout window).
 *   6. Outputs a unified JSON telemetry line over USB Serial (115200 baud)
 *      for the laptop data pipeline to ingest.
 *
 * Hardware Connections:
 *   - DHT11:  DATA -> GPIO 4 (with 10k pull-up recommended)
 *   - Morning pillbox A3144: OUT -> GPIO 32 (external 10k pull-up; LOW closed,
 * HIGH open)
 *   - USB Cable -> Laptop (Serial bridge at 115200 baud)
 */

#include <Arduino.h>
#include <DHT.h>
#include <Preferences.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

// ============================================================================
// Hardware Pin Definitions
// ============================================================================
#define PIN_DHT11 4         // DHT11 Ambient Temperature & Humidity Data
#define PIN_LDR_DO 35       // LDR Digital Output (Threshold / LM393)
#define PIN_LDR_AO 36       // LDR Analog Output (ADC1_CH0 SENSOR_VP)
#define PIN_HALL_MORNING 32 // A3144 Hall Effect (Single physical pillbox, 10k pull-up)
#define PIN_LED_MORNING 16      // Morning pill reminder LED (GPIO 16 via 220 ohm)
#define PIN_LED_AFTERNOON 17    // Afternoon pill reminder LED (GPIO 17 via 220 ohm)
#define PIN_LED_NIGHT 18        // Night routine reminder LED (GPIO 18 via 220 ohm)
#define PIN_LED_CARETAKER 19    // Caretaker alert LED (GPIO 19 via 220 ohm)
#define PIN_LED_PATH_WC 23      // Path light to WC (GPIO 23 via 220 ohm, PWM / LEDC)
#define PIN_LED_PATH_KIT 27     // Path light to Kitchen (GPIO 27 via 220 ohm, PWM / LEDC)
#define PIN_LED_PATH_KITCHEN PIN_LED_PATH_KIT
#define PIN_LED_STATUS 25       // Base station status / heartbeat LED (GPIO 25 via 330 ohm)

// DHT sensor type
#define DHT_TYPE DHT11

// ============================================================================
// RF & ESP-NOW Configuration (Must match Wearable)
// ============================================================================
#define ESPNOW_WIFI_CHANNEL 1
#define PACKET_MAGIC_HEADER 0xAA55

// ============================================================================
// Timing Constants
// ============================================================================
static const uint32_t DHT_POLL_INTERVAL_MS =
    2000; // DHT11 minimum sampling period
static const uint32_t LDR_POLL_INTERVAL_MS = 200; // LDR sampling rate (5 Hz)
static const uint32_t SERIAL_STATUS_INTERVAL_MS =
    5000; // Periodic heartbeat log
static const uint32_t WEARABLE_STALE_TIMEOUT_MS =
    2000; // Mark wearable data stale after 2s silence
static const uint32_t SIMULATION_RATE_MAX =
    3600; // At most one virtual hour per real second
static const uint32_t PILLBOX_OPEN_CONFIRM_MS = 1500;
static const uint32_t REMINDER_LED_TIMEOUT_MS = 120000;
static const uint32_t STATUS_BLINK_SLOW_MS = 1000;
static const uint32_t STATUS_BLINK_FAST_MS = 200;
static const uint32_t CARETAKER_LED_PULSE_MS = 500;

// ============================================================================
// Wearable Telemetry Data Structure (EXACT REPLICA of wearable SensorData)
// Must be byte-identical to the struct defined in
// firmware/wearable/src/main.cpp.
// ============================================================================
#pragma pack(push, 1)
struct SensorData {
  uint16_t magic;        // 0xAA55: Frame sync / protocol identifier
  uint32_t packet_id;    // Monotonically increasing packet counter
  uint32_t timestamp_ms; // Wearable node uptime in milliseconds

  // MPU6050: Inertial Motion Telemetry
  float ax; // Acceleration X (m/s^2)
  float ay; // Acceleration Y (m/s^2)
  float az; // Acceleration Z (m/s^2)
  float gx; // Gyroscope X (rad/s)
  float gy; // Gyroscope Y (rad/s)
  float gz; // Gyroscope Z (rad/s)

  // MAX30102: Pulse Oximetry / Heart Rate
  float heart_rate;     // Heart Rate (BPM), 0 if no finger detected
  uint32_t raw_ir;      // Infrared intensity (proximity/finger contact)
  bool finger_detected; // Contact indicator

  // DS18B20: Baseline Peripheral Skin Temperature
  float skin_temp; // Temperature (Celsius)

  // Hardware Health Flags
  bool mpu_ok;
  bool max_ok;
  bool ds_ok;

  // Incremented by the wearable once per double-tap gesture.
  uint16_t tap_event_sequence;

  // Integrity Checksum
  uint8_t checksum; // XOR checksum across all preceding bytes
};
#pragma pack(pop)

// ============================================================================
// Base Station Local Sensor Data
// ============================================================================
struct BaseStationData {
  float ambient_temp;     // DHT11 ambient temperature (Celsius)
  float ambient_humidity; // DHT11 relative humidity (%)
  bool dht_ok;            // DHT11 read success flag
  bool pillbox_open;      // Single physical pillbox lid GPIO32: HIGH/open, LOW/closed
  uint16_t ldr_raw;           // LDR ADC raw reading (0-4095 on GPIO 36)
  bool ldr_dark; // LDR digital comparator output (GPIO 35: HIGH when dark)
  bool ldr_ok;   // LDR sensor initialized & sampled
};

// ============================================================================
// FreeRTOS Queue for Cross-Task Synchronization
// ============================================================================
// The ESP-NOW receive callback runs inside the Wi-Fi task (Core 0).
// The main loop() runs on Core 1. noInterrupts() only guards hardware ISRs
// on the local core and does NOT prevent the Wi-Fi task from writing
// concurrently. A FreeRTOS queue is the correct primitive: it is inherently
// thread-safe, handles the "new data ready" signal implicitly, and buffers up
// to QUEUE_DEPTH packets to absorb short bursts.
// ============================================================================
static const int QUEUE_DEPTH = 5; // Buffer up to 5 packets (50ms at 10Hz)
static QueueHandle_t sensorQueue = NULL;

// ============================================================================
// Global State
// ============================================================================

// Reception statistics (only written from Wi-Fi task callback — no contention)
volatile uint32_t totalPacketsReceived = 0;
volatile uint32_t totalPacketsDropped = 0;

// Wearable freshness tracking
uint32_t lastValidPacketTime = 0; // millis() when the last valid packet arrived
uint32_t lastSeenPacketId = 0;    // Last packet_id for sequence gap detection
bool wearableConnected = false;   // True when receiving packets within timeout
uint16_t lastTapEventSequence = 0;
bool haveTapEventSequence = false;

// Base station local readings
BaseStationData baseData = {0};

// Sensor instance
DHT dht(PIN_DHT11, DHT_TYPE);

// Non-blocking timers
uint32_t lastDhtPollTime = 0;
uint32_t lastLdrPollTime = 0;
uint32_t lastStatusPrintTime = 0;
uint32_t lastStatusLedToggleTime = 0;
uint32_t pillboxOpenedAt = 0;
bool previousPillboxOpen = false;
bool pillboxOpenTiming = false;

// All 7 Base Station LED states & timers
bool morningLedOn = false;
bool afternoonLedOn = false;
bool nightLedOn = false;
bool caretakerLedOn = false;
bool caretakerLedOutputOn = false;
bool statusLedOn = false;
uint8_t pathWcBrightness = 0;
uint8_t pathKitBrightness = 0;

uint32_t morningLedTurnedOnAt = 0;
uint32_t afternoonLedTurnedOnAt = 0;
uint32_t nightLedTurnedOnAt = 0;
uint32_t caretakerLedTurnedOnAt = 0;
uint32_t lastCaretakerPulseTime = 0;

struct ModelParameters {
  float mu_g;
  float sigma_g;
  uint32_t n;
  float p_break;
  bool valid;
};
ModelParameters modelParameters = {0.0f, 0.0f, 0, 0.0f, false};
Preferences preferences;

// Simulation wall clock. Device sampling and link timers continue to use
// millis().
bool simulationClockSet = false;
uint64_t simulationStartEpochSeconds = 0;
uint32_t simulationStartMillis = 0;
int32_t simulationUtcOffsetMinutes = 0;
uint16_t simulationRate = 60; // Virtual seconds per real second
static char serialCommandBuffer[128];
static size_t serialCommandLength = 0;

// ============================================================================
// Checksum Verification
// ============================================================================
/**
 * @brief Verifies the XOR checksum of an incoming SensorData packet.
 * @return true if checksum matches, false if packet is corrupt.
 */
bool verifyChecksum(const SensorData &data) {
  const uint8_t *bytePtr = (const uint8_t *)&data;
  size_t length = sizeof(SensorData) - sizeof(data.checksum);
  uint8_t chk = 0;
  for (size_t i = 0; i < length; i++) {
    chk ^= bytePtr[i];
  }
  return (chk == data.checksum);
}

/**
 * @brief Returns virtual UTC Unix time, or 0 when no simulation time was set.
 */
uint64_t getSimulationEpochSeconds() {
  if (!simulationClockSet)
    return 0;

  const uint32_t elapsedMillis = millis() - simulationStartMillis;
  const uint64_t simulatedElapsedSeconds =
      ((uint64_t)elapsedMillis * simulationRate) / 1000ULL;
  return simulationStartEpochSeconds + simulatedElapsedSeconds;
}

/**
 * @brief Diagnostic sweep across all 7 base station LEDs.
 *        Sequentially flashes each LED to visibly confirm electrical wiring.
 */
void runLedTestSweep() {
  Serial.print(F("[LEDS] Testing MORNING(16)... "));
  digitalWrite(PIN_LED_MORNING, HIGH);
  delay(150);
  digitalWrite(PIN_LED_MORNING, LOW);

  Serial.print(F("AFTERNOON(17)... "));
  digitalWrite(PIN_LED_AFTERNOON, HIGH);
  delay(150);
  digitalWrite(PIN_LED_AFTERNOON, LOW);

  Serial.print(F("NIGHT(18)... "));
  digitalWrite(PIN_LED_NIGHT, HIGH);
  delay(150);
  digitalWrite(PIN_LED_NIGHT, LOW);

  Serial.print(F("CARETAKER(19)... "));
  digitalWrite(PIN_LED_CARETAKER, HIGH);
  delay(150);
  digitalWrite(PIN_LED_CARETAKER, LOW);

  Serial.print(F("PATH_WC(23)... "));
  ledcWrite(0, 255);
  delay(150);
  ledcWrite(0, 0);

  Serial.print(F("PATH_KIT(27)... "));
  ledcWrite(1, 255);
  delay(150);
  ledcWrite(1, 0);

  Serial.print(F("STATUS(25)... "));
  digitalWrite(PIN_LED_STATUS, HIGH);
  delay(150);
  digitalWrite(PIN_LED_STATUS, LOW);

  Serial.println(F("OK (All 7 LEDs tested)"));
}

/**
 * @brief Reads a one-line clock command without blocking the sensor loop.
 *
 * Command format: SIM_CLOCK,<unix_seconds>,<utc_offset_minutes>,<rate>
 * Example: SIM_CLOCK,1791941400,330,60
 */
void handleSerialCommands() {
  while (Serial.available() > 0) {
    const char incoming = (char)Serial.read();
    if (incoming == '\r')
      continue;

    if (incoming != '\n') {
      if (serialCommandLength < sizeof(serialCommandBuffer) - 1) {
        serialCommandBuffer[serialCommandLength++] = incoming;
      } else {
        serialCommandLength = 0;
        Serial.println(F("[CLOCK] Command rejected: line too long"));
      }
      continue;
    }

    serialCommandBuffer[serialCommandLength] = '\0';
    serialCommandLength = 0;
    if (serialCommandBuffer[0] == '\0')
      continue;

    if (strncmp(serialCommandBuffer, "LED,", 4) == 0) {
      char name[24] = {0};
      char value[24] = {0};
      char trailing = '\0';
      const int fields = sscanf(serialCommandBuffer, "LED,%23[^,],%23[^,]%c",
                                name, value, &trailing);
      if (fields == 1 && strcmp(name, "TEST") == 0) {
        runLedTestSweep();
        Serial.println(F("ACK,LED,TEST,DONE"));
        continue;
      }
      if (fields != 2) {
        Serial.println(F("ERR,LED,FORMAT"));
        continue;
      }

      const bool turnOn = strcmp(value, "ON") == 0;
      const bool turnOff = strcmp(value, "OFF") == 0;

      if (strcmp(name, "MORNING") == 0 && (turnOn || turnOff)) {
        morningLedOn = turnOn;
        morningLedTurnedOnAt = turnOn ? millis() : 0;
        digitalWrite(PIN_LED_MORNING, morningLedOn ? HIGH : LOW);
      } else if (strcmp(name, "AFTERNOON") == 0 && (turnOn || turnOff)) {
        afternoonLedOn = turnOn;
        afternoonLedTurnedOnAt = turnOn ? millis() : 0;
        digitalWrite(PIN_LED_AFTERNOON, afternoonLedOn ? HIGH : LOW);
      } else if (strcmp(name, "NIGHT") == 0 && (turnOn || turnOff)) {
        nightLedOn = turnOn;
        nightLedTurnedOnAt = turnOn ? millis() : 0;
        digitalWrite(PIN_LED_NIGHT, nightLedOn ? HIGH : LOW);
      } else if (strcmp(name, "CARETAKER") == 0 && (turnOn || turnOff)) {
        caretakerLedOn = turnOn;
        caretakerLedTurnedOnAt = turnOn ? millis() : 0;
        lastCaretakerPulseTime = millis();
        caretakerLedOutputOn = turnOn;
        digitalWrite(PIN_LED_CARETAKER, caretakerLedOutputOn ? HIGH : LOW);
      } else if (strcmp(name, "PATH_WC") == 0) {
        long brightness = 0;
        if (turnOn) {
          brightness = 255;
        } else if (turnOff) {
          brightness = 0;
        } else {
          char *end = NULL;
          brightness = strtol(value, &end, 10);
          if (*value == '\0' || *end != '\0' || brightness < 0 ||
              brightness > 255) {
            Serial.println(F("ERR,LED,BRIGHTNESS"));
            continue;
          }
        }
        pathWcBrightness = (uint8_t)brightness;
        ledcWrite(0, pathWcBrightness);
      } else if (strcmp(name, "PATH_KIT") == 0) {
        long brightness = 0;
        if (turnOn) {
          brightness = 255;
        } else if (turnOff) {
          brightness = 0;
        } else {
          char *end = NULL;
          brightness = strtol(value, &end, 10);
          if (*value == '\0' || *end != '\0' || brightness < 0 ||
              brightness > 255) {
            Serial.println(F("ERR,LED,BRIGHTNESS"));
            continue;
          }
        }
        pathKitBrightness = (uint8_t)brightness;
        ledcWrite(1, pathKitBrightness);
      } else if (strcmp(name, "STATUS") == 0 && (turnOn || turnOff)) {
        statusLedOn = turnOn;
        digitalWrite(PIN_LED_STATUS, statusLedOn ? HIGH : LOW);
      } else if (strcmp(name, "ALL") == 0 && (turnOn || turnOff)) {
        morningLedOn = turnOn;
        afternoonLedOn = turnOn;
        nightLedOn = turnOn;
        caretakerLedOn = turnOn;
        caretakerLedOutputOn = turnOn;
        morningLedTurnedOnAt = turnOn ? millis() : 0;
        afternoonLedTurnedOnAt = turnOn ? millis() : 0;
        nightLedTurnedOnAt = turnOn ? millis() : 0;
        caretakerLedTurnedOnAt = turnOn ? millis() : 0;
        pathWcBrightness = turnOn ? 255 : 0;
        pathKitBrightness = turnOn ? 255 : 0;
        digitalWrite(PIN_LED_MORNING, turnOn ? HIGH : LOW);
        digitalWrite(PIN_LED_AFTERNOON, turnOn ? HIGH : LOW);
        digitalWrite(PIN_LED_NIGHT, turnOn ? HIGH : LOW);
        digitalWrite(PIN_LED_CARETAKER, turnOn ? HIGH : LOW);
        ledcWrite(0, pathWcBrightness);
        ledcWrite(1, pathKitBrightness);
      } else {
        Serial.println(F("ERR,LED,VALUE"));
        continue;
      }
      Serial.printf("ACK,LED,%s,%s\n", name, value);
      continue;
    }

    if (strncmp(serialCommandBuffer, "P,", 2) == 0) {
      float mu = 0.0f;
      float sigma = 0.0f;
      unsigned long sampleCount = 0;
      float breakProbability = 0.0f;
      char trailing = '\0';
      const int fields =
          sscanf(serialCommandBuffer, "P,%f,%f,%lu,%f%c", &mu, &sigma,
                 &sampleCount, &breakProbability, &trailing);
      if (fields != 4 || !isfinite(mu) || !isfinite(sigma) ||
          !isfinite(breakProbability) || sigma <= 0.0f || sampleCount == 0 ||
          sampleCount > UINT32_MAX || breakProbability < 0.0f ||
          breakProbability > 1.0f) {
        Serial.println(F("ERR,P,FORMAT"));
        continue;
      }

      modelParameters.mu_g = mu;
      modelParameters.sigma_g = sigma;
      modelParameters.n = (uint32_t)sampleCount;
      modelParameters.p_break = breakProbability;
      modelParameters.valid = true;
      preferences.putFloat("mu_g", mu);
      preferences.putFloat("sigma_g", sigma);
      preferences.putUInt("model_n", modelParameters.n);
      preferences.putFloat("p_break", breakProbability);
      preferences.putBool("model_valid", true);
      Serial.println(F("ACK,P,STORED"));
      continue;
    }

    if (strcmp(serialCommandBuffer, "TEST_SPEAK,V01") == 0) {
      Serial.println(F("SPEAK,V01"));
      continue;
    }

    unsigned long long epochSeconds = 0;
    long utcOffsetMinutes = 0;
    unsigned int rate = 0;
    char trailing = '\0';
    const int fields =
        sscanf(serialCommandBuffer, "SIM_CLOCK,%llu,%ld,%u%c", &epochSeconds,
               &utcOffsetMinutes, &rate, &trailing);

    if (fields != 3 || epochSeconds == 0 || utcOffsetMinutes < -840 ||
        utcOffsetMinutes > 840 || rate == 0 || rate > SIMULATION_RATE_MAX) {
      Serial.println(F("[CLOCK] Command rejected. Expected "
                       "SIM_CLOCK,<unix_seconds>,<utc_offset_minutes>,<rate>"));
      continue;
    }

    simulationStartEpochSeconds = (uint64_t)epochSeconds;
    simulationStartMillis = millis();
    simulationUtcOffsetMinutes = (int32_t)utcOffsetMinutes;
    simulationRate = (uint16_t)rate;
    simulationClockSet = true;

    Serial.printf("[CLOCK] Simulation time set: unix=%llu, utc_offset_min=%ld, "
                  "rate=%.2f virtual-seconds/real-second\n",
                  (unsigned long long)simulationStartEpochSeconds,
                  (long)simulationUtcOffsetMinutes, (double)simulationRate);
  }
}

// ============================================================================
// ESP-NOW Receive Callback
// ============================================================================
/**
 * @brief Called by the ESP-NOW stack when a packet arrives.
 *        Runs in the Wi-Fi task context on Core 0.
 *        Validates frame integrity, then enqueues via FreeRTOS queue
 *        for safe consumption by loop() on Core 1.
 */
void onDataRecv(const uint8_t *mac_addr, const uint8_t *incomingData, int len) {
  // Reject packets with unexpected size
  if (len != sizeof(SensorData)) {
    totalPacketsDropped++;
    return;
  }

  // Copy raw bytes into a temporary struct for validation
  SensorData incoming;
  memcpy(&incoming, incomingData, sizeof(SensorData));

  // Validate magic header
  if (incoming.magic != PACKET_MAGIC_HEADER) {
    totalPacketsDropped++;
    return;
  }

  // Validate XOR checksum
  if (!verifyChecksum(incoming)) {
    totalPacketsDropped++;
    return;
  }

  // Packet is valid — enqueue for the main loop (non-blocking, discard if full)
  if (xQueueSendToBack(sensorQueue, &incoming, 0) == pdTRUE) {
    totalPacketsReceived++;
  } else {
    // Queue full — drop oldest and try again
    SensorData discard;
    if (xQueueReceive(sensorQueue, &discard, 0) == pdTRUE) {
      totalPacketsDropped++; // Count the discarded old packet
    }
    if (xQueueSendToBack(sensorQueue, &incoming, 0) == pdTRUE) {
      totalPacketsReceived++;
    } else {
      totalPacketsDropped++; // Count incoming packet if retry enqueue fails
    }
  }
}

// ============================================================================
// Local Sensor Reads
// ============================================================================

/**
 * @brief Non-blocking DHT11 read every 2 seconds.
 */
void updateDht11() {
  uint32_t now = millis();
  if (now - lastDhtPollTime >= DHT_POLL_INTERVAL_MS) {
    lastDhtPollTime = now;

    float t = dht.readTemperature();
    float h = dht.readHumidity();

    if (!isnan(t) && !isnan(h)) {
      baseData.ambient_temp = t;
      baseData.ambient_humidity = h;
      baseData.dht_ok = true;
    } else {
      baseData.dht_ok = false;
    }
  }
}

/**
 * @brief Reads the LDR (Light Dependent Resistor) ambient light sensor.
 *        - GPIO 36 (AO): Analog light level via 12-bit ADC (0 to 4095).
 *        - GPIO 35 (DO): Digital comparator threshold output (HIGH when dark).
 */
void updateLdr() {
  uint32_t now = millis();
  if (now - lastLdrPollTime >= LDR_POLL_INTERVAL_MS) {
    lastLdrPollTime = now;

    baseData.ldr_raw = analogRead(PIN_LDR_AO);
    baseData.ldr_dark = (digitalRead(PIN_LDR_DO) == HIGH);
    baseData.ldr_ok = true;
  }
}

/**
 * @brief Reads the morning & afternoon pillbox Hall sensors and confirms an
 * interaction after an open state lasting at least 1.5 seconds and a close
 * transition. This reports a container interaction only; it cannot confirm
 * ingestion.
 */
void updateHallEffect() {
  baseData.pillbox_open = (digitalRead(PIN_HALL_MORNING) == HIGH);
  if (baseData.pillbox_open && !previousPillboxOpen) {
    pillboxOpenedAt = millis();
    pillboxOpenTiming = true;
  } else if (!baseData.pillbox_open && previousPillboxOpen) {
    if (pillboxOpenTiming &&
        millis() - pillboxOpenedAt >= PILLBOX_OPEN_CONFIRM_MS) {
      morningLedOn = false;
      digitalWrite(PIN_LED_MORNING, LOW);
      Serial.println(F("EVENT,PILLBOX_INTERACTION"));
    }
    pillboxOpenedAt = 0;
    pillboxOpenTiming = false;
  }
  previousPillboxOpen = baseData.pillbox_open;
}

void updateStatusLed() {
  const uint32_t interval =
      wearableConnected ? STATUS_BLINK_SLOW_MS : STATUS_BLINK_FAST_MS;
  const uint32_t now = millis();
  if (now - lastStatusLedToggleTime >= interval) {
    lastStatusLedToggleTime = now;
    statusLedOn = !statusLedOn;
    digitalWrite(PIN_LED_STATUS, statusLedOn ? HIGH : LOW);
  }
}

void updateReminderLedTimeouts() {
  const uint32_t now = millis();

  // Morning reminder LED auto-off after timeout
  if (morningLedOn && now - morningLedTurnedOnAt >= REMINDER_LED_TIMEOUT_MS) {
    morningLedOn = false;
    morningLedTurnedOnAt = 0;
    digitalWrite(PIN_LED_MORNING, LOW);
  }

  // Afternoon reminder LED auto-off after timeout
  if (afternoonLedOn &&
      now - afternoonLedTurnedOnAt >= REMINDER_LED_TIMEOUT_MS) {
    afternoonLedOn = false;
    afternoonLedTurnedOnAt = 0;
    digitalWrite(PIN_LED_AFTERNOON, LOW);
  }

  // Night reminder LED auto-off after timeout
  if (nightLedOn && now - nightLedTurnedOnAt >= REMINDER_LED_TIMEOUT_MS) {
    nightLedOn = false;
    nightLedTurnedOnAt = 0;
    digitalWrite(PIN_LED_NIGHT, LOW);
  }

  // Caretaker alert LED pulse & auto-off after timeout
  if (caretakerLedOn &&
      now - caretakerLedTurnedOnAt >= REMINDER_LED_TIMEOUT_MS) {
    caretakerLedOn = false;
    caretakerLedTurnedOnAt = 0;
    caretakerLedOutputOn = false;
    digitalWrite(PIN_LED_CARETAKER, LOW);
  } else if (caretakerLedOn &&
             now - lastCaretakerPulseTime >= CARETAKER_LED_PULSE_MS) {
    lastCaretakerPulseTime = now;
    caretakerLedOutputOn = !caretakerLedOutputOn;
    digitalWrite(PIN_LED_CARETAKER, caretakerLedOutputOn ? HIGH : LOW);
  }
}

void handleWearableTapEvent(const SensorData &wearable) {
  if (!haveTapEventSequence) {
    lastTapEventSequence = wearable.tap_event_sequence;
    haveTapEventSequence = true;
    return;
  }
  if (wearable.tap_event_sequence == lastTapEventSequence)
    return;

  lastTapEventSequence = wearable.tap_event_sequence;

  // Clear all active reminders upon wrist double-tap acknowledgment
  morningLedOn = false;
  afternoonLedOn = false;
  nightLedOn = false;
  caretakerLedOn = false;
  morningLedTurnedOnAt = 0;
  afternoonLedTurnedOnAt = 0;
  nightLedTurnedOnAt = 0;
  caretakerLedTurnedOnAt = 0;
  caretakerLedOutputOn = false;

  digitalWrite(PIN_LED_MORNING, LOW);
  digitalWrite(PIN_LED_AFTERNOON, LOW);
  digitalWrite(PIN_LED_NIGHT, LOW);
  digitalWrite(PIN_LED_CARETAKER, LOW);

  Serial.printf("TAP,DOUBLE,%u\n", (unsigned int)lastTapEventSequence);
}

/**
 * @brief Updates wearable connection freshness based on time since last valid
 * packet.
 */
void updateWearableFreshness() {
  if (lastValidPacketTime > 0 &&
      (millis() - lastValidPacketTime > WEARABLE_STALE_TIMEOUT_MS)) {
    if (wearableConnected) {
      wearableConnected = false;
      Serial.println(
          F("[WARN] Wearable link LOST — no packets received for 2s"));
    }
  }
}

// ============================================================================
// Serial JSON Output
// ============================================================================

/**
 * @brief Formats a finite sensor value as JSON or emits JSON null when absent.
 */
static void formatJsonFloat(char *output, size_t outputSize, float value,
                            uint8_t precision, bool valid) {
  if (!valid || !isfinite(value)) {
    snprintf(output, outputSize, "null");
    return;
  }
  snprintf(output, outputSize, "%.*f", (int)precision, (double)value);
}

/**
 * @brief Outputs a single-line JSON string combining wearable and base station
 * data. Designed for direct ingestion by the laptop CSV/data pipeline.
 *
 * Format is kept on one line for easy serial parsing (no pretty print).
 */
void outputUnifiedJson(const SensorData &wearable, uint32_t pktGap) {
  // Build the JSON string manually for minimal overhead and no heap allocation.
  char ax[20], ay[20], az[20], gx[20], gy[20], gz[20];
  char heartRate[20], skinTemperature[20], roomTemperature[20],
      roomHumidity[20];
  formatJsonFloat(ax, sizeof(ax), wearable.ax, 3, wearable.mpu_ok);
  formatJsonFloat(ay, sizeof(ay), wearable.ay, 3, wearable.mpu_ok);
  formatJsonFloat(az, sizeof(az), wearable.az, 3, wearable.mpu_ok);
  formatJsonFloat(gx, sizeof(gx), wearable.gx, 3, wearable.mpu_ok);
  formatJsonFloat(gy, sizeof(gy), wearable.gy, 3, wearable.mpu_ok);
  formatJsonFloat(gz, sizeof(gz), wearable.gz, 3, wearable.mpu_ok);
  formatJsonFloat(heartRate, sizeof(heartRate), wearable.heart_rate, 1,
                  wearable.max_ok && wearable.finger_detected &&
                      wearable.heart_rate > 0);
  formatJsonFloat(skinTemperature, sizeof(skinTemperature), wearable.skin_temp,
                  2, wearable.ds_ok);
  formatJsonFloat(roomTemperature, sizeof(roomTemperature),
                  baseData.ambient_temp, 1, baseData.dht_ok);
  formatJsonFloat(roomHumidity, sizeof(roomHumidity), baseData.ambient_humidity,
                  1, baseData.dht_ok);

  Serial.printf(
      "{\"src\":\"unified\","
      "\"wearable\":{"
      "\"pkt\":%u,"
      "\"ts\":%u,"
      "\"ax\":%s,\"ay\":%s,\"az\":%s,"
      "\"gx\":%s,\"gy\":%s,\"gz\":%s,"
      "\"hr\":%s,\"ir\":%u,\"finger\":%s,"
      "\"skin_t\":%s,"
      "\"mpu_ok\":%s,\"max_ok\":%s,\"ds_ok\":%s"
      "},"
      "\"base\":{"
      "\"ambient_t\":%s,\"humidity\":%s,\"dht_ok\":%s,"
      "\"ldr_raw\":%u,\"ldr_dark\":%s,\"ldr_ok\":%s,"
      "\"pillbox\":\"%s\","
      "\"leds\":{\"morning\":%s,\"afternoon\":%s,\"night\":%s,\"caretaker\":%s,\"path_wc\":%u,\"path_kit\":%u,\"status\":%s}"
      "},"
      "\"rf\":{"
      "\"rx\":%u,\"drop\":%u,\"pkt_gap\":%u,\"link\":\"%s\""
      "},"
      "\"clock\":{"
      "\"mode\":\"%s\",\"unix_s\":%llu,\"utc_offset_min\":%ld,\"rate\":%.2f"
      "}}\n",
      // Wearable fields
      (unsigned int)wearable.packet_id, (unsigned int)wearable.timestamp_ms, ax,
      ay, az, gx, gy, gz, heartRate, (unsigned int)wearable.raw_ir,
      wearable.finger_detected ? "true" : "false", skinTemperature,
      wearable.mpu_ok ? "true" : "false", wearable.max_ok ? "true" : "false",
      wearable.ds_ok ? "true" : "false",
      // Base station fields
      roomTemperature, roomHumidity, baseData.dht_ok ? "true" : "false",
      (unsigned int)baseData.ldr_raw, baseData.ldr_dark ? "true" : "false",
      baseData.ldr_ok ? "true" : "false",
      baseData.pillbox_open ? "OPEN" : "CLOSED",
      // LED states
      morningLedOn ? "true" : "false",
      afternoonLedOn ? "true" : "false",
      nightLedOn ? "true" : "false",
      caretakerLedOn ? "true" : "false",
      (unsigned int)pathWcBrightness,
      (unsigned int)pathKitBrightness,
      statusLedOn ? "true" : "false",
      // RF statistics
      (unsigned int)totalPacketsReceived, (unsigned int)totalPacketsDropped,
      (unsigned int)pktGap, wearableConnected ? "OK" : "STALE",
      simulationClockSet ? "simulated" : "unset",
      (unsigned long long)getSimulationEpochSeconds(),
      (long)simulationUtcOffsetMinutes,
      (double)(simulationClockSet ? simulationRate : 0));
}

// ============================================================================
// Setup Routine
// ============================================================================
void setup() {
  Serial.begin(115200);
  while (!Serial && millis() < 2500) {
    // Await serial console connection
  }

  Serial.println(
      F("\n========================================================"));
  Serial.println(F(" Routine-Aware Companion - Base Station Node"));
  Serial.println(F(" Step 3: ESP-NOW Reception, Actions & USB Serial Bridge"));
  Serial.println(F("========================================================"));

  // 0. Create FreeRTOS queue before any callbacks could fire
  sensorQueue = xQueueCreate(QUEUE_DEPTH, sizeof(SensorData));
  if (sensorQueue == NULL) {
    Serial.println(F("[FATAL] Failed to create FreeRTOS queue! Rebooting..."));
    delay(3000);
    ESP.restart();
  }
  Serial.printf("[QUEUE] Created FreeRTOS queue (depth: %d, item: %u bytes)\n",
                QUEUE_DEPTH, sizeof(SensorData));

  // 1. Initialize local sensors
  Serial.printf("[DHT11] Initializing ambient sensor on GPIO %d... ",
                PIN_DHT11);
  dht.begin();
  Serial.println(F("OK (first read in 2s)"));

  Serial.printf("[LDR] Initializing on AO: GPIO %d, DO: GPIO %d... ",
                PIN_LDR_AO, PIN_LDR_DO);
  pinMode(PIN_LDR_AO, INPUT);
  pinMode(PIN_LDR_DO, INPUT);
  analogSetPinAttenuation(PIN_LDR_AO, ADC_11db);
  Serial.println(F("OK"));

  Serial.printf(
      "[HALL] Initializing pillbox sensor on GPIO %d (external pull-up)... ",
      PIN_HALL_MORNING);
  pinMode(PIN_HALL_MORNING, INPUT);
  baseData.pillbox_open = (digitalRead(PIN_HALL_MORNING) == HIGH);
  previousPillboxOpen = baseData.pillbox_open;
  if (previousPillboxOpen) {
    pillboxOpenedAt = millis();
    pillboxOpenTiming = true;
  }
  Serial.println(F("OK"));

  // 2. Initialize all 7 Base Station LEDs
  Serial.println(F("[LEDS] Initializing all 7 Base Station LEDs..."));
  pinMode(PIN_LED_MORNING, OUTPUT);
  pinMode(PIN_LED_AFTERNOON, OUTPUT);
  pinMode(PIN_LED_NIGHT, OUTPUT);
  pinMode(PIN_LED_CARETAKER, OUTPUT);
  pinMode(PIN_LED_STATUS, OUTPUT);
  digitalWrite(PIN_LED_MORNING, LOW);
  digitalWrite(PIN_LED_AFTERNOON, LOW);
  digitalWrite(PIN_LED_NIGHT, LOW);
  digitalWrite(PIN_LED_CARETAKER, LOW);
  digitalWrite(PIN_LED_STATUS, LOW);

  // Setup PWM channels for path lights (0-255 brightness)
  ledcSetup(0, 5000, 8); // Channel 0, 5 kHz, 8-bit resolution
  ledcAttachPin(PIN_LED_PATH_WC, 0);
  ledcSetup(1, 5000, 8); // Channel 1, 5 kHz, 8-bit resolution
  ledcAttachPin(PIN_LED_PATH_KITCHEN, 1);
  ledcWrite(0, 0);
  ledcWrite(1, 0);

  // Run initial diagnostic LED test sweep on boot
  runLedTestSweep();

  preferences.begin("routine", false);
  modelParameters.mu_g = preferences.getFloat("mu_g", 0.0f);
  modelParameters.sigma_g = preferences.getFloat("sigma_g", 0.0f);
  modelParameters.n = preferences.getUInt("model_n", 0);
  modelParameters.p_break = preferences.getFloat("p_break", 0.0f);
  modelParameters.valid = preferences.getBool("model_valid", false);
  Serial.printf("[MODEL] Stored laptop parameters: %s\n",
                modelParameters.valid ? "available" : "not configured");

  // 2. Initialize Wi-Fi in Station Mode for ESP-NOW
  Serial.println(F("[RF] Configuring Wi-Fi Station Mode for ESP-NOW..."));
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();

  // Lock to Channel 1 to match the Wearable transmitter
  esp_wifi_set_promiscuous(true);
  esp_wifi_set_channel(ESPNOW_WIFI_CHANNEL, WIFI_SECOND_CHAN_NONE);
  esp_wifi_set_promiscuous(false);

  Serial.printf("[RF] MAC Address: %s | Locked Channel: %d\n",
                WiFi.macAddress().c_str(), ESPNOW_WIFI_CHANNEL);

  // 3. Initialize ESP-NOW Protocol
  Serial.print(F("[ESP-NOW] Initializing protocol... "));
  if (esp_now_init() == ESP_OK) {
    Serial.println(F("OK"));
  } else {
    Serial.println(F("FAILED! Rebooting..."));
    delay(3000);
    ESP.restart();
  }

  // Register receive callback
  esp_now_register_recv_cb(onDataRecv);

  Serial.println(
      F("========================================================\n"));
  Serial.printf(
      "[READY] Listening for wearable packets (struct size: %u bytes)...\n\n",
      sizeof(SensorData));
  Serial.println(
      F("[CLOCK] Time-based behavior is disabled until SIM_CLOCK is set."));
}

// ============================================================================
// Main Execution Loop
// ============================================================================
void loop() {
  // Accept optional virtual-time updates without blocking sensor/RF work.
  handleSerialCommands();

  // 1. Service local sensors (non-blocking)
  updateDht11();
  updateLdr();
  updateHallEffect();
  updateWearableFreshness();
  updateStatusLed();
  updateReminderLedTimeouts();

  // 2. Drain all queued wearable packets and output unified JSON
  SensorData received;
  while (xQueueReceive(sensorQueue, &received, 0) == pdTRUE) {
    // Track freshness and sequence gaps
    uint32_t pktGap = 0;
    bool wearableRestarted = false;
    if (lastSeenPacketId == 0) {
      // First packet received since base station boot
      pktGap = 0;
    } else if (received.packet_id < lastSeenPacketId) {
      // Wearable counter reset: transmitter rebooted or reset
      wearableRestarted = true;
      Serial.printf(
          "[WARN] Wearable reboot detected (packet_id reset from %u to %u)\n",
          (unsigned int)lastSeenPacketId, (unsigned int)received.packet_id);
      pktGap = 0;
    } else if (received.packet_id > lastSeenPacketId + 1) {
      pktGap = received.packet_id - lastSeenPacketId - 1;
    }
    lastSeenPacketId = received.packet_id;
    const bool tapSequenceReset =
        haveTapEventSequence &&
        received.tap_event_sequence < lastTapEventSequence;
    if (wearableRestarted || tapSequenceReset) {
      lastTapEventSequence = received.tap_event_sequence;
      haveTapEventSequence = true;
    } else {
      handleWearableTapEvent(received);
    }
    lastValidPacketTime = millis();

    if (!wearableConnected) {
      wearableConnected = true;
      Serial.println(F("[INFO] Wearable link ESTABLISHED"));
    }

    outputUnifiedJson(received, pktGap);
  }

  // 3. Periodic heartbeat status (every 5 seconds) for connection monitoring
  uint32_t now = millis();
  if (now - lastStatusPrintTime >= SERIAL_STATUS_INTERVAL_MS) {
    lastStatusPrintTime = now;
    Serial.printf(
        "[STATUS] Uptime: %lu ms | RX: %u | Dropped: %u | Link: %s | Ambient: "
        "%s C, %s%% | LDR: AO=%u (dark=%s) | Pillbox: %s | LEDs: M=%s A=%s N=%s C=%s WC=%u KIT=%u STAT=%s | Sim clock: "
        "%s (unix=%llu, offset=%+ldm, rate=%ux)\n",
        (unsigned long)now, (unsigned int)totalPacketsReceived,
        (unsigned int)totalPacketsDropped, wearableConnected ? "OK" : "STALE",
        baseData.dht_ok ? String(baseData.ambient_temp, 1).c_str() : "ERR",
        baseData.dht_ok ? String(baseData.ambient_humidity, 1).c_str() : "ERR",
        (unsigned int)baseData.ldr_raw, baseData.ldr_dark ? "YES" : "NO",
        baseData.pillbox_open ? "OPEN" : "CLOSED",
        morningLedOn ? "ON" : "OFF",
        afternoonLedOn ? "ON" : "OFF",
        nightLedOn ? "ON" : "OFF",
        caretakerLedOn ? "PULSE" : "OFF",
        (unsigned int)pathWcBrightness,
        (unsigned int)pathKitBrightness,
        statusLedOn ? "BLINK" : "OFF",
        simulationClockSet ? "SET" : "UNSET",
        (unsigned long long)getSimulationEpochSeconds(),
        (long)simulationUtcOffsetMinutes,
        (unsigned int)(simulationClockSet ? simulationRate : 0));
  }
}
