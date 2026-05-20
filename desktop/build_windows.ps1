param(
    [string]$Version = "dev"
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Name = "IfcGref-Desktop"
$DistDir = Join-Path $Root "desktop_dist"
$WorkDir = Join-Path $Root "build\desktop"
$ZipPath = Join-Path $DistDir "$Name-Windows-$Version.zip"
$EnvDataArgs = @()
if (Test-Path (Join-Path $Root ".env")) {
    $EnvDataArgs = @("--add-data", ".env;.")
}

Set-Location $Root
New-Item -ItemType Directory -Force -Path $DistDir | Out-Null

python -m pip install -r requirements.txt
python -m pip install pyinstaller

if (Test-Path (Join-Path $Root "dist\$Name")) {
    Remove-Item -LiteralPath (Join-Path $Root "dist\$Name") -Recurse -Force
}
if (Test-Path $WorkDir) {
    Remove-Item -LiteralPath $WorkDir -Recurse -Force
}

python -m PyInstaller `
    --noconfirm `
    --clean `
    --name $Name `
    --distpath (Join-Path $Root "dist") `
    --workpath $WorkDir `
    --add-data "templates;templates" `
    --add-data "static;static" `
    --add-data "georeference_ifc;georeference_ifc" `
    @EnvDataArgs `
    --collect-all ifcopenshell `
    --collect-all pyproj `
    --collect-all scipy `
    --collect-all pandas `
    --hidden-import ifcopenshell.geom `
    --hidden-import ifcopenshell.util.element `
    --hidden-import ifcopenshell.util.placement `
    desktop_app.py

if (Test-Path $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}

Compress-Archive -Path (Join-Path $Root "dist\$Name\*") -DestinationPath $ZipPath
Write-Host "Desktop app package created: $ZipPath"
