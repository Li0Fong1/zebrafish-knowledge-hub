# UTF-8 查看项目日志（PowerShell 专用，避免 Get-Content 默认 GBK 乱码）
param(
    [Parameter(Position = 0)]
    [string]$Path = "logs/overnight_supervisor.log",
    [int]$Tail = 20,
    [switch]$Wait
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

$presets = @{
    "15" = "logs/overnight_15_link.log"
    "18" = "logs/overnight_18_wos.log"
    "19" = "logs/overnight_19_go.log"
    "supervisor" = "logs/overnight_supervisor.log"
}

if ($presets.ContainsKey($Path.ToLower())) {
    $Path = $presets[$Path.ToLower()]
}

$full = Join-Path $root $Path
if (-not (Test-Path $full)) {
    Write-Host "[提示] 日志不存在: $full"
    exit 1
}

$params = @{ Path = $full; Encoding = "utf8"; Tail = $Tail }
if ($Wait) { $params["Wait"] = $true }
Get-Content @params
