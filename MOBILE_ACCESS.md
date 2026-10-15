# Controlling Dexter from your phone

Dexter runs on the PC. This sets it up so you can drive it from your phone —
**from anywhere**, over an encrypted private network, with a real HTTPS padlock
and no port number in the URL. The backend never leaves loopback; Tailscale is
the only way in.

---

## Your setup (this machine)

| | |
|---|---|
| **Open on the phone** | `https://dexter.tail91d307.ts.net` |
| Tailnet | `tail91d307.ts.net` |
| PC (this machine) | `dexter` · `100.76.72.30` |
| Backend bind | `127.0.0.1:8098` (loopback only) |
| Public entry | `tailscale serve` → HTTPS 443 → `127.0.0.1:8098` |
| Auth | Tailscale device auth **+** Dexter per‑device pairing token |

> The hostname/IP above are specific to this machine. If you ever share this
> repo, scrub them — everything else here is generic.

Add the phone's browser page to the home screen (**Share → Add to Home Screen**)
and it launches like a native app, full‑screen, no URL bar.

---

## How it works

```
 iPhone  ──HTTPS──▶  tailscale serve (on the PC, tailnet:443)
 (PWA)   (WireGuard    │  terminates TLS with a real Let's Encrypt cert
          tunnel)      ▼
                    http://127.0.0.1:8098   ◀── Dexter (loopback only)
```

1. **Tailscale (WireGuard)** builds an encrypted, device‑to‑device tunnel between
   your phone and PC. Keys never leave the devices; Tailscale can't read the
   traffic. When possible the two talk directly (peer‑to‑peer), otherwise via a
   relay that only forwards *encrypted* packets.
2. **`tailscale serve`** listens on the tailnet (443), terminates HTTPS with a
   Let's Encrypt certificate for `dexter.tail91d307.ts.net`, and proxies to
   `127.0.0.1:8098`.
3. **Dexter** binds **loopback only** (`DEXTER_BIND=127.0.0.1`), so it is *not*
   reachable from the LAN or anywhere else — only through the local proxy.

### Why it's secure

- **Encrypted in transit end‑to‑end:** WireGuard (Curve25519 / ChaCha20‑Poly1305)
  for the tunnel, *plus* real TLS from `tailscale serve`. Two independent layers.
- **Only your devices can even connect:** a peer must be signed into *your*
  Tailscale account to be on the tailnet. `serve` is *tailnet‑only* (not Funnel),
  so it is never on the public internet.
- **App‑level auth on every call:** each `/api` request needs the loopback
  session token (PC shells) **or** a paired **device token** (phone). The device
  token is minted by redeeming a short‑lived code shown on the PC; only its
  SHA‑256 hash is stored in `data/devices.json`.
- **Token bootstrap can't leak:** `GET /api/dexter/auth/token` is refused unless
  the request arrives under a loopback host. The proxy forwards the *tailnet*
  Host, so the phone is correctly treated as remote and **must pair** — verified:
  `/auth/token` over the proxy returns `403 Pair this device instead.`
- **No LAN exposure:** loopback bind means nothing is open on your Wi‑Fi.

### Residual notes

- The MagicDNS name (`dexter.tail91d307.ts.net`) appears in public Certificate
  Transparency logs once HTTPS is enabled — the *name* is public, the *service*
  is not.
- The voice WebSocket (`/api/dexter/voice/ws`) is not behind the HTTP auth
  middleware. On a tailnet that's your own devices only; it is not a concern for
  chat/scheduler use. (Tracked as a future hardening item.)

---

## First‑time setup (to reproduce on another PC)

1. **Install Tailscale** on the PC and the phone (iOS/Android both supported);
   sign both into the **same** account and connect.
