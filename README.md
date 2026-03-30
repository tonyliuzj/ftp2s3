# ftp2s3

`ftp2s3` exposes a simplified S3-like API on top of multiple FTP servers. Files are stored on FTP, while PostgreSQL stores indexed metadata for admin browsing, search, and sync/reconciliation.

## What Version 1 Includes

- FastAPI backend with a simplified S3-compatible API
- Multiple zones, where each zone can pool multiple FTP servers
- Zone strategies for `fill_first`, `round_robin`, or `mirror_all`
- Buckets mapped to zones using `bucket -> zone -> FTP server pool`
- Bucket region metadata for S3-style clients
- Multiple database-managed S3 access keys
- Managed region catalog used by bucket creation and default S3 settings
- Editable S3/public-link settings in the admin panel
- Static admin panel built with HTML, CSS, and vanilla JavaScript only
- PostgreSQL-backed metadata and admin state
- Sync/reconciliation tools that treat FTP as the source of truth
- Session-based admin authentication with hashed passwords
- Streaming-style uploads/downloads using temporary file objects
- Dockerfile, env-based config, and basic logging

## Architecture

### Storage mapping

- A `zone` stores one or more FTP servers.
- A zone can use `fill_first`, `round_robin`, or `mirror_all`.
- A `bucket` belongs to one `zone`.
- A `bucket` also carries a `region`.
- An `object` belongs to a bucket and zone.
- The object key maps to an FTP path by joining `bucket.base_dir + object_key`.
- The DB tracks per-object replica placements so pooled and mirrored-in-zone copies can be located later.

Example:

- Bucket: `photos`
- Bucket base dir: `/storage/photos`
- Object key: `2026/03/cat.jpg`
- FTP path: `/storage/photos/2026/03/cat.jpg`

### Bucket and Region Validation

Bucket names are validated in the API and admin UI to match DNS-style S3 naming rules:

- 3 to 63 characters
- lowercase letters, numbers, hyphens (`-`), and dots (`.`) only
- must start and end with a letter or number
- no consecutive dots
- no dot-hyphen or hyphen-dot combinations
- cannot look like an IPv4 address

Regions are validated as S3-style identifiers:

- 3 to 32 characters
- lowercase letters, numbers, and hyphens only
- must start and end with a letter or number

### Source of truth split

- FTP stores the real file bytes.
- The database stores searchable metadata:
  - bucket
  - zone
  - object key
  - FTP path
  - file size
  - last modified

The database is an index, not the canonical file store.

## Simplified S3 API

These routes are implemented:

- `GET /` lists buckets
- `GET /{bucket}` lists objects in a bucket
- `GET /{bucket}?location` returns the bucket region
- `PUT /{bucket}/{object_path}` uploads an object
- `GET /{bucket}/{object_path}` downloads an object
- `DELETE /{bucket}/{object_path}` deletes an object
- `HEAD /{bucket}` checks bucket existence
- `HEAD /{bucket}/{object_path}` checks object existence

This is still intentionally not full S3:

- It now supports SigV4-style header authentication using any enabled managed access key
- It now supports multiple managed access keys plus S3-style presigned query URLs with `X-Amz-*` parameters
- No S3 XML payloads
- JSON responses are returned for easier learning
- No multipart upload flow
- No object versioning, ACLs, or bucket policies

Because responses are still JSON rather than S3 XML, many off-the-shelf AWS SDK calls will still expect more protocol compatibility than this project currently implements.

## Admin APIs

### Auth

- `POST /admin/login`
- `POST /admin/logout`
- `GET /admin/me`

### Zones

- `GET /admin/zones`
- `POST /admin/zones`
- `PUT /admin/zones/{id}`
- `DELETE /admin/zones/{id}`

### Regions

- `GET /admin/regions`
- `POST /admin/regions`
- `PUT /admin/regions/{id}`
- `DELETE /admin/regions/{id}`

### Buckets

- `GET /admin/buckets`
- `POST /admin/buckets`
- `PUT /admin/buckets/{id}`
- `DELETE /admin/buckets/{id}`

### Objects

- `GET /admin/buckets/{bucket}/objects`
- `GET /admin/buckets/{bucket}/search?q=...`
- `POST /admin/buckets/{bucket}/upload`
- `DELETE /admin/buckets/{bucket}/objects/{path}`
- `GET /admin/buckets/{bucket}/download/{path}`
- `POST /admin/buckets/{bucket}/presign`

### Settings

- `GET /admin/settings`
- `PUT /admin/settings`

### Keys

- `GET /admin/keys`
- `POST /admin/keys`
- `PUT /admin/keys/{id}`
- `POST /admin/keys/{id}/rotate`
- `DELETE /admin/keys/{id}`

### Sync

- `GET /admin/buckets/{bucket}/sync/preview`
- `POST /admin/buckets/{bucket}/sync/repair`
- `GET /admin/buckets/{bucket}/sync/status`
- `GET /admin/buckets/{bucket}/zone-sync/preview`
- `POST /admin/buckets/{bucket}/zone-sync/repair`
- `POST /admin/sync/rescan-all`

## Admin Panel

The static admin panel is mounted at `/panel`.

Pages:

- Login
- Dashboard
- Settings
- Regions
- Keys
- Zones management
- Buckets management
- File browser with integrated search

Sync tools, zone sync, and system status are now part of the Settings page.

No Jinja2 templates are used. Every page is a static HTML file that calls the backend with `fetch()`.

## Database Schema

### `zones`

- `id`
- `name`
- `ftp_host`
- `ftp_port`
- `ftp_username`
- `ftp_password`
- `pool_strategy`
- `pool_cursor`
- `enabled`
- `created_at`
- `updated_at`

