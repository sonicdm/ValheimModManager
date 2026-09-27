# Valheim Mod Manager

Self-hosted companion for an existing [lloesche/valheim-server](https://github.com/lloesche/valheim-server-docker) deployment. Discover, install, configure, update, and roll back BepInEx mods from **Thunderstore** and **Hexium** without replacing the game container or touching world saves.

## Deploy to another machine

Do **not** copy this working folder as-is (`.venv`, `node_modules`, and `data/` are machine-local).

On the server:

```bash
git clone <your-repo-url> ValheimModManager
cd ValheimModManager
cp .env.example .env   # edit paths + secrets for that host
docker compose up -d --build
```

Docker builds the Python env and frontend inside the image. Persistent manager state lives in the `mod_manager_data` volume, not in the repo.

## Quick start (standalone Compose)

1. Copy `.env.example` to `.env` and set a strong `MOD_MANAGER_SECRET_KEY` and `MOD_MANAGER_ADMIN_PASSWORD`.
2. Confirm `VALHEIM_BEPINEX_PATH` points at your server’s `config/bepinex` directory.
3. Build and run:

```bash
docker compose up -d --build
```

4. Open http://localhost:8090 and sign in as `admin`.
5. Click **Scan plugins** on the dashboard.

Supervisor restart control is optional. Set `SUPERVISOR_URL` (default `http://host.docker.internal:9001`) and credentials. On the Valheim side, prefer `SUPERVISOR_HTTP_PASS` (the image’s real variable) over a misnamed `SUPERVISOR_PASS`.

## Merge into the Valheim compose later

Add a sibling service that mounts `./config/bepinex` and talks to `http://BabyGotBoar:9001`. See `docker-compose.yml` for the volume pattern.

## Development

```bash
# Backend
cd backend
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
$env:DATA_DIR="..\data"
$env:BEPINEX_ROOT="\\allan-pc\h\Games\Valheim Server\Docker\config\bepinex"
$env:ADMIN_PASSWORD="changeme"
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
