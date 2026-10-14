param(
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
    [string[]]$Inputs
)
$root = $PSScriptRoot
& "$root\start_dashboard.ps1" @Inputs
