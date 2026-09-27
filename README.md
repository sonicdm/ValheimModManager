# Valheim Mod Manager

Self-hosted companion for an existing [community-valheim-tools/valheim-server](https://github.com/community-valheim-tools/valheim-server-docker) deployment (`ghcr.io/community-valheim-tools/valheim-server`). Discover, install, configure, update, and roll back BepInEx mods from **Thunderstore** and **Hexium** without replacing the game container or touching world saves.

That image is the relocated home of the former [lloesche/valheim-server-docker](https://github.com/lloesche/valheim-server-docker) project (same layout and Supervisor ABI). Legacy `ghcr.io/lloesche/valheim-server` tags remain compatible until they disappear.

## Screenshots

![Dashboard](docs/screenshots/dashboard.png)

| Discover | Installed |
| --- | --- |
| ![Discover](docs/screenshots/discover.png) | ![Installed](docs/screenshots/installed.png) |

| Package install | Config editor |
| --- | --- |
| ![Package](docs/screenshots/package.png) | ![Config](docs/screenshots/config.png) |

| Settings | Sign in |
| --- | --- |
| ![Settings](docs/screenshots/settings.png) | ![Sign in](docs/screenshots/login.png) |

## How it fits the Valheim image

With `BEPINEX=true`, the server image keeps a persistent tree under `/config/bepinex` and syncs plugins/patchers into `/opt/valheim/bepinex/BepInEx/` during `valheim-bootstrap` (container start and BepInEx install/update). The manager:

1. Writes installs to the **config** mount (`.../config/bepinex`)
2. Applies changes by stopping `valheim-server`, running `valheim-bootstrap`, then starting `valheim-server` again — it does **not** bind-mount or write `data/bepinex`
3. Leaves the Valheim image and world saves alone

Do **not** copy this working folder as-is (`.venv`, `node_modules`, and `data/` are machine-local).

## Quick start (standalone Compose)

1. Clone and configure paths for **your** host (CVT / former lloesche layout shown):

```bash
git clone https://github.com/sonicdm/ValheimModManager.git
cd ValheimModManager
cp .env.example .env
```

Edit `.env` (required values first):

| Variable | Typical value |
|---|---|
| `MOD_MANAGER_SECRET_KEY` | Long random string (session/CSRF signing) |
| `MOD_MANAGER_ADMIN_PASSWORD` | UI login password for user `admin` |
| `VALHEIM_BEPINEX_PATH` | `$HOME/valheim-server/config/bepinex` |
| `VALHEIM_DOCKER_NETWORK` | Network of the Valheim container (`docker network ls`) |
| `SUPERVISOR_URL` | `http://<valheim-container-name>:9001` |
| `MOD_MANAGER_PORT` | Host port for the UI (default `8090`) |
| `TIMEZONE` | IANA zone for maintenance cron (default `America/Los_Angeles`) |

Do **not** mount `data/bepinex` or its `plugins`/`patchers` subfolders into the manager. Those binds pin the directory the Valheim image renames during BepInEx merge.

2. On the Valheim container, enable Supervisor HTTP if you want restart control: `SUPERVISOR_HTTP=true` (and optionally `SUPERVISOR_HTTP_PASS`). Use that same password as `SUPERVISOR_PASSWORD` here — not a misnamed `SUPERVISOR_PASS`.

3. Pull and run (image: `ghcr.io/sonicdm/valheim-mod-manager:latest`):

```bash
docker compose pull
docker compose up -d
```

4. Open http://localhost:8090 and sign in as `admin` with `MOD_MANAGER_ADMIN_PASSWORD`.

On launch the manager runs a **one-shot plugin scan** (and refreshes package indexes) in the background, so existing mods under `config/bepinex` show up without clicking Scan first. Use **Scan plugins** anytime after you change files outside the UI.

Persistent manager state lives in the `mod_manager_data` volume, not in the repo.

### Updating

Merges to `main` (and git tags `v*`) publish a new image via GitHub Actions. On the server:

```bash
docker compose pull
docker compose up -d
```

Pin a release with `image: ghcr.io/sonicdm/valheim-mod-manager:0.1.0` if you prefer not to float on `:latest`.

After the first successful publish, open the GHCR package on GitHub and set visibility to **Public** so unauthenticated pulls work.

## Usage

After login you get seven pages. Day-to-day work is mostly **Discover → Installed → Restart + sync**.

### Dashboard

- **Scan plugins** — Walks `config/bepinex` and updates the managed list. Prunes packages that disappeared from disk. Also runs once automatically when the manager process starts; use the button after you change files outside the UI.
- **Check updates** — Compares installed managed mods against Thunderstore/Hexium indexes and shows pending updates.
- **Restart + sync** — Stops `valheim-server`, runs `valheim-bootstrap` (copies plugins/patchers into the live BepInEx tree), then starts the server again. Use this after installs, uninstalls, config changes that need a reload, or **Make persistent**.

Also shows server status, counts, pending updates, maintenance schedule (local wall-clock), and recent activity.

### Discover and install

1. Open **Discover**. Search by name/author; use **Include categories** / **Exclude categories** (Thunderstore-style), source, and sort. Defaults to server-side tags (`Server-side`, Hexium `Server-only` / `Client & Server`) so client-only chrome stays out of the way.
2. Open a package. The page loads the store **README** / **Changelog** (full markdown, not just the short blurb), categories, and stats. Pick a version, optionally **Preview**, then **Install**.
3. Install downloads the zip, extracts into staging, copies into `config/bepinex/plugins` (and patchers when present), and records the package in the manager DB.
4. When the install finishes, use **Restart + sync** on the dashboard so the game process actually loads the new files.

Large packs can take a while; the install page shows progress while the request runs.

### Installed

Manage everything already on disk:

| Action | What it does |
|---|---|
| **Disable / Enable** | Moves the plugin folder aside or back without deleting it |
| **Pin / Unpin** | Blocks automatic updates for that package |
| **Configure** | Jumps to that mod’s `.cfg` in **Config** (when one is known) |
| **Link store / Change store** | Attach a local/unmanaged DLL to a Thunderstore or Hexium package for updates |
| **Make persistent** | For bulky mods (lots of small files). Moves files to `config/bepinex/.persistent/<package>` and leaves a symlink in `plugins/` pointing at `/config/bepinex/.persistent/<package>`. Bootstrap then copies one symlink instead of walking thousands of files. Runtime data written by the mod follows the link and survives live-tree replaces. |
| **Make normal** | Undoes persistent layout (files back under `plugins/`) |
| **Uninstall** | Removes managed files and the DB row |
| **Import zip / DLL** | Drop a local package when it isn’t on a store |
| **Rescan** / **Refresh indexes + scan** | Re-read disk and/or refresh package indexes |

A **persistent** badge appears on rows that use the symlink layout.

### Config

Lists BepInEx and mod `.cfg` files under the config tree. Open one to edit (structured fields when parseable, raw text otherwise). Save, then **Restart + sync** if the mod only reads config at startup.

### History and Backups

- **History** — Audit log of installs, uninstalls, scans, restarts, settings changes, and errors.
- **Backups** — Create or restore zip backups of managed plugin state before risky upgrades.

### Settings

- Set **time zone** (IANA name, e.g. `America/Los_Angeles`) so the maintenance cron runs at local wall-clock time — not UTC. Compose `TIMEZONE` overrides this on every container start.
- Enable **automatic maintenance** (update check + optional apply in a time window).
- Choose whether maintenance restarts the server after updates.
- **Force restart when player status is unknown** — off by default (fail-safe); turn on only if you accept restarts when the manager cannot prove the server is empty.
- Change the admin password.

### Typical flow (new mod)

1. **Discover** → install the package.
2. If it is huge (web map tiles, asset packs, etc.), **Installed** → **Make persistent**.
3. Dashboard → **Restart + sync**.
4. Edit `.cfg` under **Config** if needed, then restart again.

## Combined Compose (server + manager)

One file runs [community-valheim-tools](https://github.com/community-valheim-tools/valheim-server-docker) and this manager on the same Docker network. Copy `docker-compose.combined.example.yml`, put `config/` and `data/` next to it (or edit the volume paths), fill `.env`, then:

```bash
cp .env.example .env
# set MOD_MANAGER_SECRET_KEY, MOD_MANAGER_ADMIN_PASSWORD, SERVER_PASS, …
docker compose -f docker-compose.combined.example.yml --env-file .env pull
docker compose -f docker-compose.combined.example.yml --env-file .env up -d
```

```yaml
services:
  valheim:
    image: ghcr.io/community-valheim-tools/valheim-server
    container_name: valheim
    cap_add:
      - sys_nice
    volumes:
      - ./config:/config
      - ./data:/opt/valheim
    ports:
      - "2456-2458:2456-2458/udp"
      - "9001:9001/tcp"   # Supervisor HTTP (LAN only)
    environment:
      - SERVER_NAME=${SERVER_NAME:-My Server}
      - WORLD_NAME=${WORLD_NAME:-Dedicated}
      - SERVER_PASS=${SERVER_PASS:-secret}
      - SERVER_PUBLIC=${SERVER_PUBLIC:-0}
      - TZ=${TIMEZONE:-America/Los_Angeles}
      - BEPINEX=true
      - SUPERVISOR_HTTP=true
      - SUPERVISOR_HTTP_PORT=9001
      - SUPERVISOR_HTTP_USER=admin
      - SUPERVISOR_HTTP_PASS=${SUPERVISOR_HTTP_PASS:-}
    restart: unless-stopped
    stop_grace_period: 2m

  mod-manager:
    image: ghcr.io/sonicdm/valheim-mod-manager:latest
    container_name: ValheimModManager
    depends_on:
      - valheim
    ports:
      - "${MOD_MANAGER_PORT:-8090}:8090"
    volumes:
      - mod_manager_data:/data
      - ./config/bepinex:/valheim/bepinex
      - ./config/bepinex:/config/bepinex
    environment:
      - DATA_DIR=/data
      - BEPINEX_ROOT=/valheim/bepinex
      - SECRET_KEY=${MOD_MANAGER_SECRET_KEY}
      - ADMIN_PASSWORD=${MOD_MANAGER_ADMIN_PASSWORD:-changeme}
      - SUPERVISOR_URL=http://valheim:9001
      - SUPERVISOR_USER=admin
      - SUPERVISOR_PASSWORD=${SUPERVISOR_HTTP_PASS:-}
      - SUPERVISOR_PROGRAM=valheim-server
      - CONTAINER_DISPLAY_NAME=valheim
      - TIMEZONE=${TIMEZONE:-America/Los_Angeles}
    restart: unless-stopped

volumes:
  mod_manager_data:
```

Open http://localhost:8090 after BepInEx has created `config/bepinex` (first boot with `BEPINEX=true`). Do **not** bind-mount `data/bepinex`.

If Valheim already runs in another compose project, use the standalone `docker-compose.yml` plus an external network instead — see `docker-compose.merge.example.yml`.

## Development

To hack on the manager without waiting for GHCR, uncomment `build: .` next to the `image:` line in compose and run `docker compose up -d --build`, or run the API/UI locally:

```bash
# Backend
cd backend
python -m venv .venv
# Windows: .\.venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
export DATA_DIR=../data
export BEPINEX_ROOT=/path/to/valheim-server/config/bepinex
export ADMIN_PASSWORD=changeme
uvicorn app.main:app --reload --port 8090

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
```

Tests:

```bash
cd backend
pytest
```

## Safety notes

- Writes only under the mounted BepInEx tree and the manager `/data` volume.
- Package zips are treated as untrusted; path traversal and symlink members are rejected.
- Automatic maintenance **defers** restarts when player status cannot be proven, unless `force_restart_when_players_unknown` is enabled.
- Intended for a trusted LAN or behind an authenticated reverse proxy — not direct internet exposure.
