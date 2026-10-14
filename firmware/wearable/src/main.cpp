/**
 * @file main.cpp
 * @brief Wearable ESP32 Node - Sensor Data Collection (Step 1)
 * @project Routine-Aware Companion - Hacktivate 2026
 *
 * This firmware initializes the hardware sensors on the wearable node,
 * reads telemetry in a non-blocking loop, and prints structured records
 * to the Serial monitor every 500ms for hardware verification.
 *
 * Hardware Connections:
 *   - I2C Bus (Shared):
 *       SDA -> GPIO 21
 *       SCL -> GPIO 22
 *       Devices: MPU6050 (0x68), MAX30102 (0x57)
 *   - 1-Wire Bus:
 *       DATA -> GPIO 4 (requires 4.7k pull-up resistor to 3.3V)
 *       Device: DS18B20
 */

#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <MAX30105.h>
#include <heartRate.h>
#include <OneWire.h>
#include <DallasTemperature.h>

// ============================================================================
// Pin Definitions & Bus Configuration
// ============================================================================
#define PIN_I2C_SDA         21
#define PIN_I2C_SCL         22
#define PIN_ONE_WIRE_BUS    4

// ============================================================================
// Timing Constants (Non-blocking intervals)
// ============================================================================
static const uint32_t SERIAL_PRINT_INTERVAL_MS = 500;  // Telemetry print frequency
static const uint32_t DS18B20_POLL_INTERVAL_MS  = 200;  // 10-bit conversion takes ~188ms
static const uint32_t MPU_SAMPLE_INTERVAL_MS    = 50;   // 20Hz MPU sampling

// ============================================================================
// Telemetry Data Structure
// Packed byte-aligned for direct future transmission over ESP-NOW in Step 2.
// ============================================================================
#pragma pack(push, 1)
struct SensorData {
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

// Non-blocking loop timers
uint32_t lastPrintTime = 0;
uint32_t lastMpuSampleTime = 0;
uint32_t lastDsRequestTime = 0;
bool dsConversionPending = false;

// Heart Rate calculation state
static const byte RATE_SIZE = 4;
byte rates[RATE_SIZE] = {0};
byte rateSpot = 0;
long lastBeat = 0;
float currentBpm = 0.0f;

// Contact detection threshold for MAX30102 IR channel
static const uint32_t IR_FINGER_THRESHOLD = 50000;

// ============================================================================
// Non-blocking Sensor Update Routines
// ============================================================================

/**
 * @brief Reads the MPU6050 accelerometer and gyroscope at periodic intervals.
 */
void updateMpu6050() {
    if (!currentTelemetry.mpu_ok) return;

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
        }
    }
}

/**
 * @brief Polls the MAX30102 FIFO buffer and computes heart rate if beat detected.
 *        Called continuously in loop() to avoid buffer overflow.
 */
