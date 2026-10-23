$source = (Get-Item '.').FullName
$destinations = @(
    (Join-Path $source 'StockPulse_Phase4_Production.zip'),
    (Join-Path (Get-Item '..').FullName 'StockPulse_Phase4_Production.zip'),
    (Join-Path (Get-Item '..').FullName 'StockPulse_Client_Delivery.zip')
)
foreach ($destination in $destinations) {
    if (Test-Path $destination) { Remove-Item -Force $destination }
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
$excludeList = @('.venv', '.git', '__pycache__', '.pytest_cache', 'scratch')
$filesToZip = Get-ChildItem -Path $source -Recurse | Where-Object { 
    $item = $_
    if ($item.Name -eq '.env' -or $item.Extension -eq '.zip' -or $item.Extension -eq '.db' -or $item.Name -like '*.db*') { return $false }
    -not ($excludeList | Where-Object { $item.FullName -like ('*' + [System.IO.Path]::DirectorySeparatorChar + $_ + '*') }) 
}
$tempDir = Join-Path $env:TEMP ('stockpulse_pack_' + (Get-Random))
New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
try {
    foreach ($f in $filesToZip) {
        $relPath = $f.FullName.Substring($source.Length).TrimStart([System.IO.Path]::DirectorySeparatorChar)
        $target = Join-Path $tempDir $relPath
        if ($f.PSIsContainer) { 
            New-Item -ItemType Directory -Path $target -Force | Out-Null 
        } else { 
            $dir = [System.IO.Path]::GetDirectoryName($target)
            if (!(Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
            Copy-Item -LiteralPath $f.FullName -Destination $target -Force
        }
    }
    foreach ($destination in $destinations) {
        [System.IO.Compression.ZipFile]::CreateFromDirectory($tempDir, $destination)
        Write-Host "Created: $destination ($([math]::Round((Get-Item $destination).Length / 1MB, 2)) MB)" -ForegroundColor Green
    }
} finally {
    Remove-Item -Recurse -Force $tempDir -ErrorAction SilentlyContinue
}
