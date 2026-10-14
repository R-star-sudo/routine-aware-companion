/**
 * @file main.cpp
 * @brief Wearable ESP32 Node - Sensor Collection & ESP-NOW Transmission (Step 2)
 * @project Routine-Aware Companion - Hacktivate 2026
 *
 * This firmware:
 *   1. Initializes hardware sensors: MPU6050 (I2C), MAX30102 (I2C), DS18B20 (1-Wire).
 *   2. Samples biometrics and inertial telemetry using non-blocking schedules.
 *   3. Packages data into a packed, validated C-struct (magic header + XOR checksum).
 *   4. Broadcasts telemetry packets over ESP-NOW on fixed Channel 1 every 100ms (10 Hz).
 *   5. Outputs summary status to the Serial monitor for live debugging.
 *
 * Hardware Connections:
 *   - Shared I2C Bus:
 *       SDA -> GPIO 21
 *       SCL -> GPIO 22
 *       Devices: MPU6050 (0x68), MAX30102 (0x57)
 *   - 1-Wire Bus:
 *       DATA -> GPIO 4 (with 4.7k pull-up resistor to 3.3V)
 *       Device: DS18B20
 */

#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <MAX30105.h>
#include <heartRate.h>
#include <OneWire.h>
#include <DallasTemperature.h>

// ============================================================================
// Hardware Pin Definitions
// ============================================================================
#define PIN_I2C_SDA         21
#define PIN_I2C_SCL         22
#define PIN_ONE_WIRE_BUS    4

// ============================================================================
// RF & ESP-NOW Configuration
// ============================================================================
#define ESPNOW_WIFI_CHANNEL     1
#define PACKET_MAGIC_HEADER     0xAA55

