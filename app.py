import base64
import hashlib
import hmac
import io
import logging
import mimetypes
import os
import re
import secrets
import shutil
import sqlite3
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, request, send_from_directory, session, url_for
from sqlalchemy import ForeignKey, Integer, String, Text, create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

try:
    import cloudinary
    import cloudinary.api
    import cloudinary.uploader
    from cloudinary.utils import cloudinary_url
except Exception:
    cloudinary = None
    cloudinary_url = None

def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)

BASE_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = BASE_DIR / "Taste-Safari"
UPLOAD_ROOT = BASE_DIR / "uploads"
DB_PATH = BASE_DIR / "taste_safari.db"
MAX_VIDEO_BYTES = int(os.environ.get("MAX_VIDEO_BYTES", str(500 * 1024 * 1024)))
MAX_IMAGE_BYTES = int(os.environ.get("MAX_IMAGE_BYTES", str(12 * 1024 * 1024)))
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://") and "+" not in DATABASE_URL.split("://", 1)[0]:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
CLOUDINARY_CLOUD_NAME = os.environ.get("CLOUDINARY_CLOUD_NAME", "")
CLOUDINARY_API_KEY = os.environ.get("CLOUDINARY_API_KEY", "")
CLOUDINARY_API_SECRET = os.environ.get("CLOUDINARY_API_SECRET", "")

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".m4v", ".ogv"}
VIDEO_MIMES = {"video/mp4", "video/webm", "video/quicktime", "video/x-m4v", "video/ogg"}
DIETARY_TYPES = ["Vegetarian", "Vegan", "Eggetarian", "Non-Vegetarian"]
CATEGORY_NAMES = [
    "Cafe", "Bistro", "Street Food", "Fine Dining", "Restaurant", "Bakery",
    "Desserts & Sweets", "Fast Food", "Food Court", "Traditional Food",
    "Drinks & Beverages", "Cloud Kitchen",
]

app = Flask(__name__, static_folder=None)
app.config.update(
    SECRET_KEY=os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32),
    MAX_CONTENT_LENGTH=MAX_VIDEO_BYTES + 20 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("FLASK_SECURE_COOKIES") == "1" or os.environ.get("RENDER") == "true",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 8,
    JSON_SORT_KEYS=False,
)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("taste_safari_admin")


class Base(DeclarativeBase):
    pass


class Admin(Base):
    __tablename__ = "admins"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Category(Base):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(140), unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    adventures = relationship("Adventure", back_populates="category")
    videos = relationship("Video", back_populates="category")


class Adventure(Base):
    __tablename__ = "adventures"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    location: Mapped[str] = mapped_column(String(180), nullable=False)
    place_name: Mapped[str] = mapped_column(String(180), nullable=False)
    rating: Mapped[float] = mapped_column(nullable=False)
    about_dish: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    recommendation: Mapped[str] = mapped_column(Text, default="")
    price_range: Mapped[str] = mapped_column(String(120), default="")
    timing: Mapped[str] = mapped_column(String(120), default="")
    dietary_type: Mapped[str] = mapped_column(String(80), default="Vegetarian")
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), nullable=False)
    reel_link: Mapped[str] = mapped_column(String(500), default="")
    photo_path: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(20), default="published")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    category = relationship("Category", back_populates="adventures")


class Video(Base):
    __tablename__ = "videos"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    video_type: Mapped[str] = mapped_column(String(30), nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), nullable=False)
    video_path: Mapped[str] = mapped_column(String(500), default="")
    reel_link: Mapped[str] = mapped_column(String(500), default="")
    thumbnail_path: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(20), default="published")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    category = relationship("Category", back_populates="videos")


class GalleryItem(Base):
    __tablename__ = "gallery"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    image_path: Mapped[str] = mapped_column(String(500), nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="published")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Collaboration(Base):
    __tablename__ = "collaborations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(140), nullable=False)
    email: Mapped[str] = mapped_column(String(180), nullable=False)
    phone: Mapped[str] = mapped_column(String(50), default="")
    brand: Mapped[str] = mapped_column(String(180), default="")
    collaboration_type: Mapped[str] = mapped_column(String(120), default="")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="new")
    submitted_at: Mapped[datetime] = mapped_column(default=utcnow)


def make_engine():
    url = DATABASE_URL or f"sqlite:///{DB_PATH}"
    kwargs = {"future": True, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs.update({"pool_size": 3, "max_overflow": 2, "pool_recycle": 280})
    eng = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    return eng


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base.metadata.create_all(engine)

if cloudinary and CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET:
    cloudinary.config(cloud_name=CLOUDINARY_CLOUD_NAME, api_key=CLOUDINARY_API_KEY, api_secret=CLOUDINARY_API_SECRET, secure=True)


def cloudinary_enabled():
    return bool(cloudinary and cloudinary_url and CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET)


def production_media_required():
    return bool(DATABASE_URL or os.environ.get("RENDER") == "true") and not cloudinary_enabled()


def db():
    return SessionLocal()


def clean(value, maximum=2000):
    return re.sub(r"\s+", " ", str(value or "")).strip()[:maximum]


def validate_url(value, kinds=("http", "https")):
    try:
        parsed = urllib.parse.urlparse(str(value or "").strip())
        return parsed.scheme in kinds and bool(parsed.netloc)
    except Exception:
        return False


def slugify(value):
    value = re.sub(r"[^a-zA-Z0-9]+", "-", str(value).strip().lower()).strip("-")
    return value or f"category-{secrets.token_hex(4)}"


def unique_slug(session, value, exclude_id=None):
    base = slugify(value)
    slug, suffix = base, 2
    while session.query(Category).filter(Category.slug == slug, Category.id != exclude_id).first():
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug


def to_json(row):
    if row is None:
        return None
    data = {column.name: getattr(row, column.name) for column in row.__table__.columns}
    for key, value in list(data.items()):
        if isinstance(value, datetime):
            data[key] = value.isoformat() + "Z"
    return data


def collection_json(items, fields):
    return [{field: getattr(item, field) for field in fields} for item in items]


def adventure_json(item):
    data = to_json(item)
    data["category_name"] = item.category.name if item.category else "Uncategorized"
    data["category_slug"] = item.category.slug if item.category else ""
    data["photo_url"] = media_url(item.photo_path, "image")
    data["reel_link"] = item.reel_link or ""
    return data


def category_json(item):
    data = to_json(item)
    data["adventure_count"] = len(item.adventures)
    data["video_count"] = len(item.videos)
    return data


def video_json(item):
    data = to_json(item)
    data["category_name"] = item.category.name if item.category else "Uncategorized"
    data["video_url"] = media_url(item.video_path, "video")
    data["thumbnail_url"] = media_url(item.thumbnail_path, "image")
    data["platform"] = video_platform(item.reel_link)
    data["kind"] = "external" if item.video_type == "external_reel" else "full_video"
    return data


def gallery_json(item):
    data = to_json(item)
    data["image_url"] = media_url(item.image_path, "image")
    return data


def collaboration_json(item):
    data = to_json(item)
    data["new_count"] = 0
    return data


def cloudinary_ref(public_id, resource_type, fmt=""):
    return f"cloudinary:{resource_type}:{public_id}:{fmt or ''}"


def parse_cloudinary_ref(ref):
    if not ref or not ref.startswith("cloudinary:"):
        return None
    parts = ref.split(":", 3)
    if len(parts) != 4:
        return None
    return {"resource_type": parts[1], "public_id": parts[2], "format": parts[3]}


def media_url(path, resource_type="image"):
    if not path:
        return ""
    parsed = parse_cloudinary_ref(path)
    if parsed:
        if not cloudinary_enabled():
            return ""
        kwargs = {"resource_type": parsed["resource_type"] or resource_type, "secure": True}
        if parsed["format"]:
            kwargs["format"] = parsed["format"]
        try:
            result = cloudinary_url(parsed["public_id"], **kwargs)
            return result[0] if isinstance(result, (tuple, list)) else result
        except Exception:
            return ""
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return url_for("uploaded_media", filename=path)


def video_platform(url):
    host = urllib.parse.urlparse(url or "").netloc.lower()
    if "youtube.com" in host or "youtu.be" in host:
        return "YouTube"
    if "instagram.com" in host:
        return "Instagram"
    return "External"


def detect_image(stream):
    head = stream.read(16)
    stream.seek(0)
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return ""


def detect_video(stream):
    head = stream.read(16)
    stream.seek(0)
    if head[4:8] == b"ftyp":
        return "mp4"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm"
    return ""


def cloudinary_public_id(folder):
    return f"taste-safari/{folder}/{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(8)}"


def validate_upload(file_obj, allowed, max_bytes, detector):
    if not file_obj or not file_obj.filename:
        raise ValueError("A file is required.")
    original = secure_filename(Path(file_obj.filename).name)
    if not original:
        raise ValueError("The uploaded filename is invalid.")
    suffix = Path(original).suffix.lower()
    if suffix not in allowed:
        raise ValueError("Unsupported file format.")
    mime = (file_obj.mimetype or "").lower()
    if suffix in IMAGE_EXTENSIONS and mime and not mime.startswith("image/"):
        raise ValueError("The uploaded file is not a valid image.")
    if suffix in VIDEO_EXTENSIONS and mime and not (mime.startswith("video/") or mime in VIDEO_MIMES):
        raise ValueError("The uploaded file is not a valid video.")
    head = file_obj.read(16)
    file_obj.seek(0)
    if not head or not detector(io.BytesIO(head)):
        raise ValueError("The file contents do not match a supported format.")
    return suffix


def cloudinary_upload_path(file_path, folder, resource_type):
    public_id = cloudinary_public_id(folder)
    result = cloudinary.uploader.upload(
        str(file_path), public_id=public_id, resource_type=resource_type,
        overwrite=False, invalidate=True,
    )
    return cloudinary_ref(result.get("public_id") or public_id, result.get("resource_type") or resource_type, result.get("format") or "")


def upload_file(file_obj, folder, allowed, max_bytes, detector, resource_type="image"):
    suffix = validate_upload(file_obj, allowed, max_bytes, detector)
    if cloudinary_enabled():
        stream = getattr(file_obj, "stream", file_obj)
        fd, tmp_name = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        written = 0
        try:
            with open(tmp_name, "wb") as output:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        raise ValueError("The uploaded file is too large.")
                    output.write(chunk)
            if written == 0:
                raise ValueError("The uploaded file is empty.")
            return cloudinary_upload_path(tmp_name, folder, resource_type)
        except ValueError:
            raise
        except Exception as error:
            raise ValueError(f"Cloudinary upload failed: {error}")
        finally:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
    if production_media_required():
        raise ValueError("Cloudinary media storage is not configured for production.")
    folder_path = UPLOAD_ROOT / folder
    folder_path.mkdir(parents=True, exist_ok=True)
    name = f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(8)}{suffix}"
    relative = f"{folder}/{name}"
    target = UPLOAD_ROOT / relative
    part = target.with_name(target.name + ".part")
    written = 0
    try:
        with part.open("wb") as output:
            while True:
                chunk = file_obj.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise ValueError("The uploaded file is too large.")
                output.write(chunk)
        if written == 0:
            raise ValueError("The uploaded file is empty.")
        part.replace(target)
    except Exception:
        if part.exists():
            part.unlink()
        raise
    return relative


