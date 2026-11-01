# Self-Hosting VARUNETRA with Docker

VARUNETRA ships as a self-contained Next.js standalone build. This guide covers
running it with Docker / Docker Compose, deploying it as a [CasaOS](https://casaos.io)
app, and configuring the optional API keys.

> **TL;DR:** VARUNETRA runs fully **without any API keys**. All core feeds
> (aviation, satellites, fires, earthquakes, weather, news, CVEs) use public
> keyless sources. Keys only matter for the optional RECON scanner backend and
> for raising rate limits on a few feeds.

---

## 1. Docker Compose (recommended)

```bash
git clone https://github.com/simplifaisoul/varunetra.git
cd varunetra

# optional: configure keys / scanner backend
cp .env.template .env        # then edit .env

docker compose up -d
```

Open <http://localhost:3000>.

What the compose file does:

- **`build:`** — compose builds the image locally from the `Dockerfile`, so
  you always run the code you just cloned. To run the prebuilt registry image
  instead, add `image: ghcr.io/simplifaisoul/varunetra:latest` to the `varunetra`
  service and drop the `build:` block.
- **`env_file: .env` (`required: false`)** — if a `.env` file exists its
  values are injected into the container; if it's missing, VARUNETRA still starts
  with the keyless feeds.
- **`ports: ${VARUNETRA_PORT:-3000}:3000`** — the web UI. The container always
  listens on 3000; the published **host** port is `VARUNETRA_PORT` (default
  `3000`). Set `VARUNETRA_PORT` in `.env` to remap it, e.g. `VARUNETRA_PORT=3005`
  when 3000 is already in use — no need to edit the compose file.
- **`restart: unless-stopped`** — survives reboots.

Common commands:

```bash
docker compose logs -f          # follow logs
docker compose up -d --build    # rebuild locally after pulling new code
docker compose down             # stop & remove
```

### Pull the prebuilt image from GHCR

A prebuilt image for `linux/amd64` and `linux/arm64` is published to the GitHub
Container Registry on every push to `master` and every `v*.*.*` tag, so you can
run VARUNETRA without building anything:

```bash
docker pull ghcr.io/simplifaisoul/varunetra:latest   # or a pinned tag, e.g. :0.1.0
docker run -d --name varunetra \
  -p 3005:3000 --env-file .env --restart unless-stopped \
  ghcr.io/simplifaisoul/varunetra:latest
```

The package is public — no `docker login` is required to pull it.

### Plain `docker run`

```bash
docker build -t varunetra:latest .
docker run -d --name varunetra -p 3000:3000 --env-file .env --restart unless-stopped varunetra:latest
```

### Image details

Multi-stage build on `node:22-alpine`, runs as a non-root user (`nextjs`,
uid 1001), serves Next.js standalone via `node server.js` on port 3000.
Final image is ~220 MB. Build excludes `node_modules`, `.next`, `.git` and the
repo's large `*.diff` artifacts via `.dockerignore`.

---

## 2. CasaOS

The compose file includes an `x-casaos:` metadata block (title, description,
icon, port map, env descriptions) that plain Docker Compose ignores but CasaOS
reads.

**Install:**

1. On the CasaOS host, clone the repo somewhere persistent (e.g.
   `/DATA/AppData/varunetra`).
2. CasaOS dashboard → **`+`** → **Install a customized app** → paste the
   contents of `docker-compose.yml`.
   *(or simply run `docker compose up -d` from the cloned directory).*
3. VARUNETRA appears on the dashboard with its icon, reachable on host port
   `3000` (or whatever `VARUNETRA_PORT` you set in `.env`).

The app icon is the gold Eye-of-Horus mark in
`public/casaos-icon.png` (512×512 PNG), referenced by the `icon:` URL in the
metadata.

> CasaOS stores imported compose files under `/var/lib/casaos/apps/`, so a
> relative `build:` context may not resolve there. If importing the YAML
> directly, either build/tag `varunetra:latest` first
> (`docker build -t varunetra:latest /path/to/varunetra`) or replace the `build:`
> block with `image: ghcr.io/simplifaisoul/varunetra:latest`.

---

## 3. API keys & data sources

Copy `.env.template` to `.env` and fill in only what you need.

### What the code actually reads today

| Variable | Purpose | Required for |
|----------|---------|--------------|
| `SCANNER_URL` | RECON scanner backend base URL (e.g. `http://scanner:7700`) | RECON toolkit (quick/ssl/headers/rdns/subdomains/tech/whois/geoloc/vuln) |
| `SCANNER_KEY` | Shared secret; **must equal the backend's `VARUNETRA_KEY`** | RECON toolkit |
| `GFW_API_TOKEN` | Private Global Fishing Watch Bearer token used for reported-vessel identity lookups | Vessel details in the oil-slick inspector |

Without `SCANNER_URL`/`SCANNER_KEY` the RECON endpoints return `503` and the
rest of VARUNETRA works normally. Generate a key with `openssl rand -hex 32`.
Set `GFW_API_TOKEN` only in the server environment (or the Docker `.env` file);
never use a `NEXT_PUBLIC_` prefix, because that would expose the token to browsers.

Live AIS vessels are served by the VARUNETRA backend, not the intel container.
Set `AISSTREAM_KEY` in the backend environment and set `VARUNETRA_API_URL` on
the frontend to the backend base URL. If omitted, the proxy derives the base
URL from `NEXT_PUBLIC_VARUNETRA_API_URL`.

### Optional keys (reserved / for higher rate limits)

These are documented for completeness and forward-compatibility. The current
data routes use **keyless** public feeds, so these are not consumed yet — set
them only if you extend the relevant route or hit rate limits.

| Variable | Service | How to get it (all free) |
|----------|---------|--------------------------|
| `FIRMS_API_KEY` | NASA FIRMS active fires | Enter an email at <https://firms.modaps.eosdis.nasa.gov/api/map_key/> — the `MAP_KEY` is emailed instantly. Limit 5000 req / 10 min. |
| `OPENSKY_CLIENT_ID` / `OPENSKY_CLIENT_SECRET` | OpenSky aviation | Create an account at <https://opensky-network.org/>, open **Account → API client**, create a client and copy id/secret. **OAuth2 only since March 2025** (username/password auth removed). |
| `N2YO_API_KEY` | N2YO satellites | Register at <https://www.n2yo.com/login/register/>, then **Profile → generate API key**. Limit 1000 req / hour; key can't be regenerated. |

### Optional keys for Live Transit

The **Live Buses & Transit** layer needs no key: it reads 185 operators' own
GTFS-Realtime feeds, including all of Great Britain and New York City. Each
variable below adds an operator that publishes only behind a free sign-up. The
key stays on the server; without it that operator is simply not shown.

| Variable | Adds | Get a key |
|----------|------|-----------|
| `TRANSIT_WMATA_KEY` | Washington DC Metrobus and Metrorail | <https://developer.wmata.com/> |
| `TRANSIT_511_KEY` | San Francisco Bay Area, every operator | <https://511.org/open-data/token> |
| `TRANSIT_TRIMET_KEY` | Portland TriMet | <https://developer.trimet.org/appid/registration/> |
| `TRANSIT_TRANSLINK_KEY` | Vancouver TransLink | <https://developer.translink.ca/> |
| `TRANSIT_STM_KEY` | Montréal STM | <https://portail.developpeurs.stm.info/> |
| `TRANSIT_NTA_KEY` | All of Ireland | <https://developer.nationaltransport.ie/> |
| `TRANSIT_TRAFIKLAB_KEY` | Stockholm SL | <https://developer.trafiklab.se/> |
| `TRANSIT_TFNSW_KEY` | Sydney buses, trains, metro, ferries and light rail | <https://opendata.transport.nsw.gov.au/> |
| `TRANSIT_METLINK_KEY` | Wellington Metlink | <https://opendata.metlink.org.nz/> |
| `TRANSIT_AT_KEY` | Auckland Transport | <https://dev-portal.at.govt.nz/> |

A refused key shows as "key refused" on that operator in `/api/transit`.

> Keep `.env` out of version control — it is already in `.gitignore`. Only
> `.env.template` (no secrets) is committed.

### Optional runtime overrides

| Variable | Purpose | Default |
|----------|---------|---------|
| `VARUNETRA_TELEGRAM_CHANNELS` | Comma-separated list of public Telegram channel usernames (no `@`) to scrape for the **Telegram OSINT** map layer. Overrides the curated default set. | `osintdefender,insiderpaper,aljazeeraenglish,nexta_live,war_monitor` |
| `VARUNETRA_PORT` | Host port the compose file publishes (container itself always listens on 3000). | `3000` |
| `VARUNETRA_API_URL` | Base URL for the VARUNETRA backend used by the vessel API proxy. Defaults to the base derived from `NEXT_PUBLIC_VARUNETRA_API_URL`. | `http://localhost:3001` |

### Keyless sources (no configuration needed)

Aviation → `adsb.lol` · Satellites → `celestrak.org` (TLE) · Fires →
NASA FIRMS open-data CSV · Earthquakes → USGS · Weather → NASA EONET · Space
weather → NOAA SWPC · CVEs → NVD · News → public RSS / HLS streams · CCTV →
public traffic-authority feeds · Crypto (BTC) → `blockstream.info` · Crypto
(ETH) → `eth.blockscout.com` ([Blockscout](https://github.com/blockscout/blockscout)
open-source explorer) · OFAC SDN sanctions → [OpenSanctions](https://www.opensanctions.org)
mirror (CC-BY 4.0) · Telegram OSINT → public `t.me/s/<channel>` web preview.
