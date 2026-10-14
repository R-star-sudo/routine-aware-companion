/**
 * @file main.cpp
 * @brief Base Station ESP32 Node - ESP-NOW Reception & USB Serial Bridge (Step 3)
 * @project Routine-Aware Companion - Hacktivate 2026
 *
 * This firmware:
 *   1. Receives ESP-NOW broadcast packets from the Wearable on Channel 1.
 *   2. Validates incoming frames (magic header 0xAA55 + XOR checksum).
 *   3. Passes validated packets to the main loop via a FreeRTOS queue
 *      (safe cross-task synchronization — no race conditions).
 *   4. Reads local sensors: DHT11 (ambient temp/humidity), Hall Effect (pillbox).
 *   5. Detects wearable staleness (no valid packet within timeout window).
 *   6. Outputs a unified JSON telemetry line over USB Serial (115200 baud)
 *      for the laptop data pipeline to ingest.
 *
 * Hardware Connections:
 *   - DHT11:  DATA -> GPIO 4 (with 10k pull-up recommended)
 *   - Hall Effect Sensor: SIGNAL -> GPIO 5 (INPUT_PULLUP, active LOW when magnet present)
 *   - USB Cable -> Laptop (Serial bridge at 115200 baud)
 */

#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <DHT.h>

// ============================================================================
// Hardware Pin Definitions
// ============================================================================
#define PIN_DHT11           4
#define PIN_HALL_EFFECT      5

// DHT sensor type
#define DHT_TYPE            DHT11

// ============================================================================
// RF & ESP-NOW Configuration (Must match Wearable)
// ============================================================================
#define ESPNOW_WIFI_CHANNEL     1
#define PACKET_MAGIC_HEADER     0xAA55

// ============================================================================
// Timing Constants
// ============================================================================
static const uint32_t DHT_POLL_INTERVAL_MS       = 2000;  // DHT11 minimum sampling period
static const uint32_t SERIAL_STATUS_INTERVAL_MS  = 5000;  // Periodic heartbeat log
static const uint32_t WEARABLE_STALE_TIMEOUT_MS  = 2000;  // Mark wearable data stale after 2s silence

// ============================================================================
// Wearable Telemetry Data Structure (EXACT REPLICA of wearable SensorData)
// Must be byte-identical to the struct defined in firmware/wearable/src/main.cpp.
// ============================================================================
#pragma pack(push, 1)
struct SensorData {
    uint16_t magic;            // 0xAA55: Frame sync / protocol identifier
    uint32_t packet_id;        // Monotonically increasing packet counter
    uint32_t timestamp_ms;     // Wearable node uptime in milliseconds

    // MPU6050: Inertial Motion Telemetry
    float ax;                  // Acceleration X (m/s^2)
    float ay;                  // Acceleration Y (m/s^2)
    float az;                  // Acceleration Z (m/s^2)
    float gx;                  // Gyroscope X (rad/s)
    float gy;                  // Gyroscope Y (rad/s)
    float gz;                  // Gyroscope Z (rad/s)

    // MAX30102: Pulse Oximetry / Heart Rate
    float heart_rate;          // Heart Rate (BPM), 0 if no finger detected
    uint32_t raw_ir;           // Infrared intensity (proximity/finger contact)
    bool finger_detected;      // Contact indicator

    // DS18B20: Baseline Peripheral Skin Temperature
    float skin_temp;           // Temperature (Celsius)

    // Hardware Health Flags
    bool mpu_ok;
    bool max_ok;
    bool ds_ok;

    // Integrity Checksum
    uint8_t checksum;          // XOR checksum across all preceding bytes
};
#pragma pack(pop)

// ============================================================================
// Base Station Local Sensor Data
// ============================================================================
struct BaseStationData {
    float ambient_temp;        // DHT11 ambient temperature (Celsius)
    float ambient_humidity;    // DHT11 relative humidity (%)
    bool  dht_ok;              // DHT11 read success flag
    bool  pillbox_open;        // Hall effect: true = open (magnet away), false = closed
};

// ============================================================================
// FreeRTOS Queue for Cross-Task Synchronization
// ============================================================================
// The ESP-NOW receive callback runs inside the Wi-Fi task (Core 0).
// The main loop() runs on Core 1. noInterrupts() only guards hardware ISRs
// on the local core and does NOT prevent the Wi-Fi task from writing concurrently.
// A FreeRTOS queue is the correct primitive: it is inherently thread-safe,
// handles the "new data ready" signal implicitly, and buffers up to
// QUEUE_DEPTH packets to absorb short bursts.
// ============================================================================
static const int QUEUE_DEPTH = 5;  // Buffer up to 5 packets (50ms at 10Hz)
static QueueHandle_t sensorQueue = NULL;

// ============================================================================
// Global State
// ============================================================================

// Reception statistics (only written from Wi-Fi task callback — no contention)
volatile uint32_t totalPacketsReceived = 0;
volatile uint32_t totalPacketsDropped = 0;

// Wearable freshness tracking
uint32_t lastValidPacketTime = 0;        // millis() when the last valid packet arrived
uint32_t lastSeenPacketId = 0;           // Last packet_id for sequence gap detection
bool wearableConnected = false;          // True when receiving packets within timeout

// Base station local readings
BaseStationData baseData = {0};

// Sensor instance
DHT dht(PIN_DHT11, DHT_TYPE);

// Non-blocking timers
uint32_t lastDhtPollTime = 0;
uint32_t lastStatusPrintTime = 0;

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
 * @brief Reads the Hall Effect sensor state (digital, active LOW).
 *        Magnet present (lid closed) = LOW -> pillbox_open = false.
 *        Magnet absent  (lid open)   = HIGH -> pillbox_open = true.
 */