def store_media_bytes(data, folder, suffix, resource_type="image"):
    if cloudinary_enabled():
        fd, tmp_name = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        try:
            Path(tmp_name).write_bytes(data)
            return cloudinary_upload_path(tmp_name, folder, resource_type)
        finally:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
    if production_media_required():
        raise ValueError("Cloudinary media storage is not configured for production.")
    folder_path = UPLOAD_ROOT / folder
    folder_path.mkdir(parents=True, exist_ok=True)
    name = f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(8)}{suffix}"
    target = folder_path / name
    target.write_bytes(data)
    return f"{folder}/{name}"


def store_media_file(source_path, folder, resource_type="image"):
    source = Path(source_path)
    suffix = source.suffix.lower() or (".jpg" if resource_type == "image" else ".mp4")
    if cloudinary_enabled():
        return cloudinary_upload_path(source, folder, resource_type)
    if production_media_required():
        raise ValueError("Cloudinary media storage is not configured for production.")
    folder_path = UPLOAD_ROOT / folder
    folder_path.mkdir(parents=True, exist_ok=True)
    name = f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(8)}{suffix}"
    target = folder_path / name
    shutil.copyfile(source, target)
    return f"{folder}/{name}"


def cloudinary_ref_for_public_id(public_id, resource_type, max_bytes=None):
    if not cloudinary_enabled():
        raise ValueError("Cloudinary media storage is not configured.")
    public_id = clean(public_id, 500)
    if not public_id.startswith("taste-safari/"):
        raise ValueError("Invalid Cloudinary asset.")
    try:
        resource = cloudinary.api.resource(public_id, resource_type=resource_type)
    except Exception as error:
        raise ValueError(f"Uploaded media could not be verified: {error}")
    if resource.get("resource_type") and resource.get("resource_type") != resource_type:
        raise ValueError("Uploaded media type does not match.")
    size = int(resource.get("bytes") or 0)
    if max_bytes and size > max_bytes:
        raise ValueError("The uploaded file is too large.")
    return cloudinary_ref(public_id, resource_type, resource.get("format") or "")


def delete_upload(ref):
    if not ref:
        return
    parsed = parse_cloudinary_ref(ref)
    if parsed:
        if cloudinary_enabled():
            try:
                cloudinary.uploader.destroy(parsed["public_id"], resource_type=parsed["resource_type"] or "image")
            except Exception as error:
                log.info("Cloudinary delete failed for %s: %s", parsed["public_id"], error)
        return
    target = (UPLOAD_ROOT / ref).resolve()
    root = UPLOAD_ROOT.resolve()
    if root in target.parents and target.exists():
        target.unlink()


def public_adventure(item):
    category = item.category
    return {
        "id": str(item.id),
        "title": item.place_name,
        "location": item.location,
        "category": category.slug if category else "",
        "categoryLabel": category.name if category else "Uncategorized",
        "price": item.price_range or "Price on request",
        "rating": float(item.rating),
        "diet": item.dietary_type or "Vegetarian",
        "time": item.timing or "Timings vary",
        "featured": float(item.rating) >= 9.5,
        "tags": [category.name] if category else [],
        "image": media_url(item.photo_path, "image"),
        "instaReel": item.reel_link or "",
        "description": item.about_dish,
        "tastingNotes": "\n\n".join(part for part in [item.notes, item.recommendation] if part),
        "shareText": f"Check out {item.place_name} in {item.location}, rated {float(item.rating):.1f}/10 on Taste Safari with Rajat!",
        "aboutDish": item.about_dish,
        "notes": item.notes,
        "recommendation": item.recommendation,
    }


def public_video(item):
    return {
        "id": item.id,
        "video_type": item.video_type,
        "title": item.title,
        "description": item.description,
        "category": item.category.slug if item.category else "",
        "category_name": item.category.name if item.category else "Uncategorized",
        "video_url": media_url(item.video_path, "video"),
        "thumbnail_url": media_url(item.thumbnail_path, "image"),
        "reel_link": item.reel_link or "",
        "platform": video_platform(item.reel_link),
        "kind": "external" if item.video_type == "external_reel" else "full_video",
    }


def public_gallery(item):
    return {
        "id": str(item.id),
        "title": item.title,
        "location": "",
        "caption": item.description,
        "image": media_url(item.image_path, "image"),
        "description": item.description,
    }


def require_admin():
    if not session.get("admin_id"):
        abort(401, description="Authentication required.")


def require_json():
    if not request.is_json:
        raise ValueError("JSON body is required.")


def csrf_check():
    token = request.headers.get("X-CSRF-Token") or (request.get_json(silent=True) or {}).get("csrf_token")
    if not token or not hmac.compare_digest(str(token), str(session.get("csrf_token", ""))):
        abort(403, description="Invalid CSRF token.")


@app.route("/api/admin/uploads/signature", methods=["POST"])
def admin_upload_signature():
    require_admin()
    csrf_check()
    if not cloudinary_enabled():
        return fail("Cloudinary media storage is not configured.", 503)
    data = request.get_json(silent=True) or {}
    resource_type = clean(data.get("resource_type"), 20)
    folder = clean(data.get("folder"), 40)
    allowed = {"image": {"adventures", "gallery", "thumbnails"}, "video": {"videos"}}
    if resource_type not in allowed or folder not in allowed[resource_type]:
        return fail("Unsupported upload type.", 400)
    public_id = cloudinary_public_id(folder)
    timestamp = int(time.time())
    params = {"public_id": public_id, "timestamp": timestamp}
    signature = hashlib.sha1(("&".join(f"{key}={params[key]}" for key in sorted(params)) + CLOUDINARY_API_SECRET).encode("utf-8")).hexdigest()
    return ok({
        "upload_url": f"https://api.cloudinary.com/v1_1/{CLOUDINARY_CLOUD_NAME}/{resource_type}/upload",
        "cloud_name": CLOUDINARY_CLOUD_NAME,
        "api_key": CLOUDINARY_API_KEY,
        "timestamp": timestamp,
        "signature": signature,
        "public_id": public_id,
        "resource_type": resource_type,
        "folder": folder,
    })


