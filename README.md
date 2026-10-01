# Valheim Mod Manager

Self-hosted companion for an existing [community-valheim-tools/valheim-server](https://github.com/community-valheim-tools/valheim-server-docker) deployment. Discover, install, configure, update, and roll back BepInEx mods from **Thunderstore** and **Hexium** without replacing the game container or touching world saves.

| Role | Image |
|---|---|
| Valheim server | `ghcr.io/community-valheim-tools/valheim-server` |
| Mod manager | `ghcr.io/sonicdm/valheim-mod-manager:latest` (or pin `:0.2.0`) |

## Screenshots

![Sign in](docs/screenshots/login.png)

![Dashboard](docs/screenshots/dashboard.png)

| Discover | Installed |
| --- | --- |
| ![Discover](docs/screenshots/discover.png) | ![Installed](docs/screenshots/installed.png) |

| Package | Config |
| --- | --- |
| ![Package](docs/screenshots/package.png) | ![Config](docs/screenshots/config.png) |

![Settings](docs/screenshots/settings.png)

## Usage

After **Sign in** you get eight pages: **Dashboard**, **Installed**, **Discover**, **Config**, **History**, **Profiles**, **Backups**, and **Settings**. The header has a **Theme** control (System / Light / Dark; defaults to System). Day-to-day work is mostly **Discover → install → Restart + sync**.

### Dashboard

Server status, counts (**Installed** / **Updates** / **Disabled** / **Unmanaged**), pending updates, the maintenance window, and recent activity.

| Button | What it does |
|---|---|
| **Scan plugins** | Walks `config/bepinex` and refreshes the managed list (also runs once on manager startup) |
| **Check updates** | Refreshes store indexes when they are empty or a few minutes old, then compares installed mods |
| **Update all** | Applies pending updates for managed packages (enabled when updates exist) |
| **Restart + sync** | Stops `valheim-server`, runs `valheim-bootstrap` (config → live BepInEx tree), then starts the server again |

Use **Restart + sync** after installs, uninstalls, config saves that need a reload, or **Make persistent**. A banner appears when plugin or config changes are waiting.

### Discover and install

1. Open **Discover**. Search by name/author; filter with include/exclude categories, source (Thunderstore / Hexium / all), and sort. Defaults favor server-side tags so client-only chrome stays out of the way. **Refresh indexes** pulls fresh Thunderstore/Hexium catalogs; **Reset to server-side** restores the default category set.
2. Open a package. You get store **Details** / **Changelog** (full markdown), categories, stats, and an **Install** card: pick a version, optionally **Preview**, then **Install** (or **Reinstall / Update** when that package is already installed).
3. Install downloads the zip, extracts into staging, copies into `config/bepinex/plugins` (and patchers when present), and records the package in the manager DB.
4. When the install finishes, use **Restart + sync** on the dashboard so the game process loads the new files.

Large packs can take a while; the package page shows progress while the request runs.

### Installed

**Installed mods** lists everything already on disk. Toolbar: **Import zip / DLL**, **Refresh indexes + scan**, **Rescan**, **Update all**.

| Action | What it does |
|---|---|
| **Disable / Enable** | Moves the plugin folder aside or back without deleting it |
| **Pin / Unpin** | Blocks automatic updates for that package |
| **Configure** | Jumps to that mod’s `.cfg` in **Config** (when one is known) |
| **Link store / Change store** | Attach a local/unmanaged DLL to a Thunderstore or Hexium package for updates |
| **Make persistent** | For bulky mods (lots of small files). Moves files to `config/bepinex/.persistent/<package>` and leaves a symlink in `plugins/` — see [Persistent packages](#persistent-packages) |
| **Make normal** | Undoes persistent layout (files back under `plugins/`) |
| **Uninstall** | Removes managed files and the DB row |

A **persistent** badge appears on rows that use the symlink layout.

### Config

Lists `.cfg` files under `config/bepinex/config`. Open one to edit with **Structured** fields when parseable, or **Raw** text. **Save**, then **Restart + sync** if the mod only reads config at startup.

### History and Backups

- **History** — Audit log of installs, uninstalls, package index refreshes, startup/manual plugin scans, restarts, settings changes, and errors.
- **Backups** — Create or restore zip backups of managed plugin state before risky upgrades.

### Profiles

Named mod lists for this dedicated server. **Save as new profile** from the current install, or **Import from file** / **Import from share code**. For each saved profile:

| Action | What it does |
|---|---|
| **Apply to server** | Full replace of managed Thunderstore/Hexium packs (persistent packs are kept). Creates a backup first |
| **Download profile file** | `.r2z` you can import in Gale or r2modman |
| **Copy share code** | Thunderstore/r2modman-style UUID (not Gale Sync paste, which needs Discord login) |
| **Download Thunderstore-only** | Same file export, but Hexium-only pins are remapped or dropped |

### Settings

- **Appearance** — System (default), Light, or Dark. Same preference as the header **Theme** control; stored in this browser only.
- **Server** — display name, time zone (IANA, e.g. `America/Los_Angeles` — Compose `TIMEZONE` overrides on container start).
- **Supervisor** — URL, credentials, program name for restart + sync.
- **Package sources** — Thunderstore and/or Hexium.
- **Updates** — policy, check interval, maintenance window, backup-before-update, restart-after-maintenance, and **Force restart when player status is unknown** (off by default).
- Change the admin password.

### Typical flow (new mod)

1. **Discover** → open a package → **Install** (or **Reinstall / Update**).
2. If it is huge (web map tiles, asset packs, etc.), **Installed** → **Make persistent** (ensure `POST_BEPINEX_CONFIG_HOOK` is set — [Persistent packages](#persistent-packages)).
3. Dashboard → **Restart + sync**.
4. Edit `.cfg` under **Config** if needed, then restart again.

## Quick start

Not a one-liner forever — you still need passwords, ports, and a Valheim + BepInEx layout — but for a **new host** the combined stack is:

```bash
mkdir -p valheim-stack/config valheim-stack/data && cd valheim-stack
curl -fsSL -o docker-compose.yml \
  https://raw.githubusercontent.com/sonicdm/ValheimModManager/main/docker-compose.combined.example.yml
curl -fsSL -o .env.example \
  https://raw.githubusercontent.com/sonicdm/ValheimModManager/main/.env.example
cp .env.example .env
# Edit .env — at least:
#   MOD_MANAGER_SECRET_KEY          (long random string)
#   MOD_MANAGER_ADMIN_PASSWORD      (UI login for admin)
#   SERVER_PASS                     (Valheim join password)
# Optional: SUPERVISOR_HTTP_PASS, MOD_MANAGER_PORT, TIMEZONE, SERVER_NAME, …
docker compose pull
docker compose up -d
```

Then open http://localhost:8090 and sign in as `admin` with `MOD_MANAGER_ADMIN_PASSWORD`.

First boot with `BEPINEX=true` creates `config/bepinex`; the manager mounts that path. You do **not** need `--build` (images are on GHCR). Do **not** bind-mount `data/bepinex` into the manager — that pins the tree the Valheim image renames during BepInEx merge.

| Variable | Typical value |
|---|---|
| `MOD_MANAGER_SECRET_KEY` | Long random string (session/CSRF signing) |
| `MOD_MANAGER_ADMIN_PASSWORD` | UI login password for user `admin` |
| `SERVER_PASS` | Valheim join password |
| `SUPERVISOR_HTTP_PASS` | Optional; same value as manager `SUPERVISOR_PASSWORD` if set |
| `MOD_MANAGER_PORT` | Host port for the UI (default `8090`) |
| `TIMEZONE` | IANA zone for maintenance cron (default `America/Los_Angeles`) |

**Already running Valheim elsewhere?** Use this repo’s standalone `docker-compose.yml` + `.env.example`, point `VALHEIM_BEPINEX_PATH` / `VALHEIM_DOCKER_NETWORK` / `SUPERVISOR_URL` at that server, enable `SUPERVISOR_HTTP=true` on Valheim, then `docker compose pull && docker compose up -d`. See `docker-compose.merge.example.yml` for a sibling-service sketch.

**Updating** (after merges to `main` / `v*` tags):

```bash
docker compose pull
docker compose up -d
```

Pin with `image: ghcr.io/sonicdm/valheim-mod-manager:0.2.0` instead of `:latest` if you want a fixed release.

## How it fits the Valheim image

With `BEPINEX=true`, the server image keeps a persistent tree under `/config/bepinex` and syncs plugins/patchers into `/opt/valheim/bepinex/BepInEx/` during `valheim-bootstrap` (container start and BepInEx install/update). The manager:

1. Writes installs to the **config** mount (`.../config/bepinex`)
2. Applies changes by stopping `valheim-server`, running `valheim-bootstrap`, then starting `valheim-server` again — it does **not** bind-mount or write `data/bepinex`
3. Leaves the Valheim image and world saves alone

The Valheim image is the relocated home of the former [lloesche/valheim-server-docker](https://github.com/lloesche/valheim-server-docker) project (same layout and Supervisor ABI). Legacy `ghcr.io/lloesche/valheim-server` tags remain compatible until they disappear.

**Persistent packages** (Make persistent) need a Valheim-side hook — see below. The manager only talks to Supervisor; it cannot recreate live plugin symlinks itself.

Do **not** copy a full git working tree as-is (`.venv`, `node_modules`, and `data/` are machine-local). For production you only need Compose files + `.env` and pulled images.

### Persistent packages

Use **Make persistent** for mods with huge on-disk trees (WebMap tiles, asset packs, etc.). Layout:

| Path | Role |
|---|---|
| `config/bepinex/.persistent/<package>/` | Real files (DLL + runtime data) |
| `config/bepinex/plugins/<package>` | Symlink → `/config/bepinex/.persistent/<package>` |

Bootstrap still syncs `plugins/` into `/opt/valheim/bepinex/BepInEx/plugins/`. On many hosts (especially **Windows/SMB** volumes) that config-side symlink is not a valid Linux link inside the Valheim container, so sync treats the package as missing and removes it from the live tree. Restart + sync cannot fix that: the manager only has Supervisor (`stop` / `valheim-bootstrap` / `start`), not a shell in the game container.

**Required on the Valheim service** — recreate a Linux symlink for every `.persistent` package after each BepInEx config/sync:

```yaml
# Compose: $$ becomes $ inside the container
- POST_BEPINEX_CONFIG_HOOK=for d in /config/bepinex/.persistent/*/; do [ -d "$$d" ] || continue; name=$$(basename "$$d"); ln -sfn "/config/bepinex/.persistent/$$name" "/opt/valheim/bepinex/BepInEx/plugins/$$name"; done
```

This is included in `docker-compose.combined.example.yml`. If Valheim already runs in another compose file, add the same env var there (see `docker-compose.merge.example.yml`). Do **not** bind-mount onto `data/bepinex/.../plugins/...` — BepInEx merge renames that tree.

## Combined Compose reference

Full YAML for the combined stack (same as `docker-compose.combined.example.yml`). Both services use published images — Valheim from CVT, manager from `ghcr.io/sonicdm/valheim-mod-manager`.

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
      # Relink .persistent packages after bootstrap (see Persistent packages)
      - POST_BEPINEX_CONFIG_HOOK=for d in /config/bepinex/.persistent/*/; do [ -d "$$d" ] || continue; name=$$(basename "$$d"); ln -sfn "/config/bepinex/.persistent/$$name" "/opt/valheim/bepinex/BepInEx/plugins/$$name"; done
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

On launch the manager runs a **one-shot plugin scan** (and refreshes package indexes) in the background. Persistent manager state lives in the `mod_manager_data` volume.

If Valheim already runs in another compose project, use the standalone `docker-compose.yml` plus an external network — see [Quick start](#quick-start).

## Development

Prefer pulled images for running a server. To hack on the manager, uncomment `build: .` next to the `image:` line in compose and run `docker compose up -d --build`, or run the API/UI locally:

```bash
# Backend
cd backend
python -m venv .venv
# Windows: .\.venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
# Local sandbox (do not point at a live server tree while developing):
#   example_mods/bepinex  — untracked fake BepInEx root
#   data-dev/             — untracked SQLite + caches
# Copy .env.dev or export:
export DATA_DIR=../data-dev
export BEPINEX_ROOT=../example_mods/bepinex
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
