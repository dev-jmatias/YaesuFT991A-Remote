<#
  Writes the first-boot settings (hostname, user, password, SSH, Wi-Fi) onto a freshly flashed Radio Remote card.

  Why: the newer Raspberry Pi Imager (2.x) offers its "Edit settings" step only for official Raspberry Pi OS images, not for
  "Use custom" images. The image reads cloud-init files from its boot partition, so this script writes them for you.

  1. Flash the image with Raspberry Pi Imager ("Use custom"). Do not remove the card yet, or remove it and put it back so
     Windows shows the small "bootfs" drive.
  2. Run (Windows PowerShell):   pwsh image\first-boot-settings.ps1        (add -Drive E: if bootfs is not found by itself)
  3. Answer the questions, eject the card, put it in the Pi and power on.

  The password is typed hidden and only its hash (SHA-512 crypt, computed by this script: nothing else has to be installed) is
  written to the card. Nothing is sent anywhere. Needs PowerShell 7 (pwsh).
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
# SHA-512 crypt ("$6$...") password hash, computed here so that nothing else has to be installed (no OpenSSL / Git needed).
# It is the algorithm of glibc crypt(3) / "openssl passwd -6" (5000 rounds); the password bytes are exactly what you typed (UTF-8).
if (-not ("RrSha512Crypt" -as [type])) {
  Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Security.Cryptography;
using System.Text;
public static class RrSha512Crypt {
  const string Alphabet = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz";
  static byte[] H(byte[] d) { using (var h = SHA512.Create()) { return h.ComputeHash(d); } }
  static void B64(StringBuilder sb, int b2, int b1, int b0, int n) {
    int w = (b2 << 16) | (b1 << 8) | b0;
    for (int i = 0; i < n; i++) { sb.Append(Alphabet[w & 0x3f]); w >>= 6; }
  }
  public static string RandomSalt() {
    var r = new byte[16]; using (var g = RandomNumberGenerator.Create()) { g.GetBytes(r); }
    var sb = new StringBuilder(); foreach (var b in r) sb.Append(Alphabet[b & 0x3f]); return sb.ToString();
  }
  public static string Hash(string password, string salt) {
    byte[] pw = new UTF8Encoding(false).GetBytes(password);
    byte[] sl = Encoding.ASCII.GetBytes(salt);
    var t = new List<byte>(); t.AddRange(pw); t.AddRange(sl); t.AddRange(pw);
    byte[] b = H(t.ToArray());
    var a = new List<byte>(); a.AddRange(pw); a.AddRange(sl);
    int cnt;
    for (cnt = pw.Length; cnt > 64; cnt -= 64) a.AddRange(b);
    for (int i = 0; i < cnt; i++) a.Add(b[i]);
    for (cnt = pw.Length; cnt > 0; cnt >>= 1) { if ((cnt & 1) != 0) a.AddRange(b); else a.AddRange(pw); }
    byte[] da = H(a.ToArray());
    var dpIn = new List<byte>(); for (int i = 0; i < pw.Length; i++) dpIn.AddRange(pw);
    byte[] dp = H(dpIn.ToArray());
    byte[] p = new byte[pw.Length]; for (int i = 0; i < p.Length; i++) p[i] = dp[i % 64];
    var dsIn = new List<byte>(); for (int i = 0; i < 16 + da[0]; i++) dsIn.AddRange(sl);
    byte[] ds = H(dsIn.ToArray());
    byte[] s = new byte[sl.Length]; for (int i = 0; i < s.Length; i++) s[i] = ds[i % 64];
    byte[] c = da;
    for (int i = 0; i < 5000; i++) {
      var ctx = new List<byte>();
      if ((i & 1) != 0) ctx.AddRange(p); else ctx.AddRange(c);
      if (i % 3 != 0) ctx.AddRange(s);
      if (i % 7 != 0) ctx.AddRange(p);
      if ((i & 1) != 0) ctx.AddRange(c); else ctx.AddRange(p);
      c = H(ctx.ToArray());
    }
    int[,] order = { {0,21,42},{22,43,1},{44,2,23},{3,24,45},{25,46,4},{47,5,26},{6,27,48},{28,49,7},{50,8,29},{9,30,51},
                     {31,52,10},{53,11,32},{12,33,54},{34,55,13},{56,14,35},{15,36,57},{37,58,16},{59,17,38},{18,39,60},
                     {40,61,19},{62,20,41} };
    var sb = new StringBuilder("$6$" + salt + "$");
    for (int i = 0; i < 21; i++) B64(sb, c[order[i,0]], c[order[i,1]], c[order[i,2]], 4);
    B64(sb, 0, 0, c[63], 2);
    return sb.ToString();
  }
}
'@
}
function New-PasswordHash($password, $salt = "") {
  if (-not $salt) { $salt = [RrSha512Crypt]::RandomSalt() }
  [RrSha512Crypt]::Hash($password, $salt)
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
  $PasswordHash = New-PasswordHash $p1
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
# A fresh instance id, so cloud-init treats the next boot as a first boot. NOTE: tested on a real Pi: writing the settings
# onto a card that has ALREADY booted did not re-run the setup (the SSH host key stayed the same). Run this script on a freshly
# flashed card, before its first boot; to change the settings later, re-flash.
[IO.File]::WriteAllText($target + "meta-data", "instance_id: rr-$(Get-Date -Format 'yyyyMMddHHmmss')`n", $enc)
[IO.File]::WriteAllText($target + "user-data", $userData.Replace("`r`n", "`n") + "`n", $enc)
if ($net) { [IO.File]::WriteAllText($target + "network-config", $net.Replace("`r`n", "`n") + "`n", $enc) }

Write-Host ""
Write-Host "Written to ${target}:  user-data$(if ($net) { '  network-config' })"
Write-Host "Eject the card, put it in the Pi and power on. After about two minutes open  https://$HostName.local"
Write-Host "(or:  ssh $UserName@$HostName.local ). If it does not appear, connect a monitor to the Pi to see what it does at boot."