@app.route("/api/admin/uploads/verify", methods=["POST"])
def admin_upload_verify():
    require_admin()
    csrf_check()
    data = request.get_json(silent=True) or {}
    public_id = clean(data.get("public_id"), 500)
    resource_type = clean(data.get("resource_type"), 20)
    if resource_type not in {"image", "video"}:
        return fail("Unsupported media type.", 400)
    try:
        ref = cloudinary_ref_for_public_id(public_id, resource_type, MAX_IMAGE_BYTES if resource_type == "image" else MAX_VIDEO_BYTES)
    except ValueError as error:
        return fail(str(error), 400)
    return ok({"public_id": public_id, "resource_type": resource_type, "ref": ref, "url": media_url(ref, resource_type)})


def paginate(query, serializer):
    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(100, max(1, int(request.args.get("per_page", 20))))
    except (TypeError, ValueError):
        page, per_page = 1, 20
    total = query.count()
    items = query.offset((page - 1) * per_page).limit(per_page).all()
    return jsonify({"items": [serializer(item) for item in items], "page": page, "per_page": per_page, "total": total})


def ok(data=None, status=200):
    payload = {"ok": True}
    if data is not None:
        payload.update(data if isinstance(data, dict) else {"data": data})
    return jsonify(payload), status


def fail(message, status=400):
    return jsonify({"ok": False, "error": message}), status


@app.errorhandler(HTTPException)
def http_error(error):
    return jsonify({"ok": False, "error": error.description or error.name}), error.code


@app.errorhandler(Exception)
def unexpected_error(error):
    log.exception("Unhandled application error")
    return jsonify({"ok": False, "error": "An unexpected server error occurred."}), 500


@app.route("/")
def index():
    return send_from_directory(PUBLIC_DIR, "index.html")


@app.route("/health")
def health():
    try:
        session_db = db()
        session_db.execute(text("SELECT 1"))
        session_db.close()
        return ok({"status": "ok", "database": "up"})
    except Exception as error:
        log.warning("Health check failed: %s", error)
        return jsonify({"ok": False, "error": "Database unavailable"}), 503


@app.route("/assets/<path:filename>")
def public_assets(filename):
    return send_from_directory(PUBLIC_DIR / "assets", filename)


@app.route("/uploads/<path:filename>")
def uploaded_media(filename):
    return send_from_directory(UPLOAD_ROOT, filename)


@app.route("/admin")
@app.route("/admin/")
def admin_page():
    return send_from_directory(BASE_DIR, "admin.html")


@app.route("/admin/login")
def admin_login_page():
    return send_from_directory(BASE_DIR, "admin.html")


@app.route("/admin.css")
def admin_css():
    return send_from_directory(BASE_DIR, "admin.css")


@app.route("/admin.js")
def admin_js():
    return send_from_directory(BASE_DIR, "admin.js")


@app.route("/api/admin/session", methods=["GET"])
def admin_session():
    return ok({"authenticated": bool(session.get("admin_id")), "csrf_token": session.get("csrf_token", ""), "cloudinary": cloudinary_enabled()})


@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    data = request.get_json(silent=True) or {}
    username = clean(data.get("username"), 80)
    password = str(data.get("password") or "")
    if not username or not password:
        return fail("Username and password are required.", 400)
    session_db = db()
    try:
        admin = session_db.query(Admin).filter_by(username=username).first()
        if not admin or not check_password_hash(admin.password_hash, password):
            return fail("Invalid username or password.", 401)
        session.clear()
        session["admin_id"] = admin.id
        session["csrf_token"] = secrets.token_urlsafe(32)
        session.permanent = True
        return ok({"authenticated": True, "csrf_token": session["csrf_token"]})
    finally:
        session_db.close()


@app.route("/api/admin/logout", methods=["POST"])
def admin_logout():
    session.clear()
    return ok({"authenticated": False})


@app.route("/api/public/content", methods=["GET"])
def public_content():
    session_db = db()
    try:
        adventures = session_db.query(Adventure).filter_by(status="published").order_by(Adventure.rating.desc(), Adventure.created_at.desc()).all()
        videos = session_db.query(Video).filter_by(status="published").order_by(Video.created_at.desc()).all()
        gallery = session_db.query(GalleryItem).filter_by(status="published").order_by(GalleryItem.display_order.asc(), GalleryItem.created_at.desc()).all()
        categories = session_db.query(Category).order_by(Category.name).all()
        return ok({
            "adventures": [public_adventure(item) for item in adventures],
            "videos": [public_video(item) for item in videos],
            "gallery": [public_gallery(item) for item in gallery],
            "categories": collection_json(categories, ["id", "name", "slug", "created_at"]),
        })
    finally:
        session_db.close()


@app.route("/api/adventures", methods=["GET"])
def list_adventures_public():
    session_db = db()
    try:
        query = session_db.query(Adventure).filter_by(status="published")
        category = request.args.get("category")
        if category:
            query = query.join(Category).filter(Category.slug == category)
        return ok({"items": [public_adventure(item) for item in query.order_by(Adventure.rating.desc()).all()]})
    finally:
        session_db.close()


@app.route("/api/categories", methods=["GET"])
def list_categories_public():
    session_db = db()
    try:
        return ok({"items": collection_json(session_db.query(Category).order_by(Category.name).all(), ["id", "name", "slug", "created_at"])})
    finally:
        session_db.close()


@app.route("/api/videos", methods=["GET"])
def list_videos_public():
    session_db = db()
    try:
        return ok({"items": [public_video(item) for item in session_db.query(Video).filter_by(status="published").order_by(Video.created_at.desc()).all()]})
    finally:
        session_db.close()


@app.route("/api/gallery", methods=["GET"])
def list_gallery_public():
    session_db = db()
    try:
        items = session_db.query(GalleryItem).filter_by(status="published").order_by(GalleryItem.display_order, GalleryItem.created_at).all()
        return ok({"items": [public_gallery(item) for item in items]})
    finally:
        session_db.close()


@app.route("/api/collaborations", methods=["POST"])
def create_collaboration():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return fail("A valid JSON body is required.", 400)
    name = clean(data.get("name"), 140)
    email = clean(data.get("email"), 180)
    message = clean(data.get("message"), 600)
    phone = clean(data.get("phone"), 50)
    brand = clean(data.get("brand"), 180)
    collaboration_type = clean(data.get("collaboration_type") or data.get("type"), 120)
    honeypot = clean(data.get("website"), 100)
    if honeypot:
        return ok({"message": "Your enquiry has been received."}, 201)
    if len(name) < 2 or not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email) or len(message) < 10:
        return fail("Please provide a valid name, email and message.", 400)
    now = time.time()
    last_submit = float(session.get("collab_last_submit") or 0)
    if now - last_submit < 30:
        return fail("Please wait a moment before submitting another enquiry.", 429)
    session_db = db()
    try:
        item = Collaboration(name=name, email=email, phone=phone, brand=brand, collaboration_type=collaboration_type, message=message)
        session_db.add(item)
        session_db.commit()
        session["collab_last_submit"] = now
        return ok({"message": "Your enquiry has been received."}, 201)
    finally:
        session_db.close()


@app.route("/api/admin/stats", methods=["GET"])
def admin_stats():
    require_admin()
    session_db = db()
    try:
        stats = {
            "adventures": session_db.query(Adventure).count(),
            "published_adventures": session_db.query(Adventure).filter_by(status="published").count(),
            "videos": session_db.query(Video).count(),
            "published_videos": session_db.query(Video).filter_by(status="published").count(),
            "gallery": session_db.query(GalleryItem).count(),
            "published_gallery": session_db.query(GalleryItem).filter_by(status="published").count(),
            "collaborations": session_db.query(Collaboration).count(),
            "new_collaborations": session_db.query(Collaboration).filter_by(status="new").count(),
            "categories": session_db.query(Category).count(),
        }
        return ok(stats)
    finally:
        session_db.close()


@app.route("/api/admin/recent-activity", methods=["GET"])
def admin_recent_activity():
    require_admin()
    session_db = db()
    try:
        adventures = session_db.query(Adventure).order_by(Adventure.created_at.desc()).limit(5).all()
        videos = session_db.query(Video).order_by(Video.created_at.desc()).limit(5).all()
        gallery = session_db.query(GalleryItem).order_by(GalleryItem.created_at.desc()).limit(5).all()
        collaborations = session_db.query(Collaboration).order_by(Collaboration.submitted_at.desc()).limit(5).all()
        return ok({
            "adventures": collection_json(adventures, ["id", "place_name", "location", "rating", "status", "created_at"]),
            "videos": collection_json(videos, ["id", "title", "video_type", "status", "created_at"]),
            "gallery": collection_json(gallery, ["id", "title", "status", "created_at"]),
            "collaborations": collection_json(collaborations, ["id", "name", "email", "collaboration_type", "status", "submitted_at"]),
        })
    finally:
        session_db.close()


