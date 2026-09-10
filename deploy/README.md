# Deploying the fit service

```bash
./deploy/install.sh root@<host>
```

Idempotent. Run it again to ship a new revision: it fetches `main`, reinstalls
dependencies, restarts the unit and then waits for the surface to actually be
warm before reporting success. A unit that started is not evidence; a warm
surface is.

## What it sets up

The service runs as an unprivileged `tmo` user out of `/opt/tmo`, bound to
loopback on port 8080, under a systemd unit that denies it the filesystem, the
kernel knobs and any address family it does not need. It reads a public API and
writes nothing, so it is given nothing.

## What it deliberately does not do

- **No TLS and no public port.** The service binds 127.0.0.1. Putting it on the
  internet needs a reverse proxy and a certificate, which needs a hostname
  pointing at the box. That is a DNS change on a domain the owner controls and
  therefore the owner's call, not a script's.
- **No firewall changes.** If the box is unreachable, fix that in the Hetzner
  console rather than by having a deploy script rewrite firewall rules.
- **No secrets.** The service has none. It reads Deribit's public API.

## Sizing

A cold fit of one currency costs about eight seconds of CPU and the service
keeps two currencies warm, refitting each every 30 seconds. That is roughly a
third of one core at rest. Requests are cache reads and cost about a
millisecond, so concurrent users cost almost nothing until the cache misses.
The unit caps the service at two cores and 2 GB.
