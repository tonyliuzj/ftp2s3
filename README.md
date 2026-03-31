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

4. Update `.env` with the local SQLite path and the PostgreSQL object metadata connection string.

Example:

```env
APP_DATABASE_URL=sqlite:///./data/app.db
OBJECT_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/ftp2s3
PUBLIC_BASE_URL=http://localhost:8000
DEFAULT_ADMIN_USERNAME=admin
DEFAULT_ADMIN_PASSWORD=admin123
```

5. Start PostgreSQL and create the database named in `OBJECT_DATABASE_URL`.

6. Start the app.

```bash
uvicorn app.main:app --reload
```

7. Open:

- Admin panel: `http://localhost:8000/panel/pages/login.html`
- API docs: `http://localhost:8000/docs`
- Health check: `http://localhost:8000/health`

## Quick Installation (One-Click)

```bash
curl -sSL https://github.com/tonyliuzj/ftp2s3/releases/latest/download/ftp2s3.sh -o ftp2s3.sh && chmod +x ftp2s3.sh && bash ftp2s3.sh
```

The installer supports:

- Direct install: Python + `systemd`
- Docker install: app container + separate PostgreSQL container via `docker-compose.yml`

## Docker Compose

For a manual Docker deployment:

1. Copy the environment file.

```bash
cp .env.example .env
```

2. Set the Docker-facing database URL in `.env`.

Example:

```env
APP_HOST_PORT=8000
APP_DATABASE_URL=sqlite:////app/data/app.db
OBJECT_DATABASE_URL=postgresql+psycopg://ftp2s3:change-me@postgres:5432/ftp2s3
POSTGRES_DB=ftp2s3
POSTGRES_USER=ftp2s3
POSTGRES_PASSWORD=change-me
PUBLIC_BASE_URL=http://localhost:8000
```

3. Start the stack.

```bash
docker compose up -d --build
```

4. Open:

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

## Default Admin Login

On first startup, the app creates the default admin from:

- `DEFAULT_ADMIN_USERNAME`
- `DEFAULT_ADMIN_PASSWORD`

Change both for real deployments.

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

Example bootstrap S3 settings:

```env
S3_DEFAULT_REGION=us-east-1
S3_ACCESS_KEY_ID=ftp2s3-access-key
S3_SECRET_ACCESS_KEY=ftp2s3-secret-key-change-me
```

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
requirements.txt
ftp2s3.sh
```