@app.route("/api/admin/adventures", methods=["GET"])
def admin_list_adventures():
    require_admin()
    csrf_check() if request.method not in ("GET", "HEAD", "OPTIONS") else None
    session_db = db()
    try:
        query = session_db.query(Adventure)
        search = clean(request.args.get("search"), 120)
        category_id = request.args.get("category_id", type=int)
        status = request.args.get("status")
        sort = request.args.get("sort", "newest")
        if search:
            like = f"%{search}%"
            query = query.filter((Adventure.place_name.like(like)) | (Adventure.location.like(like)) | (Adventure.about_dish.like(like)))
        if category_id:
            query = query.filter(Adventure.category_id == category_id)
        if status in {"published", "draft"}:
            query = query.filter(Adventure.status == status)
        if sort == "rating":
            query = query.order_by(Adventure.rating.desc(), Adventure.created_at.desc())
        else:
            query = query.order_by(Adventure.created_at.desc())
        return paginate(query, adventure_json)
    finally:
        session_db.close()


@app.route("/api/admin/adventures/<int:item_id>", methods=["GET"])
def admin_get_adventure(item_id):
    require_admin()
    session_db = db()
    try:
        item = session_db.get(Adventure, item_id)
        if not item:
            return fail("Adventure not found.", 404)
        return ok(adventure_json(item))
    finally:
        session_db.close()


@app.route("/api/admin/adventures", methods=["POST"])
def admin_create_adventure():
    require_admin()
    csrf_check()
    session_db = db()
    old_paths = []
    try:
        data = request.form.to_dict() if request.form else (request.get_json(silent=True) or {})
        category_id = int(data.get("category_id") or 0)
        category = session_db.get(Category, category_id)
        if not category:
            return fail("A valid category is required.", 400)
        rating = float(data.get("rating") or 0)
        if rating < 0 or rating > 10:
            return fail("Rating must be between 0 and 10.", 400)
        dietary = clean(data.get("dietary_type"), 80)
        if dietary not in DIETARY_TYPES:
            return fail("Dietary type is invalid.", 400)
        reel_link = clean(data.get("reel_link"), 500)
        if reel_link and not validate_url(reel_link):
            return fail("Reel link must be a valid HTTP or HTTPS URL.", 400)
        photo_path = ""
        if "photo" in request.files and request.files["photo"].filename:
            photo_path = upload_file(request.files["photo"], "adventures", IMAGE_EXTENSIONS, MAX_IMAGE_BYTES, detect_image)
            old_paths.append(photo_path)
        item = Adventure(
            location=clean(data.get("location"), 180), place_name=clean(data.get("place_name"), 180),
            rating=rating, about_dish=clean(data.get("about_dish"), 5000), notes=clean(data.get("notes"), 3000),
            recommendation=clean(data.get("recommendation"), 3000), price_range=clean(data.get("price_range"), 120),
            timing=clean(data.get("timing"), 120), dietary_type=dietary, category_id=category.id,
            reel_link=reel_link, photo_path=photo_path, status="published" if data.get("status", "published") == "published" else "draft",
        )
        if not item.location or not item.place_name or not item.about_dish:
            delete_upload(photo_path)
            return fail("Location, place name and about the dish are required.", 400)
        session_db.add(item)
        session_db.commit()
        return ok(adventure_json(item), 201)
    except (ValueError, TypeError) as error:
        session_db.rollback()
        for path in old_paths:
            delete_upload(path)
        return fail(str(error), 400)
    finally:
        session_db.close()


@app.route("/api/admin/adventures/<int:item_id>", methods=["PUT"])
def admin_update_adventure(item_id):
    require_admin()
    csrf_check()
    session_db = db()
    old_path = None
    new_photo_path = ""
    try:
        item = session_db.get(Adventure, item_id)
        if not item:
            return fail("Adventure not found.", 404)
        data = request.get_json(silent=True) or request.form.to_dict()
        category_id = int(data.get("category_id") or item.category_id)
        if not session_db.get(Category, category_id):
            return fail("A valid category is required.", 400)
        rating = float(data.get("rating", item.rating))
        if rating < 0 or rating > 10:
            return fail("Rating must be between 0 and 10.", 400)
        dietary = clean(data.get("dietary_type", item.dietary_type), 80)
        if dietary not in DIETARY_TYPES:
            return fail("Dietary type is invalid.", 400)
        reel_link = clean(data.get("reel_link", item.reel_link), 500)
        if reel_link and not validate_url(reel_link):
            return fail("Reel link must be a valid HTTP or HTTPS URL.", 400)
        old_path = item.photo_path
        if "photo" in request.files and request.files["photo"].filename:
            new_photo_path = upload_file(request.files["photo"], "adventures", IMAGE_EXTENSIONS, MAX_IMAGE_BYTES, detect_image)
            item.photo_path = new_photo_path
        item.location = clean(data.get("location", item.location), 180)
        item.place_name = clean(data.get("place_name", item.place_name), 180)
        item.rating = rating
        item.about_dish = clean(data.get("about_dish", item.about_dish), 5000)
        item.notes = clean(data.get("notes", item.notes), 3000)
        item.recommendation = clean(data.get("recommendation", item.recommendation), 3000)
        item.price_range = clean(data.get("price_range", item.price_range), 120)
        item.timing = clean(data.get("timing", item.timing), 120)
        item.dietary_type = dietary
        item.category_id = category_id
        item.reel_link = reel_link
        item.status = "published" if data.get("status", item.status) == "published" else "draft"
        if not item.location or not item.place_name or not item.about_dish:
            session_db.rollback()
            if new_photo_path:
                delete_upload(new_photo_path)
            return fail("Location, place name and about the dish are required.", 400)
        session_db.commit()
        if old_path and old_path != item.photo_path:
            delete_upload(old_path)
        return ok(adventure_json(item))
    except (ValueError, TypeError) as error:
        session_db.rollback()
        if new_photo_path:
            delete_upload(new_photo_path)
        return fail(str(error), 400)
    finally:
        session_db.close()


@app.route("/api/admin/adventures/<int:item_id>", methods=["DELETE"])
def admin_delete_adventure(item_id):
    require_admin()
    csrf_check()
    session_db = db()
    try:
        item = session_db.get(Adventure, item_id)
        if not item:
            return fail("Adventure not found.", 404)
        photo_path = item.photo_path
        session_db.delete(item)
        session_db.commit()
        delete_upload(photo_path)
        return ok({"deleted": True})
    finally:
        session_db.close()


@app.route("/api/admin/adventures/<int:item_id>/status", methods=["PATCH"])
def admin_adventure_status(item_id):
    require_admin()
    csrf_check()
    session_db = db()
    try:
        item = session_db.get(Adventure, item_id)
        if not item:
            return fail("Adventure not found.", 404)
        data = request.get_json(silent=True) or {}
        item.status = "published" if data.get("status") == "published" else "draft"
        session_db.commit()
        return ok(adventure_json(item))
    finally:
        session_db.close()


@app.route("/api/admin/categories", methods=["GET"])
def admin_list_categories():
    require_admin()
    session_db = db()
    try:
        query = session_db.query(Category)
        search = clean(request.args.get("search"), 120)
        if search:
            query = query.filter(Category.name.like(f"%{search}%"))
        return paginate(query.order_by(Category.name), category_json)
    finally:
        session_db.close()


@app.route("/api/admin/categories", methods=["POST"])
def admin_create_category():
    require_admin()
    csrf_check()
    session_db = db()
    try:
        data = request.get_json(silent=True) or {}
        name = clean(data.get("name"), 120)
        if not name:
            return fail("Category name is required.", 400)
        if session_db.query(Category).filter_by(name=name).first():
            return fail("A category with this name already exists.", 409)
        item = Category(name=name, slug=unique_slug(session_db, name))
        session_db.add(item)
        session_db.commit()
        return ok(category_json(item), 201)
    finally:
        session_db.close()


@app.route("/api/admin/categories/<int:category_id>", methods=["PUT"])
def admin_update_category(category_id):
    require_admin()
    csrf_check()
    session_db = db()
    try:
        item = session_db.get(Category, category_id)
        if not item:
            return fail("Category not found.", 404)
        data = request.get_json(silent=True) or {}
        name = clean(data.get("name", item.name), 120)
        if not name:
            return fail("Category name is required.", 400)
        duplicate = session_db.query(Category).filter(Category.name == name, Category.id != category_id).first()
        if duplicate:
            return fail("A category with this name already exists.", 409)
        item.name = name
        item.slug = unique_slug(session_db, name, category_id)
        session_db.commit()
        return ok(category_json(item))
    finally:
        session_db.close()


