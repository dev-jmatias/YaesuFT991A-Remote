<#
  Writes the first-boot settings (hostname, user, password, SSH, Wi-Fi) onto a freshly flashed Radio Remote card.

  Why: the newer Raspberry Pi Imager (2.x) offers its "Edit settings" step only for official Raspberry Pi OS images, not for
  "Use custom" images. The image reads cloud-init files from its boot partition, so this script writes them for you.

  1. Flash the image with Raspberry Pi Imager ("Use custom"). Do not remove the card yet, or remove it and put it back so
     Windows shows the small "bootfs" drive.
  2. Run (Windows PowerShell):   pwsh image\first-boot-settings.ps1        (add -Drive E: if bootfs is not found by itself)
  3. Answer the questions, eject the card, put it in the Pi and power on.

  The password is typed hidden and only its hash (SHA-512 crypt, made with the OpenSSL that comes with Git for Windows) is
  written to the card. Nothing is sent anywhere.
#>
param(
  [string]$Drive,                 # for example E:   (default: the volume labelled bootfs)
  [string]$OutDir,                # write the files here instead (for testing)
  [string]$HostName,
  [string]$UserName,
  [string]$PasswordHash           # for testing: skip the password prompt
)
$ErrorActionPreference = "Stop"

function Ask($prompt, $default = "") {
  $a = Read-Host ($(if ($default) { "$prompt [$default]" } else { $prompt }))
  if ([string]::IsNullOrWhiteSpace($a)) { $default } else { $a.Trim() }
}
# SHA-512 crypt hash of the password. The password goes to openssl's stdin as exact bytes WITHOUT a trailing newline:
# piping a string from PowerShell appends "\r\n", which would silently become part of the password.
function New-PasswordHash($password, $openssl, $salt = "") {
  $psi = New-Object Diagnostics.ProcessStartInfo
  $psi.FileName = $openssl
  foreach ($a in @("passwd", "-6")) { $psi.ArgumentList.Add($a) }
  if ($salt) { $psi.ArgumentList.Add("-salt"); $psi.ArgumentList.Add($salt) }
  $psi.ArgumentList.Add("-stdin")
  $psi.RedirectStandardInput = $true; $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
  $psi.UseShellExecute = $false
  $p = [Diagnostics.Process]::Start($psi)
  $bytes = (New-Object Text.UTF8Encoding($false)).GetBytes($password)
  $p.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length)
  $p.StandardInput.BaseStream.Flush()
  $p.StandardInput.Close()
  $out = $p.StandardOutput.ReadToEnd().Trim()
  $p.WaitForExit()
  if ($p.ExitCode -ne 0 -or $out -notmatch '^\$6\$') { throw "could not create the password hash: $($p.StandardError.ReadToEnd())" }
  $out
}
function YamlQuote($s) { '"' + ($s -replace '\\', '\\' -replace '"', '\"') + '"' }

