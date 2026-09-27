# Valheim Mod Manager

Self-hosted companion for an existing [lloesche/valheim-server](https://github.com/lloesche/valheim-server-docker) deployment. Discover, install, configure, update, and roll back BepInEx mods from **Thunderstore** and **Hexium** without replacing the game container or touching world saves.

## Screenshots

![Dashboard](docs/screenshots/dashboard.png)

| Discover | Installed |
| --- | --- |
| ![Discover](docs/screenshots/discover.png) | ![Installed](docs/screenshots/installed.png) |

| Package install | Config editor |
| --- | --- |
| ![Package](docs/screenshots/package.png) | ![Config](docs/screenshots/config.png) |

![Sign in](docs/screenshots/login.png)

## How it fits the vanilla image

With `BEPINEX=true`, lloesche keeps a persistent tree under `/config/bepinex` and syncs plugins/patchers into `/opt/valheim/bepinex/BepInEx/` during `valheim-bootstrap` (container start and BepInEx install/update). The manager:

1. Writes installs to the **config** mount (`.../config/bepinex`)
2. Applies changes by stopping `valheim-server`, running `valheim-bootstrap`, then starting `valheim-server` again — it does **not** bind-mount or write `data/bepinex`
3. Leaves the Valheim image and world saves alone

**Persistent packages** (bulky mods with many tiny files): use **Make persistent** on an installed row. Files move to `config/bepinex/.persistent/<package>` and `plugins/<package>` becomes a symlink to `/config/bepinex/.persistent/<package>`. Bootstrap's `rsync -a` copies that one symlink into the live tree without walking the files. Runtime data the mod writes follows the link and survives `data/bepinex` replaces.

Do **not** copy this working folder as-is (`.venv`, `node_modules`, and `data/` are machine-local).

## Quick start (standalone Compose)

1. Clone and configure paths for **your** host (vanilla layout shown):

```bash
git clone <your-repo-url> ValheimModManager
cd ValheimModManager
cp .env.example .env
```

Edit `.env`:

| Variable | Typical host path (lloesche) |
|---|---|
| `VALHEIM_BEPINEX_PATH` | `$HOME/valheim-server/config/bepinex` |
| `VALHEIM_DOCKER_NETWORK` | Network of the Valheim container (`docker network ls`) |
| `SUPERVISOR_URL` | `http://<valheim-container-name>:9001` |
| `MOD_MANAGER_PORT` | Host port for the UI (default `8090`) |

Do **not** mount `data/bepinex` or its `plugins`/`patchers` subfolders into the manager. Those binds pin the directory the Valheim image renames during BepInEx merge.

2. On the Valheim container, enable Supervisor HTTP if you want restart control: `SUPERVISOR_HTTP=true` (and optionally `SUPERVISOR_HTTP_PASS`). Use that same password as `SUPERVISOR_PASSWORD` here — not a misnamed `SUPERVISOR_PASS`.

3. Build and run:

```bash
docker compose up -d --build
```

4. Open http://localhost:8090 and sign in as `admin`.
5. Click **Scan plugins** on the dashboard.

Docker builds the Python env and frontend inside the image. Persistent manager state lives in the `mod_manager_data` volume, not in the repo.

## Merge into the Valheim compose later

Add a sibling service that mounts `./config/bepinex` (at both `/valheim/bepinex` and `/config/bepinex`) and talks to `http://valheim:9001` (or whatever your Valheim service/`--name` is). See `docker-compose.merge.example.yml`.

## Development

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
