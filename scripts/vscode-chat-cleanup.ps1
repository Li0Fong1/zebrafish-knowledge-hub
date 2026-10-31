<#
.SYNOPSIS
  清理 VS Code 工作区聊天记录缓存（修复 "Invalid string length" 报错）。

.DESCRIPTION
  背景：Copilot Chat 会话文件里内嵌了巨量 base64 图片，单个会话达 381 MB，
        VS Code 渲染面板时序列化字符串超过 V8 上限（约 512 MiB）→ RangeError: Invalid string length。
  动作：
    1. 校验 VS Code / Code - Insiders 进程已完全退出（否则文件被锁）。
    2. 把占用较大的会话 .jsonl 改名归档成 .bak（不删除）。
    3. 把 0 字节的空会话连同对应目录（GitHub.copilot-chat、chatEditingSessions 等）改名归档。
    4. 写入清单 manifest.json，含每个文件的原始大小 / 移动后路径 / 回滚命令。
    5. -Restore 可一键还原。

.PARAMETER Restore
  按 manifest.json 还原所有改名过的文件与目录。

.EXAMPLE
  pwsh -NoProfile -File .\vscode-chat-cleanup.ps1
  pwsh -NoProfile -File .\vscode-chat-cleanup.ps1 -Restore
#>
[CmdletBinding()]
param(
    [switch]$Restore,
    [string]$WorkspaceStorageRoot = "$env:APPDATA\Code\User\workspaceStorage",
    [string]$WorkspaceHash = '9c9f13b7383c8002232fe6e138f81148',
    [int]$ArchiveThresholdMB = 5
)

$ErrorActionPreference = 'Stop'

$wsDir       = Join-Path $WorkspaceStorageRoot $WorkspaceHash
$chatDir     = Join-Path $wsDir 'chatSessions'
$stamp       = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupRoot  = Join-Path $wsDir "_dsh_chat_backup_$stamp"
$manifestPath = if ($Restore) { $null } else { Join-Path $backupRoot 'manifest.json' }

function Assert-VsCodeClosed {
    $procs = @(Get-Process -Name 'Code', 'Code - Insiders' -ErrorAction SilentlyContinue)
    if ($procs.Count -gt 0) {
        $detail = ($procs | ForEach-Object { "PID $($_.Id) ($([math]::Round($_.WorkingSet64/1MB)) MB)" }) -join ', '
        throw "VS Code 仍在运行：$detail。请完全退出（含托盘图标）后再执行。"
    }
    Write-Host "[OK] VS Code 进程已全部退出" -ForegroundColor Green
}