# ---- where to write
if ($OutDir) { New-Item -ItemType Directory -Force $OutDir | Out-Null; $target = ([IO.Path]::GetFullPath($OutDir)).TrimEnd('\') + "\" }
else {
  if ($Drive) { $target = $Drive.TrimEnd('\') + "\" }
  else {
    # the volume labelled bootfs; works even when Windows gave it no drive letter (then its \\?\Volume{...}\ path is used)
    $v = Get-Volume | Where-Object FileSystemLabel -eq "bootfs" | Select-Object -First 1
    if (-not $v) { throw "The 'bootfs' volume was not found. Re-insert the card after flashing, or pass -Drive E:" }
    $target = if ($v.DriveLetter) { "$($v.DriveLetter):\" } else { $v.Path }
  }
  # .NET calls only: PowerShell's Test-Path/Join-Path do not cope with \\?\Volume{...} paths
  if (-not [IO.File]::Exists($target + "cmdline.txt")) { throw "$target does not look like the Raspberry Pi boot partition (no cmdline.txt)" }
}

# ---- questions
if (-not $HostName) { $HostName = Ask "Hostname (the address will be https://<hostname>.local)" "radio" }
if ($HostName -notmatch '^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$') { throw "invalid hostname '$HostName' (letters, digits and '-')" }
if (-not $UserName) { $UserName = Ask "User name" "pi" }
if ($UserName -notmatch '^[a-z_][a-z0-9_-]{0,31}$') { throw "invalid user name '$UserName' (lowercase letters, digits, '_' and '-')" }

if (-not $PasswordHash) {
  $sec1 = Read-Host "Password for $UserName" -AsSecureString
  $sec2 = Read-Host "Repeat the password" -AsSecureString
  $p1 = [Net.NetworkCredential]::new("", $sec1).Password
  $p2 = [Net.NetworkCredential]::new("", $sec2).Password
  if ($p1 -ne $p2) { throw "the passwords do not match" }
  if ($p1.Length -lt 8) { throw "use at least 8 characters" }
  $openssl = @("C:\Program Files\Git\usr\bin\openssl.exe", "C:\Program Files (x86)\Git\usr\bin\openssl.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
  if (-not $openssl) { $c = Get-Command openssl -ErrorAction SilentlyContinue; if ($c) { $openssl = $c.Source } }
  if (-not $openssl) { throw "openssl not found (it ships with Git for Windows)" }
  $PasswordHash = New-PasswordHash $p1 $openssl
  $p1 = $p2 = $null
  if ($PasswordHash -notmatch '^\$6\$') { throw "could not create the password hash" }
}

$wifi = (Ask "Wi-Fi network name (empty = use the network cable)").Trim()
$wifiPass = ""; $country = ""
if ($wifi) {
  $wifiPass = [Net.NetworkCredential]::new("", (Read-Host "Wi-Fi password" -AsSecureString)).Password
  $country = (Ask "Wi-Fi country code (two letters, for example GB, US, ES)" "GB").ToUpper()
  if ($country -notmatch '^[A-Z]{2}$') { throw "the country code must be two letters" }
}
$tz = Ask "Time zone" "Europe/London"

# ---- cloud-init files (LF line endings, no BOM)
$userData = @"
#cloud-config
# written by image/first-boot-settings.ps1
hostname: $HostName
manage_etc_hosts: true
packages:
  - avahi-daemon
apt:
  conf: |
    Acquire { Check-Date "false"; };
users:
  - name: $UserName
    groups: users,adm,dialout,audio,netdev,video,plugdev,cdrom,games,input,gpio,spi,i2c,render,sudo
    shell: /bin/bash
    lock_passwd: false
    passwd: $PasswordHash
    sudo: ALL=(ALL) NOPASSWD:ALL
chpasswd:
  expire: false
  users:
    - name: $UserName
      password: $PasswordHash
      type: hash
enable_ssh: true
ssh_pwauth: true
timezone: $tz
"@
$net = $null
if ($wifi) {
  $net = @"
network:
  version: 2
  wifis:
    renderer: NetworkManager
    wlan0:
      dhcp4: true
      optional: true
      regulatory-domain: $country
      access-points:
        $(YamlQuote $wifi):
          password: $(YamlQuote $wifiPass)
"@
}
$enc = New-Object Text.UTF8Encoding($false)
# a new instance id makes cloud-init treat the next boot as a first boot again, so running this script on a card that has
# already booted (to fix the password, hostname or Wi-Fi) takes effect without re-flashing. Data in /var/lib/radio-remote
# (the admin account and settings of the web app) is kept; the SSH host keys are regenerated.
[IO.File]::WriteAllText($target + "meta-data", "instance_id: rr-$(Get-Date -Format 'yyyyMMddHHmmss')`n", $enc)
[IO.File]::WriteAllText($target + "user-data", $userData.Replace("`r`n", "`n") + "`n", $enc)
if ($net) { [IO.File]::WriteAllText($target + "network-config", $net.Replace("`r`n", "`n") + "`n", $enc) }

Write-Host ""
Write-Host "Written to ${target}:  user-data$(if ($net) { '  network-config' })"
Write-Host "Eject the card, put it in the Pi and power on. After about two minutes open  https://$HostName.local"
Write-Host "(or:  ssh $UserName@$HostName.local ). If it does not appear, connect a monitor to the Pi to see what it does at boot."
