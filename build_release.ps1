$ErrorActionPreference = "Stop"

$project = (Resolve-Path $PSScriptRoot).Path
$python = Join-Path $project ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "找不到项目虚拟环境: $python"
}

foreach ($name in @("build", "dist")) {
    $target = Join-Path $project $name
    if (Test-Path -LiteralPath $target) {
        $resolved = (Resolve-Path $target).Path
        if (-not $resolved.StartsWith($project + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "拒绝清理未验证路径: $resolved"
        }
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}

# Keep external ICU/Qt installations out of PyInstaller's dependency search.
$originalPath = $env:PATH
try {
    $env:PATH = "C:\Windows\System32;C:\Windows"
    & $python -m PyInstaller --noconfirm --clean "MinecraftPlayerMigrator.spec"
}
finally {
    $env:PATH = $originalPath
}
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

# Keep the user-facing instructions beside the EXE; PyInstaller stores runtime
# data under _internal in recent onedir layouts.
Copy-Item -LiteralPath (Join-Path $project "README.txt") -Destination (Join-Path $project "dist\MinecraftPlayerMigrator\README.txt") -Force

$release = Join-Path $project "MinecraftPlayerMigrator-v1.1.0-win-x64.zip"
if (Test-Path -LiteralPath $release) {
    Remove-Item -LiteralPath $release -Force
}
Compress-Archive -Path (Join-Path $project "dist\MinecraftPlayerMigrator") -DestinationPath $release -CompressionLevel Optimal

$releaseDir = Join-Path $project "dist\MinecraftPlayerMigrator"
$qtNames = @("Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll")
foreach ($qtName in $qtNames) {
    $qtMatches = @(Get-ChildItem -LiteralPath $releaseDir -Recurse -File -Filter $qtName)
    if ($qtMatches.Count -ne 1) {
        throw "Qt DLL 检查失败: $qtName 数量为 $($qtMatches.Count)"
    }
    Write-Host "Qt DLL: $($qtMatches[0].FullName)"
}
$icuMatches = @(Get-ChildItem -LiteralPath $releaseDir -Recurse -File | Where-Object {
    $_.Name -match '^(?i:icuuc|icuin|icudt.*)\.dll$'
})
if ($icuMatches.Count -gt 0) {
    throw "检测到不应进入发布目录的 ICU DLL: $($icuMatches.FullName -join ', ')"
}

$checksums = Join-Path $project "SHA256SUMS.txt"
$exe = Join-Path $releaseDir "MinecraftPlayerMigrator.exe"
@(
    "$( (Get-FileHash -LiteralPath $release -Algorithm SHA256).Hash.ToLowerInvariant() )  MinecraftPlayerMigrator-v1.1.0-win-x64.zip"
    "$( (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant() )  MinecraftPlayerMigrator.exe"
) | Set-Content -LiteralPath $checksums -Encoding ascii
Write-Host "发布包: $release"
Write-Host "校验文件: $checksums"