@app.route("/api/admin/categories/<int:category_id>", methods=["DELETE"])
def admin_delete_category(category_id):
    require_admin()
    csrf_check()
    session_db = db()
    try:
        item = session_db.get(Category, category_id)
        if not item:
            return fail("Category not found.", 404)
        count = len(item.adventures) + len(item.videos)
        if count:
            return fail(f"This category is used by {count} records. Reassign or delete them first.", 409)
        session_db.delete(item)
        session_db.commit()
        return ok({"deleted": True})
    finally:
        session_db.close()


@app.route("/api/admin/videos", methods=["GET"])
def admin_list_videos():
    require_admin()
    session_db = db()
    try:
        query = session_db.query(Video)
        search = clean(request.args.get("search"), 120)
        video_type = request.args.get("video_type")
        category_id = request.args.get("category_id", type=int)
        status = request.args.get("status")
        if search:
            like = f"%{search}%"
            query = query.filter((Video.title.like(like)) | (Video.description.like(like)))
        if video_type in {"full_video", "external_reel"}:
            query = query.filter(Video.video_type == video_type)
        if category_id:
            query = query.filter(Video.category_id == category_id)
        if status in {"published", "draft"}:
            query = query.filter(Video.status == status)
        return paginate(query.order_by(Video.created_at.desc()), video_json)
    finally:
        session_db.close()


@app.route("/api/admin/videos/<int:video_id>", methods=["GET"])
def admin_get_video(video_id):
    require_admin()
    session_db = db()
    try:
        item = session_db.get(Video, video_id)
        if not item:
            return fail("Video not found.", 404)
        return ok(video_json(item))
    finally:
        session_db.close()


@app.route("/api/admin/videos", methods=["POST"])
def admin_create_video():
    require_admin()
    csrf_check()
    session_db = db()
    old_paths = []
    try:
        data = request.form.to_dict() if request.form else (request.get_json(silent=True) or {})
        video_type = clean(data.get("video_type"), 30)
        if video_type not in {"full_video", "external_reel"}:
            return fail("Video type must be full_video or external_reel.", 400)
        category = session_db.get(Category, int(data.get("category_id") or 0))
        if not category:
            return fail("A valid category is required.", 400)
        title = clean(data.get("title"), 180)
        description = clean(data.get("description"), 3000)
        reel_link = clean(data.get("reel_link"), 500)
        if not title:
            return fail("Video title is required.", 400)
        video_path = ""
        thumbnail_path = ""
        video_public_id = clean(data.get("video_public_id"), 500)
        thumbnail_public_id = clean(data.get("thumbnail_public_id"), 500)
        if video_type == "full_video":
            if not reel_link or not validate_url(reel_link):
                return fail("A valid Instagram reel link is required for full videos.", 400)
            if video_public_id:
                video_path = cloudinary_ref_for_public_id(video_public_id, "video", MAX_VIDEO_BYTES)
                old_paths.append(video_path)
            elif "video" in request.files and request.files["video"].filename:
                video_path = upload_file(request.files["video"], "videos", VIDEO_EXTENSIONS, MAX_VIDEO_BYTES, detect_video, resource_type="video")
                old_paths.append(video_path)
            else:
                return fail("A video file or uploaded video asset is required.", 400)
            if thumbnail_public_id:
                thumbnail_path = cloudinary_ref_for_public_id(thumbnail_public_id, "image", MAX_IMAGE_BYTES)
                old_paths.append(thumbnail_path)
            elif "thumbnail" in request.files and request.files["thumbnail"].filename:
                thumbnail_path = upload_file(request.files["thumbnail"], "thumbnails", IMAGE_EXTENSIONS, MAX_IMAGE_BYTES, detect_image)
                old_paths.append(thumbnail_path)
        else:
            if not reel_link or not validate_url(reel_link):
                return fail("A valid Instagram or YouTube URL is required.", 400)
            if thumbnail_public_id:
                thumbnail_path = cloudinary_ref_for_public_id(thumbnail_public_id, "image", MAX_IMAGE_BYTES)
                old_paths.append(thumbnail_path)
            elif "thumbnail" in request.files and request.files["thumbnail"].filename:
                thumbnail_path = upload_file(request.files["thumbnail"], "thumbnails", IMAGE_EXTENSIONS, MAX_IMAGE_BYTES, detect_image)
                old_paths.append(thumbnail_path)
            else:
                thumbnail_path = fetch_external_thumbnail(reel_link)
        if not title:
            return fail("Video title is required.", 400)
        item = Video(video_type=video_type, title=title, description=description, category_id=category.id,
                     video_path=video_path, reel_link=reel_link, thumbnail_path=thumbnail_path,
                     status="published" if data.get("status", "published") == "published" else "draft")
        session_db.add(item)
        session_db.commit()
        return ok(video_json(item), 201)
    except (ValueError, TypeError) as error:
        session_db.rollback()
        for path in old_paths:
            delete_upload(path)
        return fail(str(error), 400)
    finally:
        session_db.close()


@app.route("/api/admin/videos/<int:video_id>", methods=["PUT"])
def admin_update_video(video_id):
    require_admin()
    csrf_check()
    session_db = db()
    new_paths = []
    try:
        item = session_db.get(Video, video_id)
        if not item:
            return fail("Video not found.", 404)
        data = request.get_json(silent=True) or request.form.to_dict()
        video_type = clean(data.get("video_type", item.video_type), 30)
        if video_type not in {"full_video", "external_reel"}:
            return fail("Video type must be full_video or external_reel.", 400)
        category = session_db.get(Category, int(data.get("category_id") or item.category_id))
        if not category:
            return fail("A valid category is required.", 400)
        title = clean(data.get("title", item.title), 180)
        description = clean(data.get("description", item.description), 3000)
        reel_link = clean(data.get("reel_link", item.reel_link), 500)
        if not title:
            return fail("Video title is required.", 400)
        if not reel_link or not validate_url(reel_link):
            return fail("A valid video link is required.", 400)
        old_video = item.video_path
        old_thumb = item.thumbnail_path
        video_public_id = clean(data.get("video_public_id"), 500)
        thumbnail_public_id = clean(data.get("thumbnail_public_id"), 500)
        new_video_path = ""
        new_thumb_path = ""
        final_thumb_path = old_thumb
        if video_public_id:
            if video_type != "full_video":
                return fail("Only full videos can use an uploaded video asset.", 400)
            new_video_path = cloudinary_ref_for_public_id(video_public_id, "video", MAX_VIDEO_BYTES)
            new_paths.append(new_video_path)
        elif "video" in request.files and request.files["video"].filename:
            if video_type != "full_video":
                return fail("Only full video uploads can include a video file.", 400)
            new_video_path = upload_file(request.files["video"], "videos", VIDEO_EXTENSIONS, MAX_VIDEO_BYTES, detect_video, resource_type="video")
            new_paths.append(new_video_path)
        if thumbnail_public_id:
            new_thumb_path = cloudinary_ref_for_public_id(thumbnail_public_id, "image", MAX_IMAGE_BYTES)
            new_paths.append(new_thumb_path)
            final_thumb_path = new_thumb_path
        elif "thumbnail" in request.files and request.files["thumbnail"].filename:
            new_thumb_path = upload_file(request.files["thumbnail"], "thumbnails", IMAGE_EXTENSIONS, MAX_IMAGE_BYTES, detect_image)
            new_paths.append(new_thumb_path)
            final_thumb_path = new_thumb_path
        elif video_type == "external_reel" and reel_link != item.reel_link and not old_thumb:
            fetched = fetch_external_thumbnail(reel_link)
            if fetched:
                new_paths.append(fetched)
                final_thumb_path = fetched
        final_video_path = new_video_path or (old_video if video_type == "full_video" else "")
        if video_type == "full_video" and not final_video_path:
            for path in new_paths:
                delete_upload(path)
            return fail("A video file is required for full videos.", 400)
        item.video_type = video_type
        item.title = title
        item.description = description
        item.category_id = category.id
        item.reel_link = reel_link
        item.video_path = final_video_path
        item.thumbnail_path = final_thumb_path
        item.status = "published" if data.get("status", item.status) == "published" else "draft"
        session_db.commit()
        if old_video and old_video != item.video_path:
            delete_upload(old_video)
        if old_thumb and old_thumb != item.thumbnail_path:
            delete_upload(old_thumb)
        return ok(video_json(item))
    except (ValueError, TypeError) as error:
        session_db.rollback()
        for path in new_paths:
            delete_upload(path)
        return fail(str(error), 400)
    finally:
        session_db.close()


@app.route("/api/admin/videos/<int:video_id>", methods=["DELETE"])
def admin_delete_video(video_id):
    require_admin()
    csrf_check()
    session_db = db()
    try:
        item = session_db.get(Video, video_id)
        if not item:
            return fail("Video not found.", 404)
        video_path, thumb_path = item.video_path, item.thumbnail_path
        session_db.delete(item)
        session_db.commit()
        delete_upload(video_path)
        delete_upload(thumb_path)
        return ok({"deleted": True})
    finally:
        session_db.close()