void updateHallEffect() {
    baseData.pillbox_open = (digitalRead(PIN_HALL_EFFECT) == HIGH);
}

/**
 * @brief Updates wearable connection freshness based on time since last valid packet.
 */
void updateWearableFreshness() {
    if (lastValidPacketTime > 0 && (millis() - lastValidPacketTime > WEARABLE_STALE_TIMEOUT_MS)) {
        if (wearableConnected) {
            wearableConnected = false;
            Serial.println(F("[WARN] Wearable link LOST — no packets received for 2s"));
        }
    }
}

// ============================================================================
// Serial JSON Output
// ============================================================================

/**
 * @brief Outputs a single-line JSON string combining wearable and base station data.
 *        Designed for direct ingestion by the laptop CSV/data pipeline.
 *
 * Format is kept on one line for easy serial parsing (no pretty print).
 */
void outputUnifiedJson(const SensorData &wearable, uint32_t pktGap) {
    // Build the JSON string manually for minimal overhead and no heap allocation.
    Serial.printf(
        "{\"src\":\"unified\","
        "\"wearable\":{"
            "\"pkt\":%u,"
            "\"ts\":%u,"
            "\"ax\":%.3f,\"ay\":%.3f,\"az\":%.3f,"
            "\"gx\":%.3f,\"gy\":%.3f,\"gz\":%.3f,"
            "\"hr\":%.1f,\"ir\":%u,\"finger\":%s,"
            "\"skin_t\":%.2f,"
            "\"mpu_ok\":%s,\"max_ok\":%s,\"ds_ok\":%s"
        "},"
        "\"base\":{"
            "\"ambient_t\":%.1f,\"humidity\":%.1f,\"dht_ok\":%s,"
            "\"pillbox\":\"%s\""
        "},"
        "\"rf\":{"
            "\"rx\":%u,\"drop\":%u,\"pkt_gap\":%u,\"link\":\"%s\""
        "}}\n",
        // Wearable fields
        (unsigned int)wearable.packet_id,
        (unsigned int)wearable.timestamp_ms,
        wearable.ax, wearable.ay, wearable.az,
        wearable.gx, wearable.gy, wearable.gz,
        wearable.heart_rate,
        (unsigned int)wearable.raw_ir,
        wearable.finger_detected ? "true" : "false",
        wearable.skin_temp,
        wearable.mpu_ok ? "true" : "false",
        wearable.max_ok ? "true" : "false",
        wearable.ds_ok  ? "true" : "false",
        // Base station fields
        baseData.ambient_temp,
        baseData.ambient_humidity,
        baseData.dht_ok ? "true" : "false",
        baseData.pillbox_open ? "OPEN" : "CLOSED",
        // RF statistics
        (unsigned int)totalPacketsReceived,
        (unsigned int)totalPacketsDropped,
        (unsigned int)pktGap,
        wearableConnected ? "OK" : "STALE"
    );
}

// ============================================================================
// Setup Routine
// ============================================================================
void setup() {
    Serial.begin(115200);
    while (!Serial && millis() < 2500) {
        // Await serial console connection
    }

    Serial.println(F("\n========================================================"));
    Serial.println(F(" Routine-Aware Companion - Base Station Node"));
    Serial.println(F(" Step 3: ESP-NOW Reception & USB Serial Bridge"));
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
    Serial.print(F("[DHT11] Initializing on GPIO "));
    Serial.print(PIN_DHT11);
    Serial.print(F("... "));
    dht.begin();
    // DHT11 does not have a connection check method, first real read validates
    Serial.println(F("OK (first read in 2s)"));

    Serial.print(F("[HALL] Initializing pillbox sensor on GPIO "));
    Serial.print(PIN_HALL_EFFECT);
    Serial.print(F(" (INPUT_PULLUP)... "));
    pinMode(PIN_HALL_EFFECT, INPUT_PULLUP);
    Serial.println(F("OK"));

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

    Serial.println(F("========================================================\n"));
    Serial.printf("[READY] Listening for wearable packets (struct size: %u bytes)...\n\n",
                  sizeof(SensorData));
}

// ============================================================================
// Main Execution Loop
// ============================================================================
void loop() {
    // 1. Service local sensors (non-blocking)
    updateDht11();
    updateHallEffect();
    updateWearableFreshness();

    // 2. Drain all queued wearable packets and output unified JSON
    SensorData received;
    while (xQueueReceive(sensorQueue, &received, 0) == pdTRUE) {
        // Track freshness and sequence gaps
        uint32_t pktGap = 0;
        if (lastSeenPacketId == 0) {
            // First packet received since base station boot
            pktGap = 0;
        } else if (received.packet_id < lastSeenPacketId) {
            // Wearable counter reset: transmitter rebooted or reset
            Serial.printf("[WARN] Wearable reboot detected (packet_id reset from %u to %u)\n",
                          (unsigned int)lastSeenPacketId, (unsigned int)received.packet_id);
            pktGap = 0;
        } else if (received.packet_id > lastSeenPacketId + 1) {
            pktGap = received.packet_id - lastSeenPacketId - 1;
        }
        lastSeenPacketId = received.packet_id;
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
        Serial.printf("[STATUS] Uptime: %lu ms | RX: %u | Dropped: %u | Link: %s | DHT: %s | Pillbox: %s\n",
                      (unsigned long)now,
                      (unsigned int)totalPacketsReceived,
                      (unsigned int)totalPacketsDropped,
                      wearableConnected ? "OK" : "STALE",
                      baseData.dht_ok ? "OK" : "ERR",
                      baseData.pillbox_open ? "OPEN" : "CLOSED");
    }
}