void updateMax30102() {
    if (!currentTelemetry.max_ok) return;

    // particleSensor.check() pulls new samples from sensor FIFO into local buffer
    particleSensor.check();

    while (particleSensor.available()) {
        uint32_t irValue = particleSensor.getFIFOIR();
        particleSensor.nextSample(); // Advance FIFO read pointer

        currentTelemetry.raw_ir = irValue;

        if (irValue < IR_FINGER_THRESHOLD) {
            // No finger/skin contact detected
            currentTelemetry.finger_detected = false;
            currentTelemetry.heart_rate = 0.0f;
            currentBpm = 0.0f;
            continue;
        }

        currentTelemetry.finger_detected = true;

        // Peak detection algorithm from SparkFun heartRate.h
        if (checkForBeat(irValue)) {
            long delta = millis() - lastBeat;
            lastBeat = millis();

            if (delta > 250 && delta < 2500) { // Reasonable human BPM window (24 - 240 BPM)
                float beatsPerMinute = 60.0f / (delta / 1000.0f);

                rates[rateSpot++] = (byte)beatsPerMinute;
                rateSpot %= RATE_SIZE;

                // Compute smoothed running average
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

    currentTelemetry.heart_rate = currentBpm;
}

/**
 * @brief Asynchronously triggers and reads temperature from the DS18B20 sensor.
 *        Uses DallasTemperature's non-blocking mode to avoid stalling the loop.
 */
void updateDs18b20() {
    if (!currentTelemetry.ds_ok) return;

    uint32_t now = millis();

    if (!dsConversionPending) {
        // Request a new temperature conversion without waiting
        dallasSensors.requestTemperatures();
        lastDsRequestTime = now;
        dsConversionPending = true;
    } else {
        // Check if sufficient time has elapsed for 10-bit conversion (~188ms)
        if (now - lastDsRequestTime >= DS18B20_POLL_INTERVAL_MS) {
            float tempC = dallasSensors.getTempCByIndex(0);

            // DEVICE_DISCONNECTED_C is -127.0f
            if (tempC > -55.0f && tempC < 125.0f) {
                currentTelemetry.skin_temp = tempC;
            } else {
                currentTelemetry.skin_temp = -999.0f; // Invalid/Sensor read error
            }

            dsConversionPending = false;
        }
    }
}

/**
 * @brief Prints the formatted SensorData struct to the Serial monitor.
 */
void printTelemetry() {
    Serial.println(F("==================== WEARABLE TELEMETRY ===================="));
    Serial.printf("Packet ID: %u | Uptime: %lu ms\n", 
                  currentTelemetry.packet_id, (unsigned long)currentTelemetry.timestamp_ms);

    // MPU6050
    if (currentTelemetry.mpu_ok) {
        Serial.printf("[MPU6050] Accel (m/s^2) -> X: %6.2f | Y: %6.2f | Z: %6.2f\n",
                      currentTelemetry.ax, currentTelemetry.ay, currentTelemetry.az);
        Serial.printf("          Gyro  (rad/s) -> X: %6.2f | Y: %6.2f | Z: %6.2f\n",
                      currentTelemetry.gx, currentTelemetry.gy, currentTelemetry.gz);
    } else {
        Serial.println(F("[MPU6050] SENSOR ERROR / OFFLINE"));
    }

    // MAX30102
    if (currentTelemetry.max_ok) {
        Serial.printf("[MAX30102] IR: %6u | Contact: %s | Heart Rate: %5.1f BPM\n",
                      currentTelemetry.raw_ir,
                      currentTelemetry.finger_detected ? "YES" : "NO",
                      currentTelemetry.heart_rate);
    } else {
        Serial.println(F("[MAX30102] SENSOR ERROR / OFFLINE"));
    }

    // DS18B20
    if (currentTelemetry.ds_ok) {
        if (currentTelemetry.skin_temp > -100.0f) {
            Serial.printf("[DS18B20]  Skin Temp: %5.2f C\n", currentTelemetry.skin_temp);
        } else {
            Serial.println(F("[DS18B20]  Skin Temp: READ ERROR (-127C)"));
        }
    } else {
        Serial.println(F("[DS18B20]  SENSOR ERROR / OFFLINE"));
    }

    Serial.println(F("------------------------------------------------------------\n"));
}

// ============================================================================
// Setup Routine
// ============================================================================
void setup() {
    // 1. Initialize Serial Port
    Serial.begin(115200);
    while (!Serial && millis() < 2500) {
        // Short wait for native USB connection on some boards
    }

    Serial.println(F("\n========================================================"));
    Serial.println(F(" Routine-Aware Companion - Wearable Node"));
    Serial.println(F(" Step 1: Hardware Initialization & Sensor Data Acquisition"));
    Serial.println(F("========================================================"));

    // 2. Initialize Shared I2C Bus
    Serial.printf("[I2C] Initializing bus on SDA: GPIO %d, SCL: GPIO %d...\n", 
                  PIN_I2C_SDA, PIN_I2C_SCL);
    Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL);
    Wire.setClock(400000); // 400kHz Fast Mode for low latency sensor polling

    // 3. Initialize MPU6050
    Serial.print(F("[MPU6050] Initializing at 0x68... "));
    if (mpu.begin(0x68, &Wire)) {
        currentTelemetry.mpu_ok = true;
        mpu.setAccelerometerRange(MPU6050_RANGE_4_G);
        mpu.setGyroRange(MPU6050_RANGE_500_DEG);
        mpu.setFilterBandwidth(MPU6050_BAND_21_HZ);
        Serial.println(F("OK (Configured: +/-4G, 500 deg/s, 21Hz DLPF)"));
    } else {
        currentTelemetry.mpu_ok = false;
        Serial.println(F("FAILED! Check VCC, GND, SDA (21), SCL (22)"));
    }

    // 4. Initialize MAX30102
    Serial.print(F("[MAX30102] Initializing at 0x57... "));
    if (particleSensor.begin(Wire, I2C_SPEED_FAST)) {
        currentTelemetry.max_ok = true;

        // Configure sensor for Heart Rate mode
        byte ledBrightness = 60; // 0=Off to 255=50mA
        byte sampleAverage = 4;  // Options: 1, 2, 4, 8, 16, 32
        byte ledMode = 2;        // 2 = Red + IR (SpO2 / HR mode)
        int sampleRate = 200;    // Options: 50, 100, 200, 400, 800, 1000, 1600, 3200
        int pulseWidth = 411;    // Options: 69, 118, 215, 411 (microseconds)
        int adcRange = 4096;     // Options: 2048, 4096, 8192, 16384

        particleSensor.setup(ledBrightness, sampleAverage, ledMode, sampleRate, pulseWidth, adcRange);
        particleSensor.setPulseAmplitudeRed(0x0A); // Low Red LED to conserve power
        particleSensor.setPulseAmplitudeGreen(0);  // Green off
        Serial.println(F("OK (Configured: Red+IR, 200Hz, 4x Avg)"));
    } else {
        currentTelemetry.max_ok = false;
        Serial.println(F("FAILED! Check VCC, GND, and pull-ups"));
    }

    // 5. Initialize DS18B20 (1-Wire)
    Serial.printf("[DS18B20] Initializing on 1-Wire (GPIO %d)... ", PIN_ONE_WIRE_BUS);
    dallasSensors.begin();
    int deviceCount = dallasSensors.getDeviceCount();
    if (deviceCount > 0) {
        currentTelemetry.ds_ok = true;
        // Non-blocking mode: do not block execution while waiting for temperature conversion
        dallasSensors.setWaitForConversion(false);
        // 10-bit resolution -> 0.25 C precision with ~188ms conversion time
        dallasSensors.setResolution(10);
        dallasSensors.requestTemperatures();
        dsConversionPending = true;
        lastDsRequestTime = millis();
        Serial.printf("OK (%d device(s) found, 10-bit non-blocking)\n", deviceCount);
    } else {
        currentTelemetry.ds_ok = false;
        Serial.println(F("FAILED! No 1-Wire devices found. Verify 4.7k pull-up resistor to 3.3V"));
    }

    Serial.println(F("========================================================\n"));
    Serial.println(F("Starting telemetry collection loop...\n"));
}

// ============================================================================
// Main Execution Loop
// ============================================================================
void loop() {
    // 1. High-frequency sensor polling
    updateMax30102(); // Pulls FIFO samples continuously to prevent overflow
    updateMpu6050();   // 20Hz MPU sampling
    updateDs18b20();   // Non-blocking temperature conversion

    // 2. Periodic serial print every 500ms
    uint32_t currentMillis = millis();
    if (currentMillis - lastPrintTime >= SERIAL_PRINT_INTERVAL_MS) {
        lastPrintTime = currentMillis;

        // Stamp and advance packet count
        currentTelemetry.packet_id++;
        currentTelemetry.timestamp_ms = currentMillis;

        printTelemetry();
    }
}
