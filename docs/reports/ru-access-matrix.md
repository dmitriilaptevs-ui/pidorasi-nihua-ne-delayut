# RU-access transport matrix — protocol and raw evidence

**Status:** working report with raw evidence only. The verdict, chosen transport
and rollback belong to the orchestrator's ADR ([docs/adr/0003-ru-access-transport.md]);
this file does not edit it, ROADMAP or DELTA-PLAN. Owner confirmation of any new
public URL stays with the orchestrator/owner.

**Date:** 2026-10-05 (all timestamps UTC unless noted). **Host vantage:** the
laptop itself, whose egress goes through the Happ VPN to Cherry Servers DE.
Host-side success therefore proves the tunnel works, **not** reachability from
a Russian consumer connection — that requires the owner's device.

## 1. Verified topology facts

- The laptop is behind NAT in RU Wi-Fi; no inbound public IP/port-forward
  (verified in DELTA-PLAN §1.1 and re-confirmed by `ss -tlnp`: only loopback
  ports are published).
- Consequence: `sslip.io` (or any DNS pointing at the host's public IP) **cannot**
  serve traffic inbound. Goal criterion 1 mentions "temporary sslip.io until a
  real domain" — that requires either a real host with inbound reachability or a
  tunnel hostname whose owner confirmation is recorded. Flagged for the owner.
- Public entry today is an outbound tunnel; `PUBLIC_ORIGIN` in `infra/.env`
  matches the active tunnel hostname.

## 2. Protocol (how each candidate is measured)

For each transport:

1. Start it exactly with the recorded command; capture full raw output.
2. From the host, resolve + connect to the assigned URL:
   `curl -sS -o /dev/null -m 25 -w 'http=%{http_code} dns=%{time_namelookup}s tcp=%{time_connect}s tls=%{time_appconnect}s ttfb=%{time_starttransfer}s total=%{time_total}s size=%{size_download}B\n' <URL>/canvas`
3. Record: assigned URL, HTTP status, TLS time, TTFB, payload size, expiry/rotation
   behavior, and failure mode if any.
4. Owner RF check (not performed by this harness): open the same URL on a Russian
   consumer connection **without VPN**; record date, device, ISP, whether the
   canvas and login render, and repeat at a second time of day. The orchestrator
   owns this confirmation.

## 3. Candidate results

| # | Transport | Assigned URL (2026-10-05) | Host-side result | RF-confirmed by owner |
| - | --------- | ------------------------- | ---------------- | --------------------- |
| 1 | localhost.run (ssh) — current demo | `https://40cdcc5738caca.lhr.life` | HTTP 200, TLS 0.44–0.48 s, TTFB 0.98–1.62 s | Historical: owner confirmed an earlier lhr.life URL opens from RU without VPN (infra/README.md, 2026-10-05; URL rotates on reconnect) |
| 2 | Cloudflare quick tunnel | none — edge never connected | failure, see §4.2 | n/a |
| 3 | Tailscale Funnel | none — Funnel not enabled on tailnet | blocked, see §4.3 | n/a |
| 4 | Pinggy (ssh, free) | `https://qvubu-217-60-12-1.free.pinggy.net`, `https://tptcy-217-60-12-1.run.pinggy-free.link` | HTTP 200 both, TLS 0.54–0.69 s, TTFB 0.84–1.08 s, 18 733 B | not yet |
| 5 | Serveo (ssh) | `https://824238124b101fa0-217-60-12-1.serveousercontent.com` | HTTP 200, TLS 0.42 s, TTFB 0.81 s, 18 733 B | not yet |
| 6 | bore / zrok / ngrok / sslip.io | — | not evaluated: tools not installed (bore/zrok/ngrok), sslip.io inapplicable (§1) | n/a |

## 4. Raw evidence

### 4.1 localhost.run — current public origin (orchestrator-managed, not restarted)

```
$ PUB=$(grep -E '^PUBLIC_ORIGIN=' infra/.env | cut -d= -f2-)
PUBLIC_ORIGIN=https://40cdcc5738caca.lhr.life
ts=2026-10-05T16:00:47Z
attempt=1 http=200 dns=0.000066s tcp=0.000319s tls=0.477807s ttfb=1.615412s total=1.965191s
attempt=2 http=200 dns=0.000157s tcp=0.000746s tls=0.440225s ttfb=0.977877s total=1.320701s
```

DNS times are sub-millisecond, i.e. served from the local resolver cache.

### 4.2 Cloudflare quick tunnel — still cannot reach the edge

```
cloudflared version 2026.9.3 (built 2026-09-24-16:07 UTC)
flags: cloudflared tunnel --url http://127.0.0.1:3001 --no-autoupdate --edge-ip-version 4 --protocol quic
/tmp/cf-quic.log (running since 20:27 local, excerpt 15:54–16:00Z):
ERR Failed to dial a quic connection error="failed to dial to edge with quic: timeout: no recent network activity" connIndex=0 event=0 ip=198.41.192.67
ERR Failed to dial a quic connection error="failed to dial to edge with quic: timeout: no recent network activity" connIndex=0 event=0 ip=198.41.200.233
ERR Failed to dial a quic connection error="failed to dial to edge with quic: timeout: no recent network activity" connIndex=0 event=0 ip=198.41.192.227
(continuous retries across multiple edge IPs; no URL ever issued)
```

Matches the orchestrator's earlier measurement on 2026-10-05: default protocol
`TLS handshake with edge: EOF`, QUIC timeout, while `1.1.1.1` answered. UDP 7844
egress appears blocked/dropped (cloudflared's own warning in the log says so).

### 4.3 Tailscale Funnel — blocked on tailnet enablement

```
$ tailscale funnel status
No serve config
$ tailscale funnel --bg 3001          # owner pane, 2026-10-05
Funnel is not enabled on your tailnet.
To enable, visit:
         https://login.tailscale.com/f/funnel?node=nKYU12bxXC21CNTRL
```

The blocker is tailnet Funnel enablement (owner action), not the serve config.
After enabling: `tailscale funnel --bg 3001`, then the HTTPS URL is
`https://<node>.<tailnet>.ts.net`.

### 4.4 Pinggy — two working URLs per session

```
$ ssh -p 443 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/tmp/pinggy_kh \
      -R0:localhost:3001 a.pinggy.io
Allocated port 6 for remote forward to localhost:3001
You are not authenticated.
Your tunnel will expire in 60 minutes. Upgrade to Pinggy Pro to get unrestricted tunnels.
https://qvubu-217-60-12-1.free.pinggy.net
https://tptcy-217-60-12-1.run.pinggy-free.link
URL=https://qvubu-217-60-12-1.free.pinggy.net
  http=200 tls=0.691884s ttfb=1.077852s total=1.180921s size=18733B
URL=https://tptcy-217-60-12-1.run.pinggy-free.link
  http=200 tls=0.537129s ttfb=0.842294s total=0.940237s size=18733B
```

Free tier: unauthenticated, 60-minute expiry, new random subdomain every start.

### 4.5 Serveo — one working URL per session

```
$ ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/tmp/serveo_kh -R 80:localhost:3001 serveo.net
Forwarding HTTP traffic from https://824238124b101fa0-217-60-12-1.serveousercontent.com
serveo http=200 tls=0.418046s ttfb=0.813124s total=0.883965s size=18733B
```

The service prints a first-visit browser-warning tip (free tier); plain `curl`
already gets the page (200, same 18 733 B payload as the other transports).

### 4.6 Tools not present

`bore`, `zrok`, `ngrok` are not installed; no attempt was made to install them
(out of scope for a read-only evidence pass).

## 5. Risks and follow-ups

- **URL rotation:** every free transport assigns a new hostname on reconnect.
  `PUBLIC_ORIGIN`, OAuth redirect URIs (VK/Yandex) and any owner bookmarks must
  be updated when it changes. A stable hostname (real domain, reserved tunnel
  name, or a DE host) is a product decision.
- **RF reachability is unproven for Pinggy/Serveo.** Host-side measurements egress
  through the DE VPN; only the owner's device can confirm RU consumer access.
  Required per candidate if they are to count toward "≥3 options tested".
- **Free-tier limits:** Pinggy 60-minute sessions; Serveo first-visit warning;
  localhost.run rotates and has no SLA.
- **Goal wording:** criterion 1 says sslip.io + "Frankfurt (Cherry Servers) host",
  while the verified deployment is the RU laptop with a tunnel. Reconcile with
  the owner before claiming the criterion (either tunnel hostname accepted, or a
  DE host with inbound reachability is needed).
