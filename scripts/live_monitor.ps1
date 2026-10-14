param (
    [string]$Port = "COM5",
    [int]$BaudRate = 115200,
    [int]$Fps = 10,
    [string]$Time = "now",
    [int]$Rate = 1,
    [switch]$NoClock
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

if (-not $NoClock) {
    try {
        $now = [DateTime]::Now
        if (-not $Date -and ($Time -eq "now" -or [string]::IsNullOrWhiteSpace($Time))) {
            $targetDt = $now
        } elseif ($Date -and ($Time -eq "now" -or [string]::IsNullOrWhiteSpace($Time))) {
            $targetDt = [DateTime]::Parse("$Date $($now.ToString('HH:mm:ss'))")
        } elseif ($Date -and $Time) {
            $targetDt = [DateTime]::Parse("$Date $Time")
        } elseif ($Time -and -not $Date) {
            if ($Time -match "^\d{1,2}:\d{2}(:\d{2})?$") {
                $targetDt = [DateTime]::Parse("$($now.ToString('yyyy-MM-dd')) $Time")
            } else {
                $targetDt = [DateTime]::Parse($Time)
            }
        }
        $epoch = [int64]([DateTimeOffset]$targetDt).ToUnixTimeSeconds()
        $offsetMin = [int]([TimeZoneInfo]::Local.GetUtcOffset($targetDt).TotalMinutes)
        Write-Host "Initializing Base Station clock to: $($targetDt.ToString('yyyy-MM-dd HH:mm:ss')) ($($Rate)x speed)..." -ForegroundColor Green
        Start-Sleep -Milliseconds 300
        $sp.WriteLine("SIM_CLOCK,$epoch,$offsetMin,$Rate")
    } catch {
        Write-Host "[WARN] Could not parse/send clock: $_" -ForegroundColor Yellow
    }
}
Start-Sleep -Milliseconds 400

[Console]::Clear()
try { [Console]::CursorVisible = $false } catch {}

$minIntervalMs = 1000 / [Math]::Max(1, $Fps)
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

        # Rate-limit screen refresh to native stream rate
        $now = [DateTime]::UtcNow
        if (($now - $lastRender).TotalMilliseconds -lt $minIntervalMs) {
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
        $clock = $data.clock

        [Console]::SetCursorPosition(0, 0)

        Write-Host "======================================================================" -ForegroundColor Cyan
        Write-Host "         ROUTINE-AWARE COMPANION - LIVE TELEMETRY MONITOR             " -ForegroundColor White -BackgroundColor DarkBlue
        Write-Host "======================================================================" -ForegroundColor Cyan

        # RF Link Row
        $linkCol = if ($rf.link -eq "OK") { "Green" } else { "Red" }
        Write-Host " [RF LINK] " -NoNewline -ForegroundColor Yellow
        Write-Host "STATUS: $($rf.link) " -NoNewline -ForegroundColor $linkCol
        Write-Host "| Packets: $($rf.rx) | Drops: $($rf.drop) | Gaps: $($rf.pkt_gap) " -NoNewline -ForegroundColor Gray
        if ($clock -and $clock.mode -eq "simulated" -and $clock.unix_s -gt 0) {
            $clockTime = [DateTimeOffset]::FromUnixTimeSeconds([int64]$clock.unix_s).ToLocalTime().ToString("yyyy-MM-dd HH:mm:ss")
            Write-Host "| Clock: $clockTime ($($clock.rate)x)" -ForegroundColor Green
        } else {
            Write-Host "| Clock: UNSET" -ForegroundColor Yellow
        }

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

        $mCol = if ($leds.morning) { "Green" } else { "DarkGray" }
        $aCol = if ($leds.afternoon) { "Green" } else { "DarkGray" }
        $nCol = if ($leds.night) { "Green" } else { "DarkGray" }
        $cCol = if ($leds.caretaker) { "Red" } else { "DarkGray" }
        $sCol = if ($leds.status) { "Cyan" } else { "DarkGray" }

        Write-Host "   Reminders:   Morning: " -NoNewline -ForegroundColor Gray
        Write-Host $mStr -NoNewline -ForegroundColor $mCol
        Write-Host " | Afternoon: " -NoNewline -ForegroundColor Gray
        Write-Host $aStr -NoNewline -ForegroundColor $aCol
        Write-Host " | Night: " -NoNewline -ForegroundColor Gray
        Write-Host $nStr -NoNewline -ForegroundColor $nCol
        Write-Host " | Caretaker: " -NoNewline -ForegroundColor Gray
        Write-Host $cStr -ForegroundColor $cCol

        Write-Host "   Path Lights: Washroom: " -NoNewline -ForegroundColor Gray
        Write-Host ("{0,3}/255" -f [int]$leds.path_wc) -NoNewline -ForegroundColor White
        Write-Host " | Kitchen: " -NoNewline -ForegroundColor Gray
        Write-Host ("{0,3}/255" -f [int]$leds.path_kit) -NoNewline -ForegroundColor White
        Write-Host " | Heartbeat LED: " -NoNewline -ForegroundColor Gray
        Write-Host $sStr -ForegroundColor $sCol

        Write-Host "======================================================================" -ForegroundColor Cyan
        Write-Host " Streaming live at 10 Hz from $Port. Press Ctrl+C to exit." -ForegroundColor DarkGray
    }
} finally {
    try { [Console]::CursorVisible = $true } catch {}
    if ($sp.IsOpen) {
        $sp.Close()
    }
    Write-Host "`nDisconnected from $Port." -ForegroundColor Yellow
}