// Broadcast MAC address: all listening nodes on Channel 1 receive telemetry
static const uint8_t BROADCAST_MAC[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

// ============================================================================
// Non-blocking Timing Intervals (ms)
// ============================================================================
static const uint32_t TRANSMIT_INTERVAL_MS      = 100;  // 10 Hz ESP-NOW transmission rate
static const uint32_t SERIAL_PRINT_INTERVAL_MS  = 500;  // Benchtop diagnostic print rate
static const uint32_t DS18B20_POLL_INTERVAL_MS  = 200;  // 10-bit conversion time (~188ms)
static const uint32_t MPU_SAMPLE_INTERVAL_MS    = 50;   // 20 Hz MPU sensor sampling

// Live sensor quality and staleness timeouts (ms)
static const uint32_t MPU_STALE_TIMEOUT_MS      = 500;  // Stale if no IMU sample in 500ms
static const uint32_t MAX30102_STALE_TIMEOUT_MS = 1000; // Stale if no FIFO sample in 1000ms
static const uint32_t DS18B20_STALE_TIMEOUT_MS  = 1500; // Stale if no temperature reading in 1500ms

// ============================================================================
// Telemetry Data Structure
// Packed byte-aligned with header verification and checksum.
// ============================================================================
#pragma pack(push, 1)
struct SensorData {
    uint16_t magic;            // 0xAA55: Frame sync / protocol identifier
    uint32_t packet_id;        // Monotonically increasing packet counter
    uint32_t timestamp_ms;     // Local node uptime in milliseconds

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
// Sensor Instances & Global State
// ============================================================================
Adafruit_MPU6050 mpu;
MAX30105 particleSensor;
OneWire oneWire(PIN_ONE_WIRE_BUS);
DallasTemperature dallasSensors(&oneWire);

// Active telemetry structure
SensorData currentTelemetry = {0};

// Hardware initialization flags
bool mpuInitialized = false;
bool maxInitialized = false;
bool dsInitialized  = false;

// Live data quality timestamps
uint32_t lastMpuSuccessTime = 0;
uint32_t lastMaxSampleTime  = 0;
uint32_t lastDsSuccessTime  = 0;

// Non-blocking loop timers
uint32_t lastTransmitTime = 0;
uint32_t lastPrintTime = 0;
uint32_t lastMpuSampleTime = 0;
uint32_t lastDsRequestTime = 0;
bool dsConversionPending = false;

// Transmission statistics
volatile uint32_t totalPacketsSent = 0;
volatile uint32_t totalPacketsFailed = 0;
volatile bool lastTxSuccess = true;

// Heart Rate calculation state
static const byte RATE_SIZE = 4;
byte rates[RATE_SIZE] = {0};
byte rateSpot = 0;
long lastBeat = 0;
float currentBpm = 0.0f;

// Contact detection threshold for MAX30102 IR channel
static const uint32_t IR_FINGER_THRESHOLD = 50000;

// ============================================================================
// Checksum Calculation Routine
// ============================================================================
/**
 * @brief Computes an 8-bit XOR checksum over all fields preceding the checksum byte.
 */
uint8_t calculateChecksum(const SensorData &data) {
    const uint8_t *bytePtr = (const uint8_t *)&data;
    size_t length = sizeof(SensorData) - sizeof(data.checksum);
    uint8_t chk = 0;
    for (size_t i = 0; i < length; i++) {
        chk ^= bytePtr[i];
    }
    return chk;
}

// ============================================================================
// ESP-NOW Callbacks
// ============================================================================
/**
 * @brief ESP-NOW send status callback.
 */
void onDataSent(const uint8_t *mac_addr, esp_now_send_status_t status) {
    if (status == ESP_NOW_SEND_SUCCESS) {
        totalPacketsSent++;
        lastTxSuccess = true;
    } else {
        totalPacketsFailed++;
        lastTxSuccess = false;
    }
}

// ============================================================================
// Non-blocking Sensor Update Routines
// ============================================================================

/**
 * @brief Reads MPU6050 at periodic intervals (20 Hz) and tracks live sensor health.
 */
void updateMpu6050() {
    if (!mpuInitialized) {
        currentTelemetry.mpu_ok = false;
        return;
    }

    uint32_t now = millis();
    if (now - lastMpuSampleTime >= MPU_SAMPLE_INTERVAL_MS) {
        lastMpuSampleTime = now;

        sensors_event_t a, g, temp;
        if (mpu.getEvent(&a, &g, &temp)) {
            currentTelemetry.ax = a.acceleration.x;
            currentTelemetry.ay = a.acceleration.y;
            currentTelemetry.az = a.acceleration.z;
            currentTelemetry.gx = g.gyro.x;
            currentTelemetry.gy = g.gyro.y;
            currentTelemetry.gz = g.gyro.z;
            currentTelemetry.mpu_ok = true;
            lastMpuSuccessTime = now;
        } else {
            currentTelemetry.mpu_ok = false;
        }
    }

    if (lastMpuSuccessTime > 0 && (now - lastMpuSuccessTime > MPU_STALE_TIMEOUT_MS)) {
        currentTelemetry.mpu_ok = false;
    }
}

/**
 * @brief Polls MAX30102 FIFO continuously to avoid overflow, tracks heart rate,
 *        and reflects live data freshness.
 */
void updateMax30102() {
    if (!maxInitialized) {
        currentTelemetry.max_ok = false;
        return;
    }

    particleSensor.check();

    uint32_t now = millis();
    while (particleSensor.available()) {
        uint32_t irValue = particleSensor.getFIFOIR();
        particleSensor.nextSample();

        lastMaxSampleTime = now;
        currentTelemetry.max_ok = true;
        currentTelemetry.raw_ir = irValue;

        if (irValue < IR_FINGER_THRESHOLD) {
            currentTelemetry.finger_detected = false;
            currentTelemetry.heart_rate = 0.0f;
            currentBpm = 0.0f;
            continue;
        }

        currentTelemetry.finger_detected = true;

        if (checkForBeat(irValue)) {
            long delta = millis() - lastBeat;
            lastBeat = millis();

            if (delta > 250 && delta < 2500) { // Valid human pulse range (24 - 240 BPM)
                float beatsPerMinute = 60.0f / (delta / 1000.0f);

                rates[rateSpot++] = (byte)beatsPerMinute;
                rateSpot %= RATE_SIZE;

                int beatAvg = 0;
                int validSamples = 0;
                for (byte x = 0; x < RATE_SIZE; x++) {
                    if (rates[x] > 0) {
                        beatAvg += rates[x];
                        validSamples++;
                    }
                }
                if (validSamples > 0) {
                    currentBpm = (float)beatAvg / validSamples;
                }
            }
        }
    }

    if (lastMaxSampleTime > 0 && (now - lastMaxSampleTime > MAX30102_STALE_TIMEOUT_MS)) {
        currentTelemetry.max_ok = false;
    }

    currentTelemetry.heart_rate = currentBpm;
}

/**
 * @brief Asynchronously requests and collects temperature from DS18B20,
 *        marking ds_ok true only after verified valid post-conversion readings.
 */
void updateDs18b20() {
    if (!dsInitialized) {
        currentTelemetry.ds_ok = false;
        return;
    }

    uint32_t now = millis();

    if (!dsConversionPending) {
        dallasSensors.requestTemperatures();
        lastDsRequestTime = now;
        dsConversionPending = true;
    } else {
        if (now - lastDsRequestTime >= DS18B20_POLL_INTERVAL_MS) {
            float tempC = dallasSensors.getTempCByIndex(0);

            if (tempC > -55.0f && tempC < 125.0f && tempC != DEVICE_DISCONNECTED_C) {
                currentTelemetry.skin_temp = tempC;
                currentTelemetry.ds_ok = true;
                lastDsSuccessTime = now;
            } else {
                currentTelemetry.skin_temp = -999.0f;
                currentTelemetry.ds_ok = false;
            }

            dsConversionPending = false;
        }
    }

    if (lastDsSuccessTime > 0 && (now - lastDsSuccessTime > DS18B20_STALE_TIMEOUT_MS)) {
        currentTelemetry.ds_ok = false;
    }
}

/**
 * @brief Transmits current telemetry structure via ESP-NOW broadcast.
 */
void transmitTelemetry() {
    currentTelemetry.magic = PACKET_MAGIC_HEADER;
    currentTelemetry.packet_id++;
    currentTelemetry.timestamp_ms = millis();
    currentTelemetry.checksum = calculateChecksum(currentTelemetry);

    esp_err_t result = esp_now_send(BROADCAST_MAC, (uint8_t *)&currentTelemetry, sizeof(SensorData));
    if (result != ESP_OK) {
        totalPacketsFailed++;
        lastTxSuccess = false;
    }
}

/**
 * @brief Prints live diagnostics and RF status to Serial monitor.
 */
void printDiagnostics() {
    Serial.println(F("================= WEARABLE RF & SENSOR TELEMETRY ================="));
    Serial.printf("Packet #%u | Uptime: %lu ms | RF Channel: %d | Payload: %u bytes\n",
                  currentTelemetry.packet_id,
                  (unsigned long)currentTelemetry.timestamp_ms,
                  ESPNOW_WIFI_CHANNEL,
                  sizeof(SensorData));

    Serial.printf("ESP-NOW Stats -> Sent: %lu | Failed: %lu | Status: %s\n",
                  (unsigned long)totalPacketsSent,
                  (unsigned long)totalPacketsFailed,
                  lastTxSuccess ? "OK" : "ERROR");

    // MPU6050
    if (currentTelemetry.mpu_ok) {
        Serial.printf("[MPU6050] Accel (m/s^2): X: %6.2f | Y: %6.2f | Z: %6.2f\n",
                      currentTelemetry.ax, currentTelemetry.ay, currentTelemetry.az);
        Serial.printf("          Gyro  (rad/s): X: %6.2f | Y: %6.2f | Z: %6.2f\n",
                      currentTelemetry.gx, currentTelemetry.gy, currentTelemetry.gz);
    } else {
        Serial.println(F("[MPU6050] OFFLINE / READ FAILURE"));
    }

    // MAX30102
    if (currentTelemetry.max_ok) {
        Serial.printf("[MAX30102] IR: %6u | Contact: %s | HR: %5.1f BPM\n",
                      currentTelemetry.raw_ir,
                      currentTelemetry.finger_detected ? "YES" : "NO",
                      currentTelemetry.heart_rate);
    } else {
        Serial.println(F("[MAX30102] OFFLINE / READ FAILURE"));
    }

    // DS18B20
    if (currentTelemetry.ds_ok) {
        if (currentTelemetry.skin_temp > -100.0f) {
            Serial.printf("[DS18B20]  Skin Temp: %5.2f C\n", currentTelemetry.skin_temp);
        } else {
            Serial.println(F("[DS18B20]  Skin Temp: READ ERROR (-127C)"));
        }
    } else {
        Serial.println(F("[DS18B20]  OFFLINE / READ FAILURE"));
    }

    Serial.printf("[CRC/SYNC] Magic: 0x%04X | Checksum: 0x%02X\n",
                  currentTelemetry.magic, currentTelemetry.checksum);
    Serial.println(F("-------------------------------------------------------------------\n"));
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
    Serial.println(F(" Routine-Aware Companion - Wearable Node"));
    Serial.println(F(" Step 2: Sensor Acquisition & 10Hz ESP-NOW Transmission"));
    Serial.println(F("========================================================"));

    // 1. Initialize Shared I2C Bus
    Serial.printf("[I2C] Initializing on SDA: GPIO %d, SCL: GPIO %d...\n", 
                  PIN_I2C_SDA, PIN_I2C_SCL);
    Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL);
    Wire.setClock(400000);

    // 2. Initialize MPU6050
    Serial.print(F("[MPU6050] Initializing at 0x68... "));
    if (mpu.begin(0x68, &Wire)) {
        mpuInitialized = true;
        currentTelemetry.mpu_ok = false; // Validated on first live sample
        mpu.setAccelerometerRange(MPU6050_RANGE_4_G);
        mpu.setGyroRange(MPU6050_RANGE_500_DEG);
        mpu.setFilterBandwidth(MPU6050_BAND_21_HZ);
        Serial.println(F("OK"));
    } else {
        mpuInitialized = false;
        currentTelemetry.mpu_ok = false;
        Serial.println(F("FAILED! Check wiring."));
    }

    // 3. Initialize MAX30102
    Serial.print(F("[MAX30102] Initializing at 0x57... "));
    if (particleSensor.begin(Wire, I2C_SPEED_FAST)) {
        maxInitialized = true;
        currentTelemetry.max_ok = false; // Validated on first live sample
        particleSensor.setup(60, 4, 2, 200, 411, 4096);
        particleSensor.setPulseAmplitudeRed(0x0A);
        particleSensor.setPulseAmplitudeGreen(0);
        Serial.println(F("OK"));
    } else {
        maxInitialized = false;
        currentTelemetry.max_ok = false;
        Serial.println(F("FAILED! Check wiring."));
    }

    // 4. Initialize DS18B20 (1-Wire)
    Serial.printf("[DS18B20] Initializing on GPIO %d... ", PIN_ONE_WIRE_BUS);
    currentTelemetry.skin_temp = -999.0f;
    currentTelemetry.ds_ok = false; // Validated on first completed conversion
    dallasSensors.begin();
    if (dallasSensors.getDeviceCount() > 0) {
        dsInitialized = true;
        dallasSensors.setWaitForConversion(false);
        dallasSensors.setResolution(10); // 10-bit resolution (~188ms)
        dallasSensors.requestTemperatures();
        dsConversionPending = true;
        lastDsRequestTime = millis();
        Serial.println(F("OK (10-bit non-blocking, awaiting first sample)"));
    } else {
        dsInitialized = false;
        Serial.println(F("FAILED! Verify 4.7k pull-up resistor."));
    }

    // 5. Initialize Wi-Fi in Station Mode for ESP-NOW
    Serial.println(F("[RF] Configuring Wi-Fi Station Mode for ESP-NOW..."));
    WiFi.mode(WIFI_STA);
    WiFi.disconnect();

    // Lock Wi-Fi radio to Channel 1
    esp_wifi_set_promiscuous(true);
    esp_wifi_set_channel(ESPNOW_WIFI_CHANNEL, WIFI_SECOND_CHAN_NONE);
    esp_wifi_set_promiscuous(false);

    Serial.printf("[RF] MAC Address: %s | Locked Channel: %d\n",
                  WiFi.macAddress().c_str(), ESPNOW_WIFI_CHANNEL);

    // 6. Initialize ESP-NOW Protocol
    Serial.print(F("[ESP-NOW] Initializing protocol... "));
    if (esp_now_init() == ESP_OK) {
        Serial.println(F("OK"));
    } else {
        Serial.println(F("FAILED! Rebooting..."));
        delay(3000);
        ESP.restart();
    }

    // Register transmission status callback
    esp_now_register_send_cb(onDataSent);

    // 7. Register Broadcast Peer
    Serial.print(F("[ESP-NOW] Registering broadcast peer (FF:FF:FF:FF:FF:FF)... "));
    esp_now_peer_info_t peerInfo = {};
    memcpy(peerInfo.peer_addr, BROADCAST_MAC, 6);
    peerInfo.channel = ESPNOW_WIFI_CHANNEL;
    peerInfo.encrypt = false;

    if (esp_now_add_peer(&peerInfo) == ESP_OK) {
        Serial.println(F("OK"));
    } else {
        Serial.println(F("FAILED to register peer!"));
    }

    Serial.println(F("========================================================\n"));
    Serial.println(F("Wearable node active: Transmitting telemetry at 10 Hz...\n"));
}

// ============================================================================
// Main Execution Loop
// ============================================================================
void loop() {
    // 1. High-frequency sensor servicing
    updateMax30102(); // Continuous FIFO drain
    updateMpu6050();   // 20 Hz inertial sampling
    updateDs18b20();   // Non-blocking temperature conversion

    uint32_t currentMillis = millis();

    // 2. Transmit ESP-NOW telemetry packet every 100ms (10 Hz)
    if (currentMillis - lastTransmitTime >= TRANSMIT_INTERVAL_MS) {
        lastTransmitTime = currentMillis;
        transmitTelemetry();
    }

    // 3. Print benchtop diagnostics to Serial every 500ms
    if (currentMillis - lastPrintTime >= SERIAL_PRINT_INTERVAL_MS) {
        lastPrintTime = currentMillis;
        printDiagnostics();
    }
}
