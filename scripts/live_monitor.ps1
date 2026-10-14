param (
    [string]$Port = "COM5",
    [int]$BaudRate = 115200
)

$Host.UI.RawUI.WindowTitle = "Routine-Aware Companion - Live Telemetry ($Port)"

Write-Host "Opening $Port at $BaudRate baud..." -ForegroundColor Cyan
$sp = New-Object System.IO.Ports.SerialPort $Port, $BaudRate
$sp.ReadTimeout = 2500
$sp.DtrEnable = $true
$sp.RtsEnable = $false

try {
    $sp.Open()
} catch {
    Write-Host "Failed to open $Port : $_" -ForegroundColor Red
    exit 1
}

Write-Host "Connected to $Port! Waiting for telemetry stream (Press Ctrl+C to stop)..." -ForegroundColor Green
Start-Sleep -Milliseconds 800

$lastRender = [DateTime]::MinValue

try {
    while ($true) {
        try {
            $line = $sp.ReadLine()
        } catch [TimeoutException] {
            continue
        }

        if (-not $line -or -not $line.StartsWith("{")) {
            continue
        }

        # Rate-limit screen refresh to ~4 times a second for clean display
        $now = [DateTime]::UtcNow
        if (($now - $lastRender).TotalMilliseconds -lt 250) {
            continue
        }
        $lastRender = $now

        try {
            $data = $line | ConvertFrom-Json
        } catch {
            continue
        }

        if ($data.src -ne "unified") {
            continue
        }

        $w = $data.wearable
        $b = $data.base
        $rf = $data.rf

        [Console]::Clear()

        Write-Host "======================================================================" -ForegroundColor Cyan
        Write-Host "         ROUTINE-AWARE COMPANION - LIVE TELEMETRY MONITOR             " -ForegroundColor White -BackgroundColor DarkBlue
        Write-Host "======================================================================" -ForegroundColor Cyan

        # RF Link Row
        $linkCol = if ($rf.link -eq "OK") { "Green" } else { "Red" }
        Write-Host " [RF LINK] " -NoNewline -ForegroundColor Yellow
        Write-Host "STATUS: $($rf.link) " -NoNewline -ForegroundColor $linkCol
        Write-Host "| RX Packets: $($rf.rx) | Drops: $($rf.drop) | Gaps: $($rf.pkt_gap)" -ForegroundColor Gray

        Write-Host "----------------------------------------------------------------------" -ForegroundColor DarkGray
        Write-Host " [1] WEARABLE NODE (Wireless ESP-NOW 10 Hz)" -ForegroundColor Yellow
        
        # Motion
        $mpuCol = if ($w.mpu_ok) { "Green" } else { "Red" }
        Write-Host "   MPU6050 Motion [$($mpuCol)]:" -ForegroundColor $mpuCol
        Write-Host ("     Accel (m/s²):  X: {0,6:F2}  Y: {1,6:F2}  Z: {2,6:F2}" -f [double]$w.ax, [double]$w.ay, [double]$w.az) -ForegroundColor White
        Write-Host ("     Gyro  (rad/s): X: {0,6:F2}  Y: {1,6:F2}  Z: {2,6:F2}" -f [double]$w.gx, [double]$w.gy, [double]$w.gz) -ForegroundColor Gray

        # Biometrics
        Write-Host "   Biometrics:" -ForegroundColor Yellow
        $dsCol = if ($w.ds_ok) { "Green" } else { "Red" }
        Write-Host ("     Skin Temp:     {0:F2} °C" -f [double]$w.skin_t) -NoNewline -ForegroundColor White
        Write-Host " (Sensor: $dsCol)" -ForegroundColor $dsCol

        $contactText = if ($w.finger) { "DETECTED" } else { "NO CONTACT" }
        $contactCol = if ($w.finger) { "Green" } else { "DarkYellow" }
        $hrText = if ($w.hr -and [double]$w.hr -gt 0) { ("{0:F0} BPM" -f [double]$w.hr) } else { "-- BPM" }
        Write-Host "     Heart Rate:    $hrText (Finger: " -NoNewline -ForegroundColor White
        Write-Host $contactText -NoNewline -ForegroundColor $contactCol
        Write-Host (" | IR: {0})" -f [int]$w.ir) -ForegroundColor Gray

        Write-Host "----------------------------------------------------------------------" -ForegroundColor DarkGray
        Write-Host " [2] BASE STATION SENSORS (Local Node)" -ForegroundColor Yellow
        
        # Ambient
        $dhtCol = if ($b.dht_ok) { "Green" } else { "Red" }
        Write-Host ("   Ambient Room:  {0:F1} °C | {1:F1} % RH (DHT11: {2})" -f [double]$b.ambient_t, [double]$b.humidity, $dhtCol) -ForegroundColor White
        
        # LDR
        $darkStr = if ($b.ldr_dark) { "DARK" } else { "BRIGHT" }
        Write-Host ("   Ambient Light: Level: {0} | Condition: {1}" -f [int]$b.ldr_raw, $darkStr) -ForegroundColor White

        # Pillbox
        $pillCol = if ($b.pillbox -eq "CLOSED") { "Green" } else { "Yellow" }
        Write-Host "   Pillbox Door:  " -NoNewline -ForegroundColor White
        Write-Host "[ $($b.pillbox) ]" -NoNewline -ForegroundColor $pillCol
        Write-Host " (Hall Sensor GPIO 32)" -ForegroundColor Gray

        Write-Host "----------------------------------------------------------------------" -ForegroundColor DarkGray
        Write-Host " [3] BASE STATION 7-LED STATUS" -ForegroundColor Yellow
        $leds = $b.leds
        $mStr = if ($leds.morning) { "ON " } else { "OFF" }
        $aStr = if ($leds.afternoon) { "ON " } else { "OFF" }
        $nStr = if ($leds.night) { "ON " } else { "OFF" }
        $cStr = if ($leds.caretaker) { "ALERT" } else { "OFF" }
        $sStr = if ($leds.status) { "PULSE" } else { "IDLE" }

        Write-Host "   Reminders:   Morning: " -NoNewline -ForegroundColor Gray
        Write-Host $mStr -NoNewline -ForegroundColor (if ($leds.morning) { "Green" } else { "DarkGray" })
        Write-Host " | Afternoon: " -NoNewline -ForegroundColor Gray
        Write-Host $aStr -NoNewline -ForegroundColor (if ($leds.afternoon) { "Green" } else { "DarkGray" })
        Write-Host " | Night: " -NoNewline -ForegroundColor Gray
        Write-Host $nStr -NoNewline -ForegroundColor (if ($leds.night) { "Green" } else { "DarkGray" })
        Write-Host " | Caretaker: " -NoNewline -ForegroundColor Gray
        Write-Host $cStr -ForegroundColor (if ($leds.caretaker) { "Red" } else { "DarkGray" })

        Write-Host "   Path Lights: Washroom: " -NoNewline -ForegroundColor Gray
        Write-Host ("{0,3}/255" -f [int]$leds.path_wc) -NoNewline -ForegroundColor White
        Write-Host " | Kitchen: " -NoNewline -ForegroundColor Gray
        Write-Host ("{0,3}/255" -f [int]$leds.path_kit) -NoNewline -ForegroundColor White
        Write-Host " | Heartbeat LED: " -NoNewline -ForegroundColor Gray
        Write-Host $sStr -ForegroundColor (if ($leds.status) { "Cyan" } else { "DarkGray" })

        Write-Host "======================================================================" -ForegroundColor Cyan
        Write-Host " Streaming live at 10 Hz from $Port. Press Ctrl+C to exit." -ForegroundColor DarkGray
    }
} finally {
    if ($sp.IsOpen) {
        $sp.Close()
    }
    Write-Host "`nDisconnected from $Port." -ForegroundColor Yellow
}
