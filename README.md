# Taste Safari CMS

Flask + SQLAlchemy admin CMS for the existing Taste Safari static site.

- Public site: `Taste-Safari/index.html` served at `/`
- Admin dashboard: `/admin` and `/admin/login`
- Admin APIs: `/api/admin/*`
- Public APIs: `/api/public/content`, `/api/adventures`, `/api/categories`, `/api/videos`, `/api/gallery`, `/api/collaborations`
- Health check: `/health`

## Local development

```cmd
cd "C:\Users\HP\Downloads\taste-safari-with-rajat (4)"
pip install -r requirements.txt
python app.py
```

Without `DATABASE_URL`, the app uses local `taste_safari.db` and local `uploads/` for development only.

## Production architecture

- GitHub: source repository
- Render Free: Flask/Gunicorn hosting
- Supabase Free: PostgreSQL database
- Cloudinary Free: images, thumbnails, and videos

Do not use Render PostgreSQL or Supabase Storage.

## Required environment variables

Set these in Render and locally for migration:

```text
FLASK_SECRET_KEY=<long random secret>
DATABASE_URL=postgresql://postgres.[PROJECT-REF]:[PASSWORD]@aws-0-[REGION].pooler.supabase.com:6543/postgres
CLOUDINARY_CLOUD_NAME=<cloud name>
CLOUDINARY_API_KEY=<api key>
CLOUDINARY_API_SECRET=<api secret>
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<strong password>
FLASK_SECURE_COOKIES=1
```

Optional:

```text
MAX_IMAGE_BYTES=12582912
MAX_VIDEO_BYTES=524288000
```

`ADMIN_PASSWORD` is only used when the `admins` table is empty. Existing password hashes are preserved.

## Supabase setup

1. Create a Supabase project.
2. Open **Project Settings → Database → Connection string**.
3. Use the transaction pooler connection string for Render, usually port `6543`.
4. Set `DATABASE_URL` in Render.
5. Tables are created automatically by SQLAlchemy on first request. For existing local data, run `python app.py migrate-sqlite`.

## Cloudinary setup

1. Create a Cloudinary product environment.
2. Copy cloud name, API key, and API secret.
3. Set the `CLOUDINARY_*` variables in Render.
4. Media is stored under `taste-safari/adventures`, `taste-safari/gallery`, `taste-safari/videos`, and `taste-safari/thumbnails`.

Large videos use signed browser-direct uploads. Images and thumbnails are uploaded server-side to Cloudinary.

## Migration commands

Run from the project root in Windows CMD after installing requirements and setting env vars.

SQLite to Supabase/Postgres:

```cmd
cd "C:\Users\HP\Downloads\taste-safari-with-rajat (4)"
pip install -r requirements.txt
set DATABASE_URL=postgresql://postgres.[PROJECT-REF]:[PASSWORD]@aws-0-[REGION].pooler.supabase.com:6543/postgres
python app.py migrate-sqlite
```

Existing local media to Cloudinary:

```cmd
set CLOUDINARY_CLOUD_NAME=your-cloud-name
set CLOUDINARY_API_KEY=your-api-key
set CLOUDINARY_API_SECRET=your-api-secret
python app.py migrate-media
```

`migrate-sqlite` preserves IDs, relationships, and admin password hashes. It skips rows already present in the target database. It does not delete `taste_safari.db` or `uploads/`.

`migrate-media` uploads local `uploads/` files to Cloudinary and updates DB references. It skips records already using Cloudinary or external URLs.

## GitHub upload

```cmd
cd "C:\Users\HP\Downloads\taste-safari-with-rajat (4)"
git init
git add .
git commit -m "Prepare Taste Safari for Render Supabase Cloudinary deployment"
git remote add origin https://github.com/YOUR-USER/YOUR-REPO.git
git push -u origin main
```

Confirm `.env`, `*.db`, and `uploads/` are not committed.

## Render deployment

1. Create a **Web Service** from the GitHub repo.
2. Build command: `pip install -r requirements.txt`
3. Start command: `gunicorn app:app`
4. Health check path: `/health`
5. Add all environment variables listed above.

Render Free sleeps after inactivity. Supabase Free can pause after inactivity. Cloudinary Free has shared monthly credits for storage, bandwidth, and transformations; large video bandwidth can exhaust the free allowance.

## Verification checklist

- `GET /health` returns `{"ok": true, "database": "up"}`.
- Admin login/logout works at `/admin/login` and `/admin`.
- Published adventures, categories, videos, and gallery load on the public site.
- Adventure/gallery image uploads work.
- Full video uploads use direct Cloudinary upload when configured.
- External YouTube/Instagram reels still open source links.
- Collaboration form submissions are stored in PostgreSQL.
