# Dual Reflector (UxPlay) – Web + macOS Overlay

This project runs two `uxplay` AirPlay receivers (iPhone + iPad) and provides two viewing modes:

- **Web UI**: a local page that shows both devices side-by-side.
- **macOS overlay**: native, click-through frames drawn over the two `uxplay` windows (no browser).

## Install

```bash
pip install -r requirements.txt
```

You also need `uxplay` installed and on your `PATH`.

## Run (macOS overlay, no browser)

```bash
python3 main.py --ui overlay
```

If your iPhone/iPad gets stuck connecting, try disabling window auto-positioning:

```bash
python3 main.py --ui overlay --position off
```

## Run (web UI)

```bash
python3 main.py --ui web
```

Open `http://localhost:8080`.

The web UI now includes a demo picker that swaps the microsite loaded into the iPad frame and updates the matching logo automatically. To change the startup selection, set `SUMAC_DEMO_ID` before starting the server.

To manage the microsite list in the browser, open `http://localhost:8080/admin/demos`. That page lets you edit URLs, add new microsites, and delete old ones. Saved changes are written to `demo_options.json`.

If a microsite needs browser permissions inside the iframe, use the `Grant demo permissions` button in the web UI first. That primes `localhost` for microphone, camera, and location access so the proxied microsite can reuse those permissions in the embedded frame.

If UxPlay freezes after you manually resize its window(s), run with move-only positioning so the script doesn’t fight your resize:

```bash
python3 main.py --ui web --position move
```

If it still freezes during live resizing, force screen-based capture (more resilient while resizing):

```bash
python3 main.py --ui web --position move --capture screen
```

## Public quick tunnel

For a quick public HTTPS URL from this Mac, protect the app with HTTP Basic Auth and expose it through `cloudflared`.

In one terminal, start the app with a shared password and bind it to loopback only:

```bash
export SUMAC_BASIC_AUTH_PASSWORD='iamthepasswordforthissoftwareletmein'
export SUMAC_BASIC_AUTH_USERNAME='sumac'
python3 main.py --ui web --host 127.0.0.1
```

In a second terminal, start a Cloudflare quick tunnel:

```bash
cloudflared tunnel --url http://127.0.0.1:8080
```

Share the printed `https://*.trycloudflare.com` URL along with:

- Username: `sumac`
- Password: `iamthepasswordforthissoftwareletmein`

Notes:

- The public URL changes each time you restart the tunnel.
- The site stays live only while this Mac, the Python app, `uxplay`, and `cloudflared` are all running.
- Browser permissions are requested on the current site host, not just `localhost`.

## macOS Permissions

- **Accessibility** (needed for auto-positioning `uxplay` windows via AppleScript):  
  System Settings → Privacy & Security → Accessibility → allow your terminal / IDE.
- **Screen Recording** (needed for **web UI** capture/streaming):  
  System Settings → Privacy & Security → Screen Recording → allow your terminal / IDE.
- **Overlay mode does not require Screen Recording.**
# demo_env