function Move-Aside {
    param([System.IO.FileSystemInfo]$Item, [string]$Kind)
    $relative = $Item.FullName.Substring($wsDir.Length).TrimStart('\', '/')
    $dest     = Join-Path $backupRoot $relative
    $destDir  = Split-Path $dest -Parent
    if (-not (Test-Path $destDir)) { New-Item -ItemType Directory -Path $destDir -Force | Out-Null }

    $size = if ($Item -is [System.IO.FileInfo]) { $Item.Length } else { 0 }
    [void](Move-Item -LiteralPath $Item.FullName -Destination $dest -Force)

    $entry = [ordered]@{
        kind        = $Kind
        original    = $Item.FullName
        archivedAt  = $dest
        sizeBytes   = $size
        sizeMB      = [math]::Round($size / 1MB, 2)
        movedAt     = (Get-Date).ToString('o')
    }
    Write-Host ("  归档 {0,-8} {1,9:N2} MB  {2}" -f $Kind, ($size / 1MB), (Split-Path $Item.FullName -Leaf)) -ForegroundColor DarkGray
    return $entry
}

# ---------------------------------------------------------------- Restore 模式
if ($Restore) {
    Assert-VsCodeClosed
    $candidate = Get-ChildItem -LiteralPath $wsDir -Directory -Filter '_dsh_chat_backup_*' |
                 Sort-Object Name -Descending | Select-Object -First 1
    if (-not $candidate) { throw "在 $wsDir 下找不到任何 _dsh_chat_backup_* 备份目录。" }
    $mf = Join-Path $candidate.FullName 'manifest.json'
    if (-not (Test-Path $mf)) { throw "备份目录 $($candidate.Name) 里没有 manifest.json。" }

    $data = Get-Content -LiteralPath $mf -Raw -Encoding UTF8 | ConvertFrom-Json
    Write-Host "从 $($candidate.Name) 还原 $($data.items.Count) 项..." -ForegroundColor Cyan
    # 目录后于文件还原，避免建父目录失败
    $ordered = @($data.items | Sort-Object @{ Expression = { $_.kind -eq 'dir' } })
    $restored = 0
    foreach ($it in $ordered) {
        if (-not (Test-Path -LiteralPath $it.archivedAt)) {
            Write-Warning "备份中不存在，跳过：$($it.archivedAt)"
            continue
        }
        $parent = Split-Path $it.original -Parent
        if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
        [void](Move-Item -LiteralPath $it.archivedAt -Destination $it.original -Force)
        $restored++
        Write-Host ("  还原 {0}" -f $it.original) -ForegroundColor DarkGray
    }
    Write-Host "[完成] 还原 $restored 项。" -ForegroundColor Green
    return
}

# ---------------------------------------------------------------- 清理模式
Write-Host "工作区缓存：$wsDir" -ForegroundColor Cyan
if (-not (Test-Path $chatDir)) { throw "找不到聊天会话目录：$chatDir" }

Assert-VsCodeClosed

if (-not (Test-Path $backupRoot)) { New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null }

$items    = New-Object System.Collections.Generic.List[object]
$keepList = New-Object System.Collections.Generic.List[string]
$guidRe   = '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'

Write-Host "`n[1/3] 处理占用较大的会话（阈值 ${ArchiveThresholdMB} MB）..." -ForegroundColor Yellow
$sessionFiles = Get-ChildItem -LiteralPath $chatDir -File -Filter '*.jsonl' | Sort-Object Length -Descending
foreach ($f in $sessionFiles) {
    $mb = $f.Length / 1MB
    if ($mb -ge $ArchiveThresholdMB) {
        $items.Add((Move-Aside -Item $f -Kind 'big-session'))
    }
    else {
        Write-Host ("  保留 {0,9:N2} MB  {1}" -f $mb, $f.Name) -ForegroundColor Green
        $keepList.Add($f.FullName)
    }
}

Write-Host "`n[2/3] 处理空会话（0 字节，无任何消息）..." -ForegroundColor Yellow
$emptyIds = @()
foreach ($f in $sessionFiles) {
    if ($f.Length -eq 0 -and (Test-Path -LiteralPath $f.FullName)) {
        $id = [System.IO.Path]::GetFileNameWithoutExtension($f.Name)
        if ($id -match $guidRe) { $emptyIds += $id }
    }
}
if ($emptyIds.Count -eq 0) { Write-Host "  没有空会话。" -ForegroundColor DarkGray }

$siblingDirs = @('GitHub.copilot-chat', 'chatEditingSessions', 'ms-python.python')
foreach ($id in $emptyIds) {
    $f = Join-Path $chatDir "$id.jsonl"
    if (Test-Path -LiteralPath $f) {
        $items.Add((Move-Aside -Item (Get-Item -LiteralPath $f) -Kind 'empty-session'))
    }
    foreach ($sd in $siblingDirs) {
        $sub = @(
            (Join-Path (Join-Path $wsDir $sd) $id),
            (Join-Path (Join-Path (Join-Path $wsDir $sd) 'debug-logs') $id),
            (Join-Path (Join-Path (Join-Path $wsDir $sd) 'transcripts') "$id.jsonl")
        )
        foreach ($p in $sub) {
            if (Test-Path -LiteralPath $p) {
                $node = Get-Item -LiteralPath $p
                $kind = if ($node -is [System.IO.DirectoryInfo]) { 'dir' } else { 'file' }
                $items.Add((Move-Aside -Item $node -Kind $kind))
            }
        }
    }
}

# ---------------------------------------------------------------- 3. 校验
Write-Host "`n[3/3] 校验结果..." -ForegroundColor Yellow
$manifest = [ordered]@{
    generatedAt = (Get-Date).ToString('o')
    workspaceStorage = $wsDir
    workspaceHash    = $WorkspaceHash
    archiveRoot      = $backupRoot
    archivedCount    = $items.Count
    keptSessions     = $keepList
    items            = $items
    rollbackCommand  = "pwsh -NoProfile -File '$PSCommandPath' -Restore -WorkspaceStorageRoot '$WorkspaceStorageRoot' -WorkspaceHash '$WorkspaceHash'"
}
$manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $manifestPath -Encoding UTF8

# 校验：每一项原始路径已不存在、备份存在
$bad = @()
foreach ($it in $items) {
    if (Test-Path -LiteralPath $it.original) { $bad += "未移走：$($it.original)" }
    if (-not (Test-Path -LiteralPath $it.archivedAt)) { $bad += "备份缺失：$($it.archivedAt)" }
}

$remainMB = [math]::Round(((Get-ChildItem -LiteralPath $wsDir -Recurse -File -ErrorAction SilentlyContinue |
                            Measure-Object Length -Sum).Sum) / 1MB, 1)

Write-Host ""
Write-Host "================================" -ForegroundColor Cyan
Write-Host ("归档项 : {0} 个" -f $items.Count)
Write-Host ("备份到 : {0}" -f $backupRoot)
Write-Host ("清单   : {0}" -f $manifestPath)
Write-Host ("本工作区当前剩余: {0} MB" -f $remainMB)
if ($items.Count -gt 0) {
    $archivedMB = [math]::Round((($items | Measure-Object -Property sizeBytes -Sum).Sum) / 1MB, 1)
    Write-Host ("已移出总量      : {0} MB" -f $archivedMB)
}
if ($bad.Count -gt 0) {
    Write-Host "[校验失败]" -ForegroundColor Red
    $bad | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
    exit 1
}
Write-Host "[校验通过] 所有归档项均已核验（原路径已空、备份存在）" -ForegroundColor Green
Write-Host ""
Write-Host "回滚命令：" -ForegroundColor Yellow
Write-Host "  $($manifest.rollbackCommand)" -ForegroundColor White
