# raspizero

A Raspberry Pi Zero 2 W (Raspberry Pi OS / Debian 13 trixie) with an OV5647 camera (Pi camera v1.3), on the home WiFi.
It's **Rocky's eyes**: the voice assistant ([`k8s/esp32-homeai`](../k8s/esp32-homeai/)) asks it for a photo when you
say "take a photo" or "what do you see?". Not part of the Kubernetes cluster.

| Folder | What it is | Runs as |
|---|---|---|
| [`rocky-cam/`](rocky-cam/) | one photo per HTTP request, for the API's `look_through_camera` tool | user service (`lalotone`) |
| [`wifi-fallback-ap/`](wifi-fallback-ap/) | opens its own access point when it can't join a WiFi network, to fix the WiFi | system service (root) |

Secrets (the camera token, the AP password) live only on the Pi and in a Kubernetes Secret, never here.

## rocky-cam

`GET /snapshot` with `Authorization: Bearer <token>` returns a JPEG taken right then with `rpicam-still`, at the
sensor's full resolution (2592x1944, ~650 KB, ~3.7 s; `ROCKY_CAM_SIZE` to change it): the API zooms into parts of it.

**Exposure is metered by rocky-cam itself.** With this OV5647 module and libcamera 0.7.2 the camera's auto-exposure
acts as if the room were dark: in daylight it chose 30 ms at 3.8x gain (~16x too much light), 55% of the image was pure
white, and EV compensation did nothing, while a manual 2 ms exposure looked right. So rocky-cam takes 1-3 small test
frames at known exposures, measures their brightness (plain Python on the YUV frame) and shoots at the exposure that
hits a target brightness: 55% → 8% blown out, brightness 227 → 90/255. Focus is fixed (~1 m to infinity on the v1.3
lens): sharp at arm's length and beyond, soft within a few cm.

By design:

- **No stream, nothing stored**: the photo goes from the camera's stdout into the HTTP response.
- **Only heavy1 may ask** (`ROCKY_CAM_ALLOW` in `~/.config/rocky-cam/env`: heavy1's LAN address, since the voice pod reaches the Pi through
  it; unset = nobody), **and it needs the token** (`~/.config/rocky-cam/token`, mode 600, compared in constant time).
- One capture at a time, at most one every 3 s; every request is logged (`systemctl --user status rocky-cam`).
- Hardened unit: read-only home and system, private /tmp, no new privileges.

Measured 2026-10-23: from this laptop → 403; from heavy1 without/with a wrong token → 401; right token → 200 JPEG in
1.5-2.7 s; a second request at once → 429; POST/PUT/DELETE → 405.

Install (as the user, no sudo):

```bash
scp rocky-cam/rocky_cam.py raspizero:rocky-cam/ && scp rocky-cam/rocky-cam.service raspizero:.config/systemd/user/
ssh raspizero 'umask 077; mkdir -p ~/.config/rocky-cam && echo ROCKY_CAM_ALLOW=<heavy1 LAN IP> > ~/.config/rocky-cam/env &&
  python3 -c "import secrets,sys; sys.stdout.write(secrets.token_urlsafe(32))" > ~/.config/rocky-cam/token'
ssh raspizero 'loginctl enable-linger $USER && systemctl --user daemon-reload && systemctl --user enable --now rocky-cam'
# the token goes straight from the Pi into the cluster, never printed:
ssh raspizero 'cat ~/.config/rocky-cam/token' | kubectl -n voice create secret generic rocky-cam --from-file=token=/dev/stdin
kubectl -n voice rollout restart deploy/esp32-homeai-api
```

**Linger is required**: without it the user's services stop when the last login session closes, and the camera
vanishes ("camera unreachable"). Rotate the token the same way (new file, re-create the Secret, restart the API); the
old one stops working immediately. Give the Pi a DHCP reservation in the router: the API uses its IP.

## wifi-fallback-ap

With NetworkManager: if no WiFi is connected 90 s after boot, or after a drop longer than 60 s, it starts the access
point `<hostname>-setup` (WPA2) on `10.42.0.1`. Join it, `ssh <user>@10.42.0.1`, and fix the WiFi:
`sudo nmcli device wifi connect "<SSID>" password "<password>"`. While the AP is up and nobody is on it, it retries
the saved networks every 5 minutes; when WiFi is back the AP goes away. `wifi-fallback-ap --status` shows what it
would do.

Install: copy the folder to the Pi, put the AP password (8-63 chars) in `ap-password` next to `install.sh`, then
`sudo bash install.sh`. The password ends up only in `/etc/wifi-fallback-ap.conf` (root-only) and NetworkManager's
own profile; `ap-password` is shredded.