@app.route("/api/admin/videos/<int:video_id>/status", methods=["PATCH"])
def admin_video_status(video_id):
    require_admin()
    csrf_check()
    session_db = db()
    try:
        item = session_db.get(Video, video_id)
        if not item:
            return fail("Video not found.", 404)
        data = request.get_json(silent=True) or {}
        item.status = "published" if data.get("status") == "published" else "draft"
        session_db.commit()
        return ok(video_json(item))
    finally:
        session_db.close()


@app.route("/api/admin/gallery", methods=["GET"])
def admin_list_gallery():
    require_admin()
    session_db = db()
    try:
        query = session_db.query(GalleryItem)
        search = clean(request.args.get("search"), 120)
        status = request.args.get("status")
        if search:
            like = f"%{search}%"
            query = query.filter((GalleryItem.title.like(like)) | (GalleryItem.description.like(like)))
        if status in {"published", "draft"}:
            query = query.filter(GalleryItem.status == status)
        return paginate(query.order_by(GalleryItem.display_order, GalleryItem.created_at), gallery_json)
    finally:
        session_db.close()


@app.route("/api/admin/gallery/<int:item_id>", methods=["GET"])
def admin_get_gallery(item_id):
    require_admin()
    session_db = db()
    try:
        item = session_db.get(GalleryItem, item_id)
        if not item:
            return fail("Gallery item not found.", 404)
        return ok(gallery_json(item))
    finally:
        session_db.close()


@app.route("/api/admin/gallery", methods=["POST"])
def admin_create_gallery():
    require_admin()
    csrf_check()
    session_db = db()
    old_paths = []
    try:
        files = request.files.getlist("images") or request.files.getlist("image")
        if not files:
            return fail("At least one image is required.", 400)
        title_default = clean(request.form.get("title"), 180)
        description_default = clean(request.form.get("description"), 3000)
        titles = request.form.getlist("titles")
        descriptions = request.form.getlist("descriptions")
        max_order = session_db.query(GalleryItem.display_order).order_by(GalleryItem.display_order.desc()).first()
        order = (max_order[0] if max_order else 0) + 1
        created = []
        for index, file_obj in enumerate(files):
            image_path = upload_file(file_obj, "gallery", IMAGE_EXTENSIONS, MAX_IMAGE_BYTES, detect_image)
            old_paths.append(image_path)
            title = clean(titles[index] if index < len(titles) else title_default or Path(file_obj.filename).stem.replace("_", " ").title(), 180)
            description = clean(descriptions[index] if index < len(descriptions) else description_default, 3000)
            item = GalleryItem(title=title, description=description, image_path=image_path, display_order=order + len(created),
                               status="published" if request.form.get("status", "published") == "published" else "draft")
            session_db.add(item)
            created.append(item)
        session_db.commit()
        return ok({"items": [gallery_json(item) for item in created]}, 201)
    except (ValueError, TypeError) as error:
        session_db.rollback()
        for path in old_paths:
            delete_upload(path)
        return fail(str(error), 400)
    finally:
        session_db.close()


@app.route("/api/admin/gallery/<int:item_id>", methods=["PUT"])
def admin_update_gallery(item_id):
    require_admin()
    csrf_check()
    session_db = db()
    old_path = None
    new_image_path = ""
    try:
        item = session_db.get(GalleryItem, item_id)
        if not item:
            return fail("Gallery item not found.", 404)
        data = request.get_json(silent=True) or request.form.to_dict()
        old_path = item.image_path
        if "image" in request.files and request.files["image"].filename:
            new_image_path = upload_file(request.files["image"], "gallery", IMAGE_EXTENSIONS, MAX_IMAGE_BYTES, detect_image)
            item.image_path = new_image_path
        item.title = clean(data.get("title", item.title), 180)
        item.description = clean(data.get("description", item.description), 3000)
        item.display_order = int(data.get("display_order", item.display_order) or item.display_order)
        item.status = "published" if data.get("status", item.status) == "published" else "draft"
        if not item.title or not item.image_path:
            session_db.rollback()
            if new_image_path:
                delete_upload(new_image_path)
            return fail("Title and image are required.", 400)
        session_db.commit()
        if old_path != item.image_path:
            delete_upload(old_path)
        return ok(gallery_json(item))
    except (ValueError, TypeError) as error:
        session_db.rollback()
        if new_image_path:
            delete_upload(new_image_path)
        return fail(str(error), 400)
    finally:
        session_db.close()


@app.route("/api/admin/gallery/<int:item_id>", methods=["DELETE"])
def admin_delete_gallery(item_id):
    require_admin()
    csrf_check()
    session_db = db()
    try:
        item = session_db.get(GalleryItem, item_id)
        if not item:
            return fail("Gallery item not found.", 404)
        image_path = item.image_path
        session_db.delete(item)
        session_db.commit()
        delete_upload(image_path)
        return ok({"deleted": True})
    finally:
        session_db.close()


@app.route("/api/admin/gallery/reorder", methods=["POST"])
def admin_reorder_gallery():
    require_admin()
    csrf_check()
    session_db = db()
    try:
        data = request.get_json(silent=True) or {}
        ids = data.get("ids") or []
        if not isinstance(ids, list):
            return fail("ids must be a list.", 400)
        for index, item_id in enumerate(ids):
            item = session_db.get(GalleryItem, int(item_id))
            if item:
                item.display_order = index
        session_db.commit()
        return ok({"reordered": True})
    except (ValueError, TypeError) as error:
        session_db.rollback()
        return fail(str(error), 400)
    finally:
        session_db.close()


@app.route("/api/admin/collaborations", methods=["GET"])
def admin_list_collaborations():
    require_admin()
    session_db = db()
    try:
        query = session_db.query(Collaboration)
        search = clean(request.args.get("search"), 120)
        status = request.args.get("status")
        if search:
            like = f"%{search}%"
            query = query.filter((Collaboration.name.like(like)) | (Collaboration.email.like(like)) | (Collaboration.message.like(like)))
        if status in {"new", "read", "in_progress", "replied", "closed"}:
            query = query.filter(Collaboration.status == status)
        return paginate(query.order_by(Collaboration.submitted_at.desc()), collaboration_json)
    finally:
        session_db.close()


@app.route("/api/admin/collaborations/<int:item_id>", methods=["GET"])
def admin_get_collaboration(item_id):
    require_admin()
    session_db = db()
    try:
        item = session_db.get(Collaboration, item_id)
        if not item:
            return fail("Collaboration not found.", 404)
        if item.status == "new":
            item.status = "read"
            session_db.commit()
        return ok(collaboration_json(item))
    finally:
        session_db.close()


@app.route("/api/admin/collaborations/<int:item_id>", methods=["PUT"])
def admin_update_collaboration(item_id):
    require_admin()
    csrf_check()
    session_db = db()
    try:
        item = session_db.get(Collaboration, item_id)
        if not item:
            return fail("Collaboration not found.", 404)
        data = request.get_json(silent=True) or {}
        status = clean(data.get("status"), 30)
        if status in {"new", "read", "in_progress", "replied", "closed"}:
            item.status = status
        session_db.commit()
        return ok(collaboration_json(item))
    finally:
        session_db.close()


@app.route("/api/admin/collaborations/<int:item_id>", methods=["DELETE"])
def admin_delete_collaboration(item_id):
    require_admin()
    csrf_check()
    session_db = db()
    try:
        item = session_db.get(Collaboration, item_id)
        if not item:
            return fail("Collaboration not found.", 404)
        session_db.delete(item)
        session_db.commit()
        return ok({"deleted": True})
    finally:
        session_db.close()


def youtube_id(url):
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    if "youtu.be" in host:
        return parsed.path.strip("/").split("/")[0]
    if "youtube.com" in host:
        query = urllib.parse.parse_qs(parsed.query)
        if "v" in query:
            return query["v"][0]
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[0] in {"shorts", "embed", "live"}:
            return parts[1]
    return ""