### `zone_servers`

- `id`
- `zone_id`
- `name`
- `ftp_host`
- `ftp_port`
- `ftp_username`
- `ftp_password`
- `enabled`
- `sort_order`
- `capacity_bytes`
- `created_at`
- `updated_at`

### `buckets`

- `id`
- `name`
- `zone_id`
- `base_dir`
- `region`
- `enabled`
- `created_at`
- `updated_at`

### `regions`

- `id`
- `code`
- `name`
- `created_at`
- `updated_at`

### `objects`

- `id`
- `bucket_id`
- `zone_id`
- `object_key`
- `ftp_path`
- `size`
- `last_modified`
- `created_at`
- `updated_at`

### `object_replicas`

- `id`
- `object_id`
- `zone_id`
- `zone_server_id`
- `ftp_path`
- `is_primary`
- `created_at`
- `updated_at`

### `admin_users`

- `id`
- `username`
- `password_hash`
- `created_at`

### `app_settings`

- `key`
- `value`
- `updated_at`

### `s3_access_keys`

- `id`
- `name`
- `access_key_id`
- `secret_access_key`
- `enabled`
- `is_default`
- `last_used_at`
- `created_at`
- `updated_at`

## How Sync Avoids Drift

### Normal writes

- Uploads write to FTP first, then write or update the DB row.
- Deletes remove the file from FTP first, then remove the DB row.
- Object keys are normalized before any path is built.
- Path traversal such as `../` is rejected before the FTP path is generated.

### Why this reduces drift

- FTP-first writes mean the file store is updated before the index claims success.
- If the DB update fails after an upload, the app tries to roll back by deleting the just-uploaded FTP file.
- If a DB row survives after a failed delete, the Settings page sync tools can remove the stale row because FTP is treated as the source of truth.

### Reconciliation rules

For a selected bucket:

1. The app connects to the correct FTP server for that bucket's zone.
2. It scans files under the bucket's base directory.
3. It derives object keys from FTP paths.
4. It compares FTP files with indexed DB rows.
5. It reports:
   - FTP-only files
   - DB-only rows
   - path mismatches
   - optional size mismatches

Repair behavior in v1:

- If FTP has a file and DB is missing it, insert the DB row.
- If FTP has a file and DB metadata is wrong, update the existing DB row when possible.
- If DB has a row but FTP no longer has the file, delete the stale DB row.

This keeps the database aligned with the actual FTP file tree over time.

## Zone Pool Strategies

- A zone can contain multiple FTP servers.
- `fill_first` keeps writing to the first enabled server until it is considered full, then moves to the next one.
- `round_robin` rotates uploads across enabled servers in order.
- `mirror_all` writes the same FTP path to every enabled server inside the zone.
- Optional `capacity_bytes` values let the app estimate when a pooled server should be skipped before upload.
- The Zone Sync tool in Settings compares the selected zone strategy against actual FTP files and repairs missing copies or stale replica metadata inside that zone.

## Project Layout

```text
app/
  api/
    admin.py
    s3.py
  services/
    ftp_storage.py
    storage_manager.py
    sync_service.py
  static/
    css/
    js/
    pages/
  bootstrap.py
  config.py
  database.py
  dependencies.py
  http_helpers.py
  main.py
  models.py
  pathing.py
  schemas.py
  security.py
```

## Local Run

1. Create a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Copy `.env.example` to `.env` and update values if needed.
4. Start PostgreSQL and create the database named in `DATABASE_URL`.
5. Start the app:

```bash
uvicorn app.main:app --reload
```

6. Open:

- Admin panel: `http://localhost:8000/panel/pages/login.html`
- API docs: `http://localhost:8000/docs`
- Health check: `http://localhost:8000/health`

## S3 Connection Details

To connect an S3-style client, you now need:

- Endpoint URL: `PUBLIC_BASE_URL` or your server URL, for example `http://localhost:8000`
- Bucket name: the bucket you created in the admin panel
- Region: the bucket's `region` field, for example `us-east-1`
- Access key ID: any enabled key from the new Keys page
- Secret access key: the secret paired with that access key ID

This implementation is path-style only, so the bucket is part of the URL path, not the hostname. Direct links follow the general S3 presign shape:

```text
https://your-endpoint.example.com/bucket-name/path/to/file.jpg?X-Amz-Algorithm=...&X-Amz-Credential=...&X-Amz-Date=...&X-Amz-Expires=...&X-Amz-SignedHeaders=host&X-Amz-Signature=...
```

The environment `S3_ACCESS_KEY_ID` and `S3_SECRET_ACCESS_KEY` are now bootstrap values. On first startup they seed the initial key in the database, and after that you can add or rotate keys from the Keys page. Regions are also seeded into the database from the default region setting and any existing bucket regions, then managed from the Regions page.

Example environment settings:

```env
PUBLIC_BASE_URL=http://localhost:8000
S3_DEFAULT_REGION=us-east-1
S3_ACCESS_KEY_ID=ftp2s3-access-key
S3_SECRET_ACCESS_KEY=ftp2s3-secret-key-change-me
```

Example path-style object URL:

```text
http://localhost:8000/my-bucket/path/to/file.txt
```

## Default Admin Login

On first startup, the app creates the default admin from environment variables:

- Username: `DEFAULT_ADMIN_USERNAME`
- Password: `DEFAULT_ADMIN_PASSWORD`

Change both for any real deployment.

## PostgreSQL Setup

Set `DATABASE_URL` in `.env` to a PostgreSQL SQLAlchemy URL, for example:

```env
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/ftp2s3
```

Create the database before starting the app. This project now expects PostgreSQL at runtime and no longer falls back to SQLite.
