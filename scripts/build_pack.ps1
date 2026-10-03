<#
  Build the installer pack and the documentation pack (run on the development PC, Windows PowerShell 7).

    pwsh scripts\build_pack.ps1            # tests, docs, wheels, zip
    pwsh scripts\build_pack.ps1 -SkipTests -SkipWheels

  Output (in .\dist):
    radio-remote-installer-<version>.zip   the folder to copy to the Pi (install-everything.sh, program, wheels, manual)
    radio-remote-docs-<version>.zip        the manual only (HTML + PDF)
#>
param([switch]$SkipTests, [switch]$SkipWheels)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "create the development venv first (see README: Develop)" }
$version = (Select-String -Path "backend\radio_remote\__init__.py" -Pattern '__version__\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
$dist = Join-Path $root "dist"
$stage = Join-Path $dist "radio-remote-installer"
Write-Host "== Radio Remote $version"

if (-not $SkipTests) {
  Write-Host "== tests"
  $env:PYTHONPATH = "backend"
  & $py -m pytest -q
  if ($LASTEXITCODE -ne 0) { throw "tests failed" }
}

Write-Host "== documentation"
& $py -m pip install --quiet markdown
& $py scripts\build_docs.py --pdf
if ($LASTEXITCODE -ne 0) { throw "documentation build failed" }

if (Test-Path $stage) { [IO.Directory]::Delete($stage, $true) }
New-Item -ItemType Directory -Force $stage | Out-Null

Write-Host "== program"
$app = Join-Path $stage "radio-remote"
New-Item -ItemType Directory -Force $app | Out-Null
foreach ($item in "backend", "frontend", "scripts", "packaging", "config", "docs", "docs-html", "requirements.txt", "install.sh", "update.sh", "README.md", "pyproject.toml") {
  Copy-Item -Recurse -Force $item (Join-Path $app $item)
}
Get-ChildItem $app -Recurse -Directory -Filter "__pycache__" | ForEach-Object { [IO.Directory]::Delete($_.FullName, $true) }
foreach ($junk in "config\radio-remote.toml", "config\none.toml") { $p = Join-Path $app $junk; if (Test-Path $p) { [IO.File]::Delete($p) } }

Write-Host "== installer files"
Copy-Item installer\install-everything.sh $stage
Copy-Item installer\START-HERE.txt $stage
# the shell scripts must have LF line endings (a CR breaks them on the Pi)
Get-ChildItem $stage -Recurse -Include *.sh, START-HERE.txt | ForEach-Object {
  $t = [IO.File]::ReadAllText($_.FullName).Replace("`r`n", "`n")
  [IO.File]::WriteAllText($_.FullName, $t, (New-Object Text.UTF8Encoding($false)))
}

Write-Host "== manual"
Copy-Item -Recurse -Force docs-html (Join-Path $stage "docs")

if (-not $SkipWheels) {
  Write-Host "== Python libraries for the Pi (64-bit, Python 3.11 and 3.13)"
  $wh = Join-Path $stage "wheels"
  New-Item -ItemType Directory -Force $wh | Out-Null
  foreach ($v in @(@("3.11", "cp311"), @("3.13", "cp313"))) {
    & $py -m pip download -r requirements.txt -d $wh --only-binary=:all: `
      --platform manylinux_2_28_aarch64 --platform manylinux_2_17_aarch64 --platform manylinux2014_aarch64 --platform linux_aarch64 `
      --python-version $v[0] --implementation cp --abi $v[1] --quiet
    if ($LASTEXITCODE -ne 0) { throw "could not download the wheels for Python $($v[0])" }
  }
}

Write-Host "== zip"
$zip = Join-Path $dist "radio-remote-installer-$version.zip"
if (Test-Path $zip) { [IO.File]::Delete($zip) }
Compress-Archive -Path $stage -DestinationPath $zip -CompressionLevel Optimal
$dz = Join-Path $dist "radio-remote-docs-$version.zip"
if (Test-Path $dz) { [IO.File]::Delete($dz) }
Compress-Archive -Path (Join-Path $root "docs-html\*") -DestinationPath $dz -CompressionLevel Optimal
Get-ChildItem $dist -Filter *.zip | ForEach-Object { "{0,-44} {1,8:N1} MB  sha256 {2}" -f $_.Name, ($_.Length / 1MB), (Get-FileHash $_.FullName).Hash.Substring(0, 16) }
Write-Host "Done. Copy 'radio-remote-installer' (unzipped) to the Pi and run: sudo bash install-everything.sh"