def fetch_external_thumbnail(url):
    def save_image(image_url):
        request_obj = urllib.request.Request(image_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request_obj, timeout=8) as response:
            if response.status != 200:
                return ""
            mime = response.headers.get_content_type()
            if not mime.startswith("image/"):
                return ""
            data = response.read(MAX_IMAGE_BYTES + 1)
            if not data or len(data) > MAX_IMAGE_BYTES:
                return ""
            suffix = mimetypes.guess_extension(mime) or ".jpg"
            if suffix == ".jpe":
                suffix = ".jpg"
            return store_media_bytes(data, "thumbnails", suffix, "image")

    try:
        video_id = youtube_id(url)
        candidates = []
        if video_id:
            candidates = [f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg", f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"]
        elif "instagram.com" in urllib.parse.urlparse(url).netloc.lower():
            page_request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(page_request, timeout=8) as response:
                if response.status != 200:
                    return ""
                html = response.read(2 * 1024 * 1024).decode("utf-8", "ignore")
            match = re.search(r"<meta[^>]+property=[\"']og:image[\"'][^>]+content=[\"']([^\"']+)", html, re.I) or re.search(r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+property=[\"']og:image[\"']", html, re.I)
            if match:
                image_url = match.group(1).replace("&amp;", "&")
                if image_url.startswith("//"):
                    image_url = "https:" + image_url
                candidates = [image_url]
        else:
            return ""
        for candidate in candidates:
            try:
                saved = save_image(candidate)
                if saved:
                    return saved
            except Exception as error:
                log.info("Thumbnail candidate failed for %s: %s", candidate, error)
    except Exception as error:
        log.info("External thumbnail unavailable for %s: %s", url, error)
    return ""


def initialize_database():
    session_db = db()
    try:
        if not session_db.query(Admin).first():
            username = os.environ.get("ADMIN_USERNAME", "admin")
            password = os.environ.get("ADMIN_PASSWORD")
            if not password:
                password = secrets.token_urlsafe(18)
                print("\n" + "=" * 64)
                print("FIRST-RUN ADMIN CREATED")
                print(f"Username: {username}")
                print(f"Temporary password: {password}")
                print("Change this password after first login.")
                print("=" * 64 + "\n")
            session_db.add(Admin(username=username, password_hash=generate_password_hash(password)))
        categories = {item.name: item for item in session_db.query(Category).all()}
        for name in CATEGORY_NAMES:
            if name not in categories:
                item = Category(name=name, slug=unique_slug(session_db, name))
                session_db.add(item)
                categories[name] = item
        session_db.commit()
    finally:
        session_db.close()


def seed_legacy_content(session_db, categories):
    if session_db.query(Adventure).count() or session_db.query(Video).count() or session_db.query(GalleryItem).count():
        return
    category_map = {
        "Street Food": "Street Food", "Heritage & Royal": "Traditional Food", "Local Favourites": "Restaurant",
        "Hidden Gems": "Street Food", "Cafés & Bistros": "Cafe", "Regional Flavours": "Traditional Food",
    }
    adventures = [
        ("Near Laxman Mandir, Old City, Bharatpur", "Laxman Mandir Bedmi Puri & Aloo Sabzi", 9.8, "Street Food", "₹50 for 2 Puris + Sabzi", "6:30 AM – 11:30 AM", "Vegetarian", "Golden, intensely crisp puris stuffed with coarse spiced urad dal, served piping hot alongside tangy aloo sabzi and mustard pickled green chillies.", "The outer crust crackles with a nutty aroma of dal and coriander. The gravy is mildly tart with slow-building warmth.", "A quintessential Bharatpur breakfast ritual."),
        ("Mathura Gate Chowk, Bharatpur", "Shree Krishna Heeng Kachori & Kadhi", 9.7, "Street Food", "₹30 per plate", "7:00 AM – 2:00 PM & 4:30 PM – 8:00 PM", "Vegetarian", "Ultra-flaky puffed pastry laden with aromatic heeng, drowned in gram-flour kadhi and tangy tamarind chutney.", "The crunch remains intact even after drenching in warm kadhi.", "Best enjoyed fresh from the cauldron."),
        ("Deeg Highway, Bharatpur Division", "Deeg Road Royal Laal Maas Dhaba", 9.9, "Traditional Food", "₹380 with Bajra Roti", "12:30 PM – 4:00 PM & 7:00 PM – 11:00 PM", "Non-Vegetarian", "Tender goat meat slow-braised over charcoal in a thick gravy of hand-ground Mathania chillies and mustard oil.", "Rich burgundy colour comes from soaked chillies, with a smoky charcoal finish.", "Order the full portion and eat slowly."),
        ("Subhash Chowk Bazaar, Bharatpur", "Mahaveer Rabdi & Hot Malpua", 9.6, "Desserts & Sweets", "₹70 per plate", "8:00 AM – 10:30 PM", "Vegetarian", "Freshly fried syrupy malpua with caramelized edges smothered in thick saffron rabdi.", "The contrast between crisp malpua and chilled rabdi is unforgettable.", "Visit during evening for the warmest service."),
        ("Kumher Gate Evening Bazaar, Bharatpur", "Kumher Gate Tawa Seekh & Keema", 9.5, "Street Food", "₹140 with 2 Roomali Rotis", "6:00 PM – 11:00 PM", "Non-Vegetarian", "Spiced minced meat charred over iron tawa with butter, onions, and roomali rotis.", "Juicy, smoky, and packed with green cardamom and black pepper.", "A late-night street favourite."),
        ("Station Road, Bharatpur", "Braj Kadhai Clay Oven Paneer Feast", 9.4, "Restaurant", "₹240 per platter", "12:00 PM – 11:00 PM", "Vegetarian", "Chunky buffalo-milk paneer roasted to smoky perfection in traditional clay tandoors.", "Silky interior with charred blisters and a gentle ajwain punch.", "Ask for extra mint chutney."),
        ("Lohagarh Fort Outer Enclosure, Bharatpur", "The Fortside Courtyard Café", 9.3, "Cafe", "₹180 - ₹350 per person", "9:00 AM – 9:00 PM", "Vegetarian", "A tranquil stone courtyard café serving regional pour-overs, artisan sandwiches, and warm croissants.", "A calm heritage retreat overlooking the Lohagarh moat.", "Ideal for afternoon coffee."),
        ("Deeg Palace Ring Road, Bharatpur Division", "Govardhan Royal Chappan Bhog Thali", 9.8, "Traditional Food", "₹350 unlimited thali", "11:30 AM – 4:00 PM & 7:00 PM – 10:00 PM", "Vegetarian", "A grand 16-dish brass thali featuring Panchmel dal, baatis, gatte ki sabzi, ker sangri, kadhi, and regional mithais.", "Every bowl showcases a distinct spice profile and regional tradition.", "Go hungry and share if possible."),
        ("Kotwali Chowk, Bharatpur", "Brijwasi Heeng Jalebi & Khurchan", 9.7, "Bakery", "₹60 per dona", "6:00 AM – 10:00 AM & 5:00 PM – 9:00 PM", "Vegetarian", "Crispy spiral jalebis made with naturally fermented batter and fried in desi ghee, paired with khurchan.", "Crisp outside, hot fragrant syrup inside, softened by unsweetened creaminess.", "Morning is the best time to visit."),
        ("Mathura Gate Bazaar, Bharatpur", "Mathura Gate Velvet Dahi Bhalla", 9.6, "Street Food", "₹40 per plate", "3:00 PM – 8:30 PM", "Vegetarian", "Spongy urad dal dumplings soaked in chilled sweetened yogurt with roasted jeera and tamarind chutney.", "Cool, refreshing, and deeply comforting on warm afternoons.", "Perfect for a light evening snack."),
        ("Subhash Chowk Corner, Bharatpur", "Subhash Chowk Smoked Kulhad Chai", 9.5, "Drinks & Beverages", "₹15 per clay cup", "5:30 AM – 11:30 PM", "Vegetarian", "Full-cream milk simmered with green cardamom, ginger, and Assam tea, poured into terracotta kulhads.", "The earthy aroma of baked clay infuses into the hot tea.", "Pair with a local snack."),
        ("Near Circular Road, Bharatpur", "Khandani Desi Ghee Dal Baati Churma", 9.8, "Traditional Food", "₹220 per full plate", "11:00 AM – 9:30 PM", "Vegetarian", "Traditional wheat and semolina baatis baked until crackled, dipped in cow ghee, served with spicy five-lentil dal and dry fruit churma.", "Rich, hearty, and authentic with coarse comforting churma.", "A complete Rajasthani staple."),
    ]
    image_map = {
        "Bedmi Puri": "assets/images/food_bedmi_puri.svg", "Heeng Kachori": "assets/images/food_bharatpur_kachori.svg",
        "Laal Maas": "assets/images/food_royal_laal_maas.svg", "Rabdi & Hot Malpua": "assets/images/food_braj_rabdi_malpua.svg",
        "Tawa Seekh": "assets/images/food_seekh_kebabs.svg", "Paneer Feast": "assets/images/food_paneer_tikka_feast.svg",
        "Courtyard Café": "assets/images/food_heritage_cafe.svg", "Chappan Bhog": "assets/images/food_chappan_bhog_thali.svg",
        "Jalebi & Khurchan": "assets/images/food_jalebi_khurchan.svg", "Dahi Bhalla": "assets/images/food_dahi_bhalla_chaat.svg",
        "Kulhad Chai": "assets/images/food_masala_chai_kulhad.svg", "Dal Baati Churma": "assets/images/food_dal_baati_churma.svg",
    }
    existing_assets = PUBLIC_DIR / "assets"
    for index, (location, name, rating, label, price, timing, diet, about, notes, recommendation) in enumerate(adventures):
        category_name = category_map.get(label, "Restaurant")
        category = categories.get(category_name) or next(iter(categories.values()))
        relative_image = next((image for key, image in image_map.items() if key in name), "")
        photo_path = ""
        if relative_image and (PUBLIC_DIR / relative_image).exists():
            photo_path = store_media_file(PUBLIC_DIR / relative_image, "adventures", "image")
        session_db.add(Adventure(location=location, place_name=name, rating=rating, about_dish=about, notes=notes,
                                 recommendation=recommendation, price_range=price, timing=timing, dietary_type=diet,
                                 category_id=category.id, reel_link="https://www.instagram.com/taste_safari_/reels/",
                                 photo_path=photo_path, status="published"))
    videos_seed = [
        ("full_video", "Saffron Heeng Jalebi Crafting in Bharatpur Bazaar", "Watch the swirling technique of fermented batter piped into boiling desi ghee and served fresh on sal leaves.", "Street Food", "assets/videos/portrait/jalebi_sweet_safari_portrait.mp4", "assets/thumbnails/portrait/jalebi_sweet_safari_p.jpg"),
        ("full_video", "Royal Heritage Spiced Feast Preparation", "Layered aromatic basmati rice, whole cloves, saffron broth, and roasted spices slow-steamed under sealed clay dough.", "Traditional Food", "assets/videos/portrait/royal_heritage_feast_portrait.mp4", "assets/thumbnails/portrait/royal_heritage_feast_p.jpg"),
        ("external_reel", "Bedmi Puri Morning Ritual", "A quick reel from the Laxman Mandir breakfast counter.", "Street Food", "", "assets/images/gallery_jalebi_fry.jpg"),
        ("external_reel", "Royal Feast on Deeg Road", "A cinematic reel capturing the royal heritage thali service.", "Traditional Food", "", "assets/images/gallery_royal_thali.jpg"),
    ]
    for index, (kind, title, description, category_name, video_src, thumb_src) in enumerate(videos_seed):
        category = categories.get(category_name) or next(iter(categories.values()))
        video_path = ""
        if video_src and (PUBLIC_DIR / video_src).exists():
            video_path = store_media_file(PUBLIC_DIR / video_src, "videos", "video")
        thumbnail_path = ""
        if thumb_src and (PUBLIC_DIR / thumb_src).exists():
            thumbnail_path = store_media_file(PUBLIC_DIR / thumb_src, "thumbnails", "image")
        session_db.add(Video(video_type=kind, title=title, description=description, category_id=category.id,
                             video_path=video_path, reel_link="https://www.instagram.com/taste_safari_/reels/",
                             thumbnail_path=thumbnail_path, status="published"))
    gallery_seed = [
        ("Royal Braj Heritage Spiced Platter", "Deeg Road Royal Kitchens", "Aromatic basmati rice layered with roasted spices and herbs slow-steamed over coals.", "assets/images/portrait/gallery_royal_thali.jpg"),
        ("Saffron Jalebis in Pure Desi Ghee", "Kotwali Chowk Halwai", "Fermented batter swirled in sizzling pure ghee and soaked into hot saffron syrup.", "assets/images/portrait/gallery_jalebi_fry.jpg"),
        ("Crisp Cast Iron Tawa Sizzle", "Station Road Night Market", "Paper-thin crispy fold sizzling on a cast-iron tawa with spicy red garlic chutney.", "assets/images/portrait/gallery_tawa_sizzle.jpg"),
        ("Crisp Artisan Halwai Roll", "Laxman Temple Street", "Piping hot golden rolled pastry layered with savory spiced lentils and green chillies.", "assets/images/portrait/gallery_crisp_roll.jpg"),
        ("Generational Halwai Sweet Crafting", "Subhash Chowk Bazaar", "Master halwai reducing sweetened buffalo milk into delicate layered khurchan sheets.", "assets/images/portrait/gallery_halwai_craft.jpg"),
        ("Slow-Simmered Royal Braj Curry", "Deeg Highway Dhaba", "Slow-simmered rich red curry with whole Mathania chillies, mustard oil, and ground cloves.", "assets/images/portrait/gallery_spiced_curry.jpg"),
        ("Golden Jalebis Fresh from the Vat", "Brijwasi Confectioners", "Lustrous syrupy saffron rings resting on brass trays ready for the morning rush.", "assets/images/portrait/gallery_jalebi_syrup.jpg"),
        ("Traditional Sweet Heritage", "Old City Chowk", "Historic sweet-making techniques preserved across three generations in Bharatpur.", "assets/images/portrait/gallery_sweet_tradition.jpg"),
    ]
    for index, (title, location, caption, image_src) in enumerate(gallery_seed):
        image_path = ""
        if (PUBLIC_DIR / image_src).exists():
            image_path = store_media_file(PUBLIC_DIR / image_src, "gallery", "image")
        session_db.add(GalleryItem(title=title, description=f"{caption} · {location}", image_path=image_path,
                                   display_order=index, status="published"))
    session_db.commit()


@app.before_request
def ensure_database():
    if not hasattr(app, "_db_initialized"):
        initialize_database()
        app._db_initialized = True


def parse_sqlite_dt(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", ""))
    except Exception:
        return None


def migrate_sqlite_to_configured_db():
    if not DATABASE_URL:
        print("DATABASE_URL is not set; SQLite migration requires a PostgreSQL/Supabase target.")
        return
    if not DB_PATH.exists():
        print("Local taste_safari.db was not found; nothing to migrate.")
        return
    source = sqlite3.connect(DB_PATH)
    source.row_factory = sqlite3.Row
    session_db = db()
    datetime_fields = {"created_at", "updated_at", "submitted_at"}
    specs = [
        ("admins", Admin), ("categories", Category), ("adventures", Adventure),
        ("videos", Video), ("gallery", GalleryItem), ("collaborations", Collaboration),
    ]
    try:
        for table, model in specs:
            existing = {row[0] for row in session_db.query(model.id).all()}
            existing_usernames = {row[0] for row in session_db.query(Admin.username).all()} if table == "admins" else set()
            existing_names = {row[0] for row in session_db.query(Category.name).all()} if table == "categories" else set()
            existing_slugs = {row[0] for row in session_db.query(Category.slug).all()} if table == "categories" else set()
            inserted = 0
            for row in source.execute(f"SELECT * FROM {table}"):
                data = dict(row)
                if data.get("id") in existing:
                    continue
                if table == "admins" and data.get("username") in existing_usernames:
                    continue
                if table == "categories" and (data.get("name") in existing_names or data.get("slug") in existing_slugs):
                    continue
                for key in list(data.keys()):
                    if key in datetime_fields:
                        parsed = parse_sqlite_dt(data[key])
                        if parsed is None:
                            data.pop(key, None)
                        else:
                            data[key] = parsed
                session_db.add(model(**data))
                inserted += 1
            session_db.commit()
            print(f"{table}: inserted {inserted} row(s)")
        if "postgres" in DATABASE_URL:
            for table, _model in specs:
                try:
                    session_db.execute(text(f"SELECT setval('{table}_id_seq', COALESCE((SELECT MAX(id) FROM {table}), 1), (SELECT COUNT(*) FROM {table}) > 0)"))
                except Exception as error:
                    log.info("Sequence reset skipped for %s: %s", table, error)
            session_db.commit()
    finally:
        session_db.close()
        source.close()


def migrate_media_to_cloudinary():
    if not cloudinary_enabled():
        print("Cloudinary is not configured; set CLOUDINARY_* env vars first.")
        return
    session_db = db()
    updated = 0
    try:
        jobs = []
        for item in session_db.query(Adventure).all():
            jobs.append((item, "photo_path", "adventures", "image"))
        for item in session_db.query(Video).all():
            jobs.append((item, "video_path", "videos", "video"))
            jobs.append((item, "thumbnail_path", "thumbnails", "image"))
        for item in session_db.query(GalleryItem).all():
            jobs.append((item, "image_path", "gallery", "image"))
        for item, field, folder, resource_type in jobs:
            ref = getattr(item, field)
            if not ref or ref.startswith("cloudinary:") or ref.startswith("http"):
                continue
            source = UPLOAD_ROOT / ref
            if not source.exists():
                continue
            setattr(item, field, cloudinary_upload_path(source, folder, resource_type))
            updated += 1
        session_db.commit()
        print(f"media: migrated {updated} file(s) to Cloudinary")
    finally:
        session_db.close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "migrate-sqlite":
        migrate_sqlite_to_configured_db()
    elif len(sys.argv) > 1 and sys.argv[1] == "migrate-media":
        migrate_media_to_cloudinary()
    else:
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
