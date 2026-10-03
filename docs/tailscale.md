# Remote access with Tailscale

Tailscale gives your devices a private network to the Pi without opening any port to the Internet. It is the
recommended way to operate away from home. The app's own logins stay on: Tailscale is the outer wall, not a replacement.

**Never port-forward the Pi.** Never bind the app to `0.0.0.0`.

## Setup

1. Install Tailscale on the Pi (the installer can do it: `--with-tailscale`; or follow tailscale.com/download), then sign in once:
   `sudo tailscale up` (it prints a link to open in a browser). The Pi now appears in your Tailscale account.
2. Install Tailscale on your phone/tablet/PC and sign in to the same account.
3. In the Tailscale admin console (login.tailscale.com/admin/dns) switch on **MagicDNS** and **HTTPS Certificates**.
4. On the Pi run the setup script:

   ```bash
   sudo /opt/radio-remote/current/scripts/tailscale_setup.sh
   ```

   It finds the Pi's Tailscale name (`<pi>.<tailnet>.ts.net`), lets the HTTPS front end (Caddy) fetch a real certificate for it
   from Tailscale, adds the name to Caddy, checks the new configuration before using it, and tests the address. The first request
   can take up to a minute while the certificate is issued. `--dry-run` shows what it would do; `--remove` goes back to the LAN
   name only. The name is remembered in `/etc/radio-remote/caddy-extra-hosts`, so updates and reboots keep it.
5. Open `https://<pi>.<tailnet>.ts.net` from any device that has Tailscale switched on.

**Why `https://<pi>.local:8443`, the `100.x.y.z` address or a port number did not work:** the HTTPS front end answers only to the
names it is configured for (by default `<hostname>.local`) on the standard HTTPS port 443. Under any other name it has no site
and gives no answer. Step 4 adds the Tailscale name. The `100.x.y.z` address cannot get a certificate: use the `.ts.net` name.

Alternative without Caddy: `sudo tailscale serve --bg --https=443 http://127.0.0.1:8080`. **Requires verification:** whether your
Tailscale version forwards `X-Forwarded-Proto: https`; without it the session cookie lacks `Secure`. Check in the browser
dev tools; prefer the script above if it is missing.

**If the script stops with a message:** "not signed in" means run `sudo tailscale up`; "MagicDNS" or "HTTPS Certificates" means
switch them on in the admin console and run the script again; a timeout at the end means the certificate could not be issued yet:
wait a minute and run it again, and look at `sudo journalctl -u caddy -n 30`.

## Audio over Tailscale

WebRTC audio uses UDP. On a **direct** tailnet path it works like the LAN. On a **relayed** (DERP) path expect extra delay or
failure. Check with `tailscale ping <pi>` (it says "direct" or "via DERP"). A WebSocket audio fallback is a planned revision.

## Hardening checklist

- Tailscale ACLs: allow only your own devices/users to reach the Pi on port 443.
- Keep the Pi's firewall rules from [06-security-remote.md](06-security-remote.md).
- Use strong, unique passwords; give family/guests `viewer` accounts.
- Optional: Tailscale "key expiry" and device approval so a lost phone can be cut off.
