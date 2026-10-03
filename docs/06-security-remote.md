# Security and remote operation (Phase 8)

The system can transmit RF on your licence, so security has two jobs: keep strangers out, and make sure a bug,
a flaky network or a second user can never leave the transmitter keyed or in the wrong hands.

## Who can do what

| Role | Watch state, listen | Take control, change radio, PTT, mic audio | Users, config, diagnostics, power off |
|---|---|---|---|
| viewer | yes | no | no |
| operator | yes | yes (when holding control) | no |
| admin | yes | yes | yes |

### Control lease (many watch, one controls)

- Only the connection that holds the lease may change the radio, key PTT, or have its mic audio reach the radio.
  Everything else is rejected **on the server**, whatever the browser does.
- The first operator/admin to connect to a free lease gets it. Others see who has control and can **Request control**.
- Different user asks: the holder gets a Hand over / Keep control prompt. No answer within `safety.control_request_timeout_s`
  (default 10 s, 3-120): control passes automatically, so an unattended session never blocks anyone.
- **Trusted users** (set by an admin in Admin > Users, "Trusted"): their request is granted **immediately, with no prompt**.
  The previous holder is told who took over. Trust applies to operators/admins only (a viewer can never hold control), it
  takes effect on the user's next request without a reconnect, and revoking it is equally immediate. Changing the flag does
  not sign the user out.
- Same user on another device: immediate hand-over.
- **Never during transmission.** A request while the transmitter is keyed is refused (an unanswered request waits
  until PTT is released). When control moves for any reason, the previous holder is un-keyed first.
- Admin **Force** takes control at once and un-keys first.
- A disconnect frees the lease and un-keys.

## What is enforced where

| Concern | Measure | Where |
|---|---|---|
| Passwords | scrypt (N=2^14), min 10 chars, constant-time verify, dummy hash for unknown users | `auth.py` |
| Brute force | per address+user back-off (4 failures, doubling up to 5 min); address taken from `X-Forwarded-For` only when the peer is the local proxy | `auth.py`, `common.py` |
| Sessions | random 256-bit id, only a hash is stored, `HttpOnly; SameSite=Strict`, `Secure` behind HTTPS, 12 h idle / 7 d absolute, revoked on password change, role change, deletion | `auth.py`, `admin.py` |
| CSRF | `SameSite=Strict` plus a per-session token header on every state-changing request | `app.py` |
| WebSocket | session cookie, `Origin` must match `Host` (or `allowed_origins`), session re-checked on every message, 4 KB message cap, max 24 clients | `app.py` |
| Command flood | token bucket 40/s (burst 80) per connection; heartbeats exempt; persistent abuse closes the socket | `ratelimit.py` |
| Input | every command is a typed intent validated against the radio's capabilities; no raw CAT, no shell, no paths from the browser | `app.py`, `radio/controls.py` |
| PTT | allow flag (off by default; an administrator turns it on with a password re-check, audited); single owner; heartbeat; hard time limit; un-key on disconnect / lease change / shutdown; retried until acknowledged | `safety.py`, `lease.py`, `admin.py` |
| Web-editable config | an allow-list. `server.*` and `storage.*` can only be changed by editing the file on the Pi; `safety.allow_ptt` only through its own endpoint (administrator, password re-entered, throttled, audited) | `admin.py` |
| Headers | CSP (no inline script/style), `X-Frame-Options: DENY`, `nosniff`, `Permissions-Policy` (mic for self only), HSTS over HTTPS, `no-store` on API | `app.py` |
| Logs | passwords, cookies, CSRF tokens and `rr_session` values are scrubbed before any handler; the audit table records logins, failures, control changes, PTT, user/config changes, power-off, restarts | `logs.py`, `auth.py` |
| Last-resort recovery | `python -m radio_remote.cli reset-password USER` on the Pi (needs filesystem access, prompts for the password) | `cli.py` |

Not done, deliberately or for now:

- **No per-session IP binding**, no 2FA, no password-expiry or complexity rules beyond length.
- **No WebSocket one-time ticket**: `SameSite=Strict` plus the Origin check cover cross-site hijacking.
- **The audit log is not tamper-evident** (an admin can read it; someone with file access can edit it).
- **TLS is not terminated by the app.** Use the proxy below.

## Network layout

```
Internet  X  (never port-forward this)
LAN / Tailscale ---> Caddy :443 (TLS) ---> app 127.0.0.1:8080 ---> radio (USB)
```

- Keep `server.host = "127.0.0.1"`. The app trusts `X-Forwarded-*` headers only from a loopback peer; binding it to
  `0.0.0.0` behind a proxy would let anyone on the LAN spoof their address and "Secure" status.
- **Why HTTPS matters:** browsers only expose the microphone on HTTPS (or localhost). Without it you can listen but not transmit audio.

### Recipe A - LAN with Caddy's internal CA

1. `sudo apt install caddy`, copy `config/Caddyfile.example` to `/etc/caddy/Caddyfile` (Option A), `sudo systemctl reload caddy`.
2. Each phone/tablet/PC must trust Caddy's root certificate once. The root is at
   `/var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt` on the Pi (the path can differ between Caddy installs; the
   installer pack and the image also publish it for download). iOS additionally needs it enabled under Settings > General > About >
   Certificate Trust Settings.
3. Browse to `https://raspberrypi.local`.

### Recipe B - Tailscale (preferred for remote use)

1. Install Tailscale on the Pi and on your devices, `sudo tailscale up`. Enable MagicDNS and HTTPS certificates in the admin console.
2. Run `sudo /opt/radio-remote/current/scripts/tailscale_setup.sh`: it adds the Tailscale name to Caddy (verified on a real Pi). Step by step in
   [tailscale](tailscale.md). `tailscale serve` is not used: Caddy forwards `X-Forwarded-Proto: https`, which the session cookie's `Secure` flag needs.
3. Keep application logins on. Tailscale is the outer wall, not a replacement for accounts.
4. WebRTC audio uses UDP; over a direct tailnet path it is fine, over a relayed (DERP) path latency rises. The UI will
   tell you if audio cannot connect; a WebSocket-audio fallback is not built yet.

### Firewall (optional, recommended)

```bash
sudo ufw default deny incoming
sudo ufw allow in on tailscale0
sudo ufw allow from 192.168.0.0/16 to any port 443 proto tcp   # your LAN range
sudo ufw allow from 192.168.0.0/16 to any port 22 proto tcp
sudo ufw enable
```

WebRTC audio selects ports dynamically; with `deny incoming` allow UDP from your LAN/tailnet interfaces as well
(not verified against a real firewall yet).

## Operating notes

- **First run**: the first visit creates the administrator. Do this on the LAN, not over a forwarded port.
- **Enable PTT**: Admin > Config > *Transmitting (PTT)* (administrator password required), or set `safety.allow_ptt = true` in
  the config file and restart. It is off by default and every change is in the audit log.
- **Lost admin password**: `python -m radio_remote.cli reset-password <user>` on the Pi.
- **Backup**: the config file and `data/radio-remote.db` (users, sessions, audit).
- **Radio-side backstop**: set the radio's own TX time-out (menu 036 on the FT-991A) as the last line of defence.

## What has been tested

Automated (222 tests, mock radio and simulated CAT): lease transfer / deny / timeout / force / refusal while keyed,
heartbeat and disconnect un-keying, role enforcement, rate limiting, user management and last-admin protection, session
revocation, config allow-list and validation, audit contents, secret scrubbing, HSTS / cookie flags, recovery CLI.
In a real browser: admin sheet (users, clients, config, diagnostics, audit, account), request dialog and hand-over between
two users, watching-mode lock-out of dial and PTT.

**Not tested:** TLS in front of the app (Caddy / Tailscale), real phones/tablets, behaviour behind a real firewall,
several simultaneous audio listeners on a Pi.
