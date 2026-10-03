# Remote access with Tailscale

Tailscale gives your devices a private network to the Pi without opening any port to the Internet. It is the
recommended way to operate away from home. The app's own logins stay on: Tailscale is the outer wall, not a replacement.

**Never port-forward the Pi.** Never bind the app to `0.0.0.0`.

## Setup

1. Install Tailscale on the Pi following Tailscale's official instructions for Raspberry Pi OS (tailscale.com/download), then `sudo tailscale up`.
2. Install Tailscale on your phone/tablet/PC and sign in to the same account.
3. In the Tailscale admin console: enable **MagicDNS** and **HTTPS certificates**, and note the Pi's name
   (`<pi>.<tailnet>.ts.net`). `tailscale status` on the Pi shows it.
4. HTTPS front end (the microphone needs HTTPS). Use the Caddy option in
   [`config/Caddyfile.example`](../config/Caddyfile.example) ("Option B"), replacing the hostname with the Pi's
   `.ts.net` name, then `sudo systemctl reload caddy`. Caddy forwards `X-Forwarded-Proto`, so cookies get the `Secure` flag.
   **Requires verification** on your install: Caddy's `get_certificate tailscale` needs permission to talk to `tailscaled`
   (see Caddy's Tailscale documentation).
5. Open `https://<pi>.<tailnet>.ts.net` from any device on your tailnet.

Alternative: `sudo tailscale serve --bg --https=443 http://127.0.0.1:8080`. **Requires verification:** whether your
Tailscale version forwards `X-Forwarded-Proto: https`; without it the session cookie lacks `Secure`. Check in the browser
dev tools; prefer the Caddy route if it is missing.

## Audio over Tailscale

WebRTC audio uses UDP. On a **direct** tailnet path it works like the LAN. On a **relayed** (DERP) path expect extra delay or
failure. Check with `tailscale ping <pi>` (it says "direct" or "via DERP"). A WebSocket audio fallback is a planned revision.

## Hardening checklist

- Tailscale ACLs: allow only your own devices/users to reach the Pi on port 443.
- Keep the Pi's firewall rules from [06-security-remote.md](06-security-remote.md).
- Use strong, unique passwords; give family/guests `viewer` accounts.
- Optional: Tailscale "key expiry" and device approval so a lost phone can be cut off.