2. **Name the PC** something short in the admin console
   (<https://login.tailscale.com/admin/machines> → ⋯ → *Rename machine…*), e.g.
   `dexter`. The MagicDNS name becomes `dexter.<tailnet>.ts.net`.
3. **Enable HTTPS** for the tailnet:
   <https://login.tailscale.com/admin/dns> → **HTTPS Certificates → Enable**.
4. **Set `.env`** (see reference below): `DEXTER_BIND=127.0.0.1` and
   `DEXTER_TAILNET_HOST=<magicdns name>,<100.x IP>` (comma‑separated; include the
   bare name too if you want to reach it that way).
5. **Start the proxy** on the PC (persists across reboots):
   ```
   tailscale serve --bg 8098
   ```
   (Use whatever port Dexter actually took — see `%LOCALAPPDATA%\Buzzcaf\ports.json`.)
6. **Restart Dexter** so it picks up the loopback bind.
7. On the phone open `https://<magicdns name>`, **pair**, then **Add to Home
   Screen**.

---

## Pairing a phone (or a new device)

1. On the **PC**: open Dexter → **Open Full Dashboard** → the **“Pair a phone”**
   panel → **Generate pairing code** (valid ~2 minutes).
2. On the **phone**: open `https://dexter.tail91d307.ts.net` → enter the 6‑char
   code → **Pair**. The device token is stored in the browser; you stay signed in.
3. Manage/revoke devices from the same **Pair a phone** panel (or
   `DELETE /api/dexter/devices/{id}`).

Re‑pair is needed only if you clear the site's browser data, or the URL's origin
changes (e.g. you switch from `http://…:8098` to `https://…`).

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Phone can't load the page | Tailscale toggle **ON** on the phone? PC online in the tailnet? Try the `100.x` IP: `https://100.76.72.30` won't have a matching cert — prefer the MagicDNS name. |
| `403 Forbidden host` | The Host isn't in the allowlist. Add it to `DEXTER_TAILNET_HOST` in `.env` and restart Dexter. |
| `401` on every call | Not paired (or token cleared). Re‑pair from the PC panel. |
| `serve` hangs when first run | HTTPS Certificates not enabled yet (step 3). Enable, then re‑run. |
| Port changed (not 8098) | Dexter takes the next free port from 8098 up. Re‑point the proxy: `tailscale serve reset` then `tailscale serve --bg <newport>`. |
| Check the proxy | `tailscale serve status` |

---

## Exposing another app on the same PC

`tailscale serve` can front any local port. Give each app its **own HTTPS port**
on the same tailnet host so their asset paths never collide (a `/subpath` breaks
apps that assume they live at `/`).

Already exposed:

| App | Local port | Phone URL |
|---|---|---|
| Dexter | 8098 | `https://dexter.tail91d307.ts.net` |
| Buzzcaf Studio | 8099 | `https://dexter.tail91d307.ts.net:8443` |

To add another (e.g. a local app on port 9000):

```
tailscale serve --bg --https=9001 9000
```

Then open `https://dexter.tail91d307.ts.net:9001`. Remove one with
`tailscale serve --https=<port> off`.

> **Auth caveat:** Dexter gates every request with a per‑device pairing token.
> Other apps (Buzzcaf Studio included) may have **no** auth of their own — on the
> tailnet that means *any device signed into your Tailscale account* can use
> them. Fine for your own phone/PC; just don't share your tailnet with others, or
> add auth to those apps if you do.

## Turning it off

- **Stop remote access (Dexter):** `tailscale serve --https=443 off`.
- **Stop Buzzcaf Studio:** `tailscale serve --https=8443 off`.
- **Stop everything:** `tailscale serve reset`.
- **Revoke a device:** the *Pair a phone* panel, or `DELETE /api/dexter/devices/{id}`.
- **Fully local again:** remove `DEXTER_BIND` / `DEXTER_TAILNET_HOST` from `.env`
  (or leave `DEXTER_BIND=127.0.0.1`) and restart.

---

## `.env` reference

```dotenv
# Dexter binds loopback only; tailscale serve exposes HTTPS on the tailnet
# and proxies to it. Nothing is exposed on the LAN.
DEXTER_BIND=127.0.0.1
# Host-header allowlist — the proxy forwards the tailnet Host, which the
# server must accept. Comma-separated: bare name, MagicDNS name, 100.x IP.
DEXTER_TAILNET_HOST=dexter,dexter.tail91d307.ts.net,warhead-mothership.tail91d307.ts.net,100.76.72.30
```

`DEXTER_BIND=0.0.0.0` also works (bind to all interfaces, no proxy) but exposes
the port to your LAN and serves plain HTTP — the `tailscale serve` setup above is
strictly more secure and is the recommended path.
