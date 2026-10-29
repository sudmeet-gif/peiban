$ErrorActionPreference = 'Stop'
$dir = $PSScriptRoot
$manifestFile = Join-Path $dir 'release-manifest.json'
if (-not (Test-Path -LiteralPath $manifestFile -PathType Leaf)) { throw 'Put release-manifest.json and every part in the same folder.' }
$manifest = Get-Content -LiteralPath $manifestFile -Raw -Encoding UTF8 | ConvertFrom-Json
$out = Join-Path $dir 'peiban-install.zip'
if (Test-Path -LiteralPath $out) { throw 'peiban-install.zip already exists. Move or rename it first.' }
$temp = Join-Path $dir ('peiban-install-' + [Guid]::NewGuid().ToString('N') + '.tmp')
try {
    $stream = [IO.File]::Open($temp, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try {
        foreach ($part in $manifest.parts) {
            $name = [string]$part.name
            if ($name -notmatch '^peiban-install\.zip\.part\d{2}$') { throw 'Invalid part filename.' }
            $path = Join-Path $dir $name
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing part: $name" }
            if ((Get-Item -LiteralPath $path).Length -ne [long]$part.size) { throw "Incorrect part size: $name" }
            Write-Host "Checking and joining $name ..."
            if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $part.sha256.ToLowerInvariant()) { throw "Part checksum mismatch: $name" }
            $inputStream = [IO.File]::OpenRead($path)
            try { $inputStream.CopyTo($stream, 8388608) } finally { $inputStream.Dispose() }
        }
    } finally { $stream.Dispose() }
    if ((Get-Item -LiteralPath $temp).Length -ne [long]$manifest.size) { throw 'Incorrect assembled file size.' }
    Write-Host 'Checking the complete ZIP...'
    if ((Get-FileHash -LiteralPath $temp -Algorithm SHA256).Hash.ToLowerInvariant() -ne $manifest.sha256.ToLowerInvariant()) { throw 'Complete ZIP checksum mismatch.' }
    Move-Item -LiteralPath $temp -Destination $out
    Write-Host "Done: $out"
    Write-Host 'Extract peiban-install.zip, then run PeibanSetup.exe.'
} finally {
    if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Force }
}
