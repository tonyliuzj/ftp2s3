# ftp2s3

`ftp2s3` exposes a simplified S3-style API on top of one or more FTP servers. File bytes live on FTP, PostgreSQL stores all S3 and object metadata, and SQLite stores only local panel data such as admin logins and site settings.

## Local Run

1. Create a virtual environment.

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Install dependencies.

```bash
pip install -r requirements.txt
```

3. Copy the environment file.

```bash
cp .env.example .env
```

4. Adjust `.env` only for runtime basics if needed.

Example:

```env
APP_DATABASE_URL=sqlite:///./data/app.db
POSTGRES_HOST=localhost
POSTGRES_DB=ftp2s3
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
```

5. Start PostgreSQL.

6. Start the app.

```bash
uvicorn app.main:app --reload
```

7. Open the admin panel and complete first-run setup:

- Admin panel: `http://localhost:8000/panel/pages/login.html`
- API docs: `http://localhost:8000/docs`
- Health check: `http://localhost:8000/health`

On first visit, the panel redirects to `/panel/pages/setup.html` and asks for the PostgreSQL connection, the first admin account, the public base URL, and the initial S3 defaults/access key. `FTP_TIMEOUT` is managed later from the Settings page.

## Quick Installation (One-Click)

```bash
curl -sSL https://github.com/tonyliuzj/ftp2s3/releases/latest/download/ftp2s3.sh -o ftp2s3.sh && chmod +x ftp2s3.sh && bash ftp2s3.sh
```

The installer supports:

- Direct install: Python + `systemd`
- Docker install: app container via `docker-compose.yml`, with optional local PostgreSQL via `postgresql/docker-compose.yml`

## Docker Compose

For a manual Docker deployment:

1. Copy the environment file.

```bash
cp .env.example .env
```

2. Keep or adjust the Compose defaults in `.env`.

Example:

```env
APP_HOST_PORT=8000
POSTGRES_HOST=localhost
POSTGRES_DB=ftp2s3
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
```

3. Start the stack.

Use external PostgreSQL:

```bash
docker compose up -d --build
```

Use local Docker PostgreSQL:

```bash
docker compose -f docker-compose.yml -f postgresql/docker-compose.yml up -d --build
```

4. Open the admin panel and complete first-run setup:

- Admin panel: `http://localhost:8000/panel/pages/login.html`
- API docs: `http://localhost:8000/docs`

## Features

- FastAPI backend with a simplified S3-style API
- Multiple zones with pooled or mirrored FTP servers
- Bucket-to-zone mapping
- PostgreSQL-backed S3, bucket, zone, key, and object metadata
- SQLite-backed admin login and local site settings
- Managed access keys for S3-style auth
- Static admin panel built with HTML, CSS, and vanilla JavaScript
- Sync and reconciliation tools with FTP treated as the source of truth

## API Overview

Core S3-style routes:

- `GET /` lists buckets
- `GET /{bucket}` lists objects
- `GET /{bucket}?location` returns the bucket region
- `PUT /{bucket}/{object_path}` uploads an object
- `GET /{bucket}/{object_path}` downloads an object
- `DELETE /{bucket}/{object_path}` deletes an object
- `HEAD /{bucket}` checks bucket existence
- `HEAD /{bucket}/{object_path}` checks object existence

Admin routes include:

- auth
- zones
- regions
- buckets
- objects
- settings
- keys
- sync tools

This project is intentionally not a full S3 implementation. It is path-style only and returns JSON responses rather than S3 XML.

## First Run Setup

On first panel visit, `ftp2s3` redirects to the setup page instead of relying on env-defined admin credentials. The setup page stores:

- local site settings in SQLite
- the first admin user in SQLite
- PostgreSQL-backed object settings and access key defaults when PostgreSQL is reachable
- pending PostgreSQL-backed object settings in SQLite if PostgreSQL is temporarily unavailable

After setup, normal logins use the created admin account.

## S3 Connection Notes

To connect an S3-style client, you need:

- Endpoint URL: `PUBLIC_BASE_URL`
- Bucket name: a bucket created in the admin panel
- Region: the bucket `region`
- Access key ID: an enabled managed key
- Secret access key: the matching secret

Example object URL:

```text
http://localhost:8000/my-bucket/path/to/file.txt
```

S3 defaults and the initial access key are configured from the setup page.

## Project Layout

```text
app/
  api/
  services/
  static/
  bootstrap.py
  config.py
  database.py
  main.py
  models.py
  schemas.py
Dockerfile
docker-compose.yml
postgresql/docker-compose.yml
requirements.txt
ftp2s3.sh
```
