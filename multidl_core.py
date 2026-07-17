#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
multidl_core.py  –  هسته‌ی مشترک دانلودر چندسایته (v1.0)
Python : 3.9+
این فایل به‌تنهایی اجرا نمی‌شود؛ توسط multidl_cli.py و multidl_bot.py ایمپورت می‌شود.

نصب پیش‌نیازها:
    pip install yt-dlp requests
    (اختیاری، برای پشتیبانی بهتر یوتیوب/اینستاگرام)
    pip install pytube instaloader

اگر روی Termux هستید و merge صدا/تصویر نیاز شد:
    pkg install ffmpeg
"""

import os
import re
import sys
import json
import time
import shutil
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

try:
    import requests
except ImportError:
    print("❌ کتابخانه requests نصب نیست.\n   pip install requests")
    sys.exit(1)

log = logging.getLogger("multidl")
if not log.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA}
TIMEOUT = 15
# TIMEOUT بالا برای درخواست‌های سریع (چک‌کردن هر روش/provider) استفاده می‌شود تا
# زنجیره‌ی fallback سریع جلو برود. برای خودِ دانلود فایل بزرگ باید صبورتر بود،
# چون رو اینترنت موبایل سرعت نوسان می‌کند و timeout کوتاه باعث لغو بی‌خودی
# دانلودهایی می‌شود که حتی ۹۵٪ کامل شده بودند.
DOWNLOAD_SOCKET_TIMEOUT = 30
DOWNLOAD_RETRIES = 10

INCLUDE_ID_SUFFIX = True
# اگر False شود، پسوند "[videoID]" از اسم فایل‌های دانلودشده با yt-dlp حذف می‌شود.
# نکته: این [ID] همان شناسه‌ی یکتای ویدیو در سایت مبدأ است (نه یک کد خرابی)؛ حذفش
# احتمال بازنویسی فایل‌های هم‌نام (چند ویدیوی مختلف با یک عنوان یکسان) را بالا می‌برد.

def _outtmpl(outdir):
    if INCLUDE_ID_SUFFIX:
        return os.path.join(outdir, "%(title).150B [%(id)s].%(ext)s")
    return os.path.join(outdir, "%(title).150B.%(ext)s")

# ───────────────────────── ابزارهای عمومی ─────────────────────────

def which(cmd):
    return shutil.which(cmd) is not None

def has_module(name):
    try:
        __import__(name)
        return True
    except ImportError:
        return False

def ensure_dir(path):
    Path(path).mkdir(parents=True, exist_ok=True)
    return path

def safe_filename(name, maxlen=120):
    name = re.sub(r'[\\/*?:"<>|\n\r\t]', "_", name).strip()
    return name[:maxlen] if name else f"file_{int(time.time())}"

PARALLEL_MIN_SIZE = 8 * 1024 * 1024   # فقط فایل‌های بزرگ‌تر از ۸ مگابایت با چند اتصال دانلود می‌شوند
PARALLEL_CONNECTIONS = 4               # تعداد اتصال هم‌زمان برای دانلود موازی

# اگر پاسخ سرور یکی از این نوع‌ها باشد، یعنی به‌جای فایل رسانه‌ای یک صفحه‌ی وب/خطا
# برگردانده (مثلاً چون لینک نیاز به روش استخراج دیگری داشته)؛ نباید آن را به‌عنوان
# فایل دانلودشده ذخیره کرد (این دقیقاً باگی بود که باعث می‌شد گاهی به‌جای ویدیو یک
# فایل HTML بی‌فایده به کاربر تحویل داده شود).
_NON_MEDIA_CONTENT_TYPES = ("text/html", "application/xhtml+xml")

# لایه‌ی دوم دفاعی: حتی اگر content-type درست تشخیص داده نشود (بعضی سرورها هدر
# اشتباه می‌فرستند)، اگر پسوند فایل خودش نشان‌دهنده‌ی یک صفحه‌ی وب/اسکریپت باشد
# (نه رسانه)، آن را رد می‌کنیم. این دقیقاً همان مشکلی بود که باعث می‌شد به‌جای
# ویدیو یک فایل .php بی‌فایده دانلود شود.
_NON_MEDIA_EXTENSIONS = {".php", ".asp", ".aspx", ".jsp", ".jspx", ".cgi", ".htm", ".html"}


def _reject_if_non_media_extension(name_or_url):
    if not name_or_url:
        return
    path_part = urlparse(name_or_url).path if "://" in name_or_url else name_or_url
    ext = os.path.splitext(path_part)[1].lower().split("?")[0]
    if ext in _NON_MEDIA_EXTENSIONS:
        raise ProviderError(
            f"لینک به یک فایل رسانه‌ای واقعی اشاره نمی‌کند (پسوند {ext} یعنی صفحه‌ی وب/اسکریپت است، نه ویدیو/عکس/صدا)")


def _looks_like_html_bytes(chunk):
    head = chunk[:512].lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html")


def _range_probe(url, headers):
    """یک درخواست Range سبک برای فهمیدن پشتیبانی سرور از دانلود موازی + حجم فایل + نام پیشنهادی + نوع محتوا."""
    try:
        r = requests.get(url, headers={**headers, "Range": "bytes=0-0"},
                          timeout=TIMEOUT, stream=True, allow_redirects=True)
        supports = r.status_code == 206
        size = None
        cr = r.headers.get("Content-Range")
        if cr and "/" in cr:
            try:
                size = int(cr.rsplit("/", 1)[-1])
            except ValueError:
                size = None
        if size is None:
            cl = r.headers.get("Content-Length")
            if cl:
                try:
                    size = int(cl)
                except ValueError:
                    pass
        cd = r.headers.get("content-disposition", "")
        m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd)
        suggested_name = unquote(m.group(1)) if m else None
        ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        r.close()
        return supports, size, suggested_name, ctype
    except Exception:
        return False, None, None, ""


def _download_byte_range(url, fpath, start, end, headers):
    h = dict(headers)
    h["Range"] = f"bytes={start}-{end}"
    r = requests.get(url, headers=h, timeout=DOWNLOAD_SOCKET_TIMEOUT, stream=True)
    r.raise_for_status()
    with open(fpath, "r+b") as f:
        f.seek(start)
        for chunk in r.iter_content(chunk_size=1 << 18):
            if chunk:
                f.write(chunk)


def stream_download(url, outdir, filename=None, headers=None, session=None):
    """
    دانلود مستقیم یک فایل. دو بهبود نسبت به یک درخواست ساده‌ی GET:
      ۱. اگر سرور از HTTP Range پشتیبانی کند و فایل به‌اندازه‌ی کافی بزرگ باشد،
         با چند اتصال هم‌زمان (سریع‌تر) دانلود می‌شود؛ در غیر این صورت fallback
         امن به دانلود تک‌اتصالی معمولی.
      ۲. اگر پاسخ سرور در واقع یک صفحه‌ی HTML/خطا باشد (نه فایل رسانه‌ای واقعی)،
         خطا می‌دهد به‌جای اینکه آن صفحه را به‌اشتباه به‌عنوان فایل دانلودشده ذخیره کند.
    """
    h = dict(HEADERS)
    if headers:
        h.update(headers)
    ensure_dir(outdir)

    # نکته: عمداً پسوند خودِ URL درخواستی را رد نمی‌کنیم — خیلی از سایت‌های دانلود
    # واقعی، فایل رسانه‌ای را از یک آدرس مثل download.php?... سرو می‌کنند و این
    # کاملاً قانونی است. آنچه واقعاً مهم است پسوندِ فایلی است که قرار است ذخیره شود
    # (از content-disposition یا پارامتر filename) — همان‌جا چک می‌کنیم.
    if filename:
        _reject_if_non_media_extension(filename)

    if session is None:
        supports, size, suggested_name, ctype = _range_probe(url, h)
        if ctype in _NON_MEDIA_CONTENT_TYPES:
            raise ProviderError(
                f"سرور به‌جای فایل، یک صفحه‌ی وب برگرداند (content-type: {ctype}) — "
                f"این لینک احتمالاً نیاز به روش استخراج اختصاصی دارد که در دسترس نیست")
        if suggested_name:
            _reject_if_non_media_extension(suggested_name)
        if supports and size and size >= PARALLEL_MIN_SIZE:
            final_name = safe_filename(filename or suggested_name or
                                        os.path.basename(urlparse(url).path) or f"file_{int(time.time())}")
            fpath = os.path.join(outdir, final_name)
            try:
                with open(fpath, "wb") as f:
                    f.truncate(size)
                chunk_len = size // PARALLEL_CONNECTIONS
                ranges = []
                for i in range(PARALLEL_CONNECTIONS):
                    start = i * chunk_len
                    end = size - 1 if i == PARALLEL_CONNECTIONS - 1 else start + chunk_len - 1
                    ranges.append((start, end))
                errors = []
                with ThreadPoolExecutor(max_workers=PARALLEL_CONNECTIONS) as ex:
                    futs = [ex.submit(_download_byte_range, url, fpath, s, e, h) for s, e in ranges]
                    for fut in futs:
                        try:
                            fut.result()
                        except Exception as e:
                            errors.append(str(e))
                if not errors and os.path.getsize(fpath) == size:
                    return fpath
                try:
                    os.remove(fpath)
                except OSError:
                    pass
                # اگه موازی شکست خورد، پایین‌تر با روش معمولی دوباره امتحان می‌کنیم
            except Exception:
                try:
                    os.remove(fpath)
                except OSError:
                    pass

    # روش معمولی (تک‌اتصالی) — هم fallback و هم حالت پیش‌فرض فایل‌های کوچک
    s = session or requests
    r = s.get(url, headers=h, stream=True, timeout=DOWNLOAD_SOCKET_TIMEOUT, allow_redirects=True)
    r.raise_for_status()
    ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if ctype in _NON_MEDIA_CONTENT_TYPES:
        raise ProviderError(
            f"سرور به‌جای فایل، یک صفحه‌ی وب برگرداند (content-type: {ctype}) — "
            f"این لینک احتمالاً نیاز به روش استخراج اختصاصی دارد که در دسترس نیست")
    if not filename:
        cd = r.headers.get("content-disposition", "")
        m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd)
        if m:
            filename = unquote(m.group(1))
        else:
            filename = os.path.basename(urlparse(url).path) or f"file_{int(time.time())}"
    _reject_if_non_media_extension(filename)
    filename = safe_filename(filename)
    fpath = os.path.join(outdir, filename)
    first_chunk_checked = False
    with open(fpath, "wb") as f:
        for chunk in r.iter_content(chunk_size=1 << 16):
            if chunk:
                if not first_chunk_checked:
                    first_chunk_checked = True
                    if _looks_like_html_bytes(chunk):
                        r.close()
                        try:
                            f.close()
                            os.remove(fpath)
                        except OSError:
                            pass
                        raise ProviderError(
                            "سرور به‌جای فایل رسانه‌ای، محتوای HTML برگرداند (content-type درست تشخیص داده نشد) — "
                            "این لینک احتمالاً نیاز به روش استخراج اختصاصی دارد")
                f.write(chunk)
    return fpath

def fetch_html(url, headers=None, session=None):
    s = session or requests
    h = dict(HEADERS)
    if headers:
        h.update(headers)
    r = s.get(url, headers=h, timeout=TIMEOUT, allow_redirects=True)
    r.raise_for_status()
    return r.text, str(r.url)

def find_meta(html, prop):
    m = re.search(
        rf'<meta[^>]+(?:property|name)=["\']{re.escape(prop)}["\'][^>]+content=["\']([^"\']+)["\']',
        html, re.I)
    if m:
        return m.group(1)
    m = re.search(
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{re.escape(prop)}["\']',
        html, re.I)
    return m.group(1) if m else None


class ProviderError(Exception):
    pass


# ───────────────────────── لایه‌ی yt-dlp (مشترک بین چند دسته) ─────────────────────────

def find_cookiefile():
    """
    اگر فایل cookies.txt (فرمت Netscape) کنار اسکریپت یا در پوشه‌ی جاری باشد،
    خودکار برای عبور از خطای "Sign in to confirm you're not a bot" استفاده می‌شود.
    برای ساخت این فایل: افزونه‌ی مرورگر "Get cookies.txt LOCALLY" را نصب کن،
    وارد youtube.com شو، اکسپورت بگیر و کنار همین اسکریپت‌ها با نام cookies.txt بگذار.
    """
    candidates = [
        os.path.join(os.getcwd(), "cookies.txt"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def ytdlp_download(url, outdir, extra_opts=None, use_module_first=True):
    """
    تلاش برای دانلود با yt-dlp. اول ماژول پایتون، بعد باینری CLI.
    خروجی: لیست مسیر فایل‌های دانلودشده.
    """
    ensure_dir(outdir)
    base_opts = {
        "outtmpl": _outtmpl(outdir),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "format": "bv*+ba/b",
        "merge_output_format": "mp4",
        "retries": DOWNLOAD_RETRIES,
        "fragment_retries": DOWNLOAD_RETRIES,
        "socket_timeout": DOWNLOAD_SOCKET_TIMEOUT,
        "concurrent_fragment_downloads": 8,
        "http_chunk_size": 10 * 1024 * 1024,
    }
    if which("aria2c"):
        # aria2c چند اتصال هم‌زمان برای هر قطعه باز می‌کند و معمولاً به‌طور محسوسی
        # سریع‌تر از دانلودر داخلی yt-dlp است.
        base_opts["external_downloader"] = "aria2c"
        base_opts["external_downloader_args"] = {"aria2c": ["-x", "8", "-s", "8", "-k", "1M"]}
    cookiefile = find_cookiefile()
    if cookiefile:
        base_opts["cookiefile"] = cookiefile
    if extra_opts:
        base_opts.update(extra_opts)

    if use_module_first and has_module("yt_dlp"):
        import yt_dlp
        before = set(os.listdir(outdir))
        try:
            with yt_dlp.YoutubeDL(base_opts) as ydl:
                ydl.download([url])
        except Exception as e:
            raise ProviderError(f"yt-dlp(module): {e}")
        after = set(os.listdir(outdir))
        new_files = [os.path.join(outdir, f) for f in (after - before)]
        if new_files:
            return new_files
        raise ProviderError("yt-dlp(module): فایلی تولید نشد")

    if which("yt-dlp"):
        before = set(os.listdir(outdir))
        cmd = ["yt-dlp", "-f", base_opts["format"], "--no-playlist", "-q",
               "-o", base_opts["outtmpl"], url]
        try:
            subprocess.run(cmd, check=True, timeout=300,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError as e:
            raise ProviderError(f"yt-dlp(cli): {e.stderr.decode(errors='ignore')[:300]}")
        after = set(os.listdir(outdir))
        new_files = [os.path.join(outdir, f) for f in (after - before)]
        if new_files:
            return new_files
        raise ProviderError("yt-dlp(cli): فایلی تولید نشد")

    raise ProviderError("yt-dlp نصب نیست (pip install yt-dlp)")


def gallery_dl_download(url, outdir):
    if not which("gallery-dl") and not has_module("gallery_dl"):
        raise ProviderError("gallery-dl نصب نیست")
    ensure_dir(outdir)
    before = set(os.listdir(outdir))
    try:
        subprocess.run(["gallery-dl", "-d", outdir, url],
                       check=True, timeout=300,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        raise ProviderError(f"gallery-dl: {e.stderr.decode(errors='ignore')[:300]}")
    after = set(os.listdir(outdir))
    new_files = [os.path.join(outdir, f) for f in (after - before)]
    if not new_files:
        raise ProviderError("gallery-dl: فایلی تولید نشد")
    return new_files


# ───────────────────────── انتخاب کیفیت (لیست فرمت‌ها + دانلود فرمت خاص) ─────────────────────────

QUALITY_MENU_CATEGORIES = {"youtube", "tiktok", "twitter", "instagram"}
# این دسته‌ها معمولاً روی yt-dlp تکیه دارند و می‌توان فرمت‌های موجود را قبل از دانلود لیست کرد.

def list_video_qualities(url, max_options=5):
    """
    فرمت‌های موجود یک ویدیو را برمی‌گرداند (برای ساخت منوی انتخاب کیفیت).
    خروجی: {"title", "id", "video_options", "audio_option", "slideshow_images", "ffmpeg_hint"}

    نکته‌ی مهم درباره‌ی کیفیت‌های بالا (1080p/1440p/4K در یوتیوب):
    این کیفیت‌ها معمولاً به‌صورت "adaptive" (ویدیوی بی‌صدا + صدای جدا) هستند، نه یک
    فایل progressive (ویدیو+صدا با هم). قبلاً این تابع فقط progressive را نشان می‌داد
    که باعث می‌شد کیفیت‌های بالا اصلاً در لیست ظاهر نشوند. حالا اگر ffmpeg نصب باشد،
    این فرمت‌های adaptive هم (با پرچم _needs_merge) اضافه می‌شوند تا بعداً با ادغام
    خودکار صدا+تصویر دانلود شوند. اگر ffmpeg نصب نباشد، همان‌طور که بود فقط progressive
    نشان داده می‌شود، ولی یک پیام (ffmpeg_hint) توضیح می‌دهد که کیفیت بالاتری هم هست.
    """
    if not has_module("yt_dlp"):
        raise ProviderError("yt-dlp نصب نیست")
    import yt_dlp

    ffmpeg_available = bool(which("ffmpeg"))

    opts = {
        "quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True,
        "socket_timeout": TIMEOUT,
        # نکته: عمداً player_client را به یک کلاینت خاص (مثلاً فقط android) محدود
        # نمی‌کنیم. یوتیوب گاهی یک کلاینت خاص را موقتاً مسدود می‌کند و این باعث
        # می‌شد کل استخراج با خطای "Requested format is not available" شکست بخورد.
        # اعتماد به رفتار پیش‌فرض خود yt-dlp (که مرتب برای دور زدن این مسدودسازی‌ها
        # به‌روزرسانی می‌شود) قابل‌اعتمادتر از هاردکد کردن یک کلاینت است.
    }
    cf = find_cookiefile()
    if cf:
        opts["cookiefile"] = cf
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    formats = info.get("formats") or []
    progressive = [f for f in formats
                   if f.get("vcodec") not in (None, "none") and f.get("acodec") not in (None, "none")]
    by_height = {}
    for f in progressive:
        h = f.get("height") or 0
        cur = by_height.get(h)
        if cur is None or (f.get("filesize") or f.get("filesize_approx") or 0) > \
                           (cur.get("filesize") or cur.get("filesize_approx") or 0):
            by_height[h] = f
    video_options = list(by_height.values())

    audio_only = [f for f in formats if f.get("vcodec") in (None, "none") and f.get("acodec") not in (None, "none")]
    best_audio = max(audio_only, key=lambda f: f.get("abr") or 0, default=None)

    ffmpeg_hint = None
    if ffmpeg_available:
        adaptive_video = [f for f in formats
                          if f.get("vcodec") not in (None, "none") and f.get("acodec") in (None, "none")]
        existing_heights = {f.get("height") or 0 for f in video_options}
        adaptive_by_height = {}
        for f in adaptive_video:
            h = f.get("height") or 0
            if h in existing_heights:
                continue
            cur = adaptive_by_height.get(h)
            if cur is None or (f.get("tbr") or 0) > (cur.get("tbr") or 0):
                adaptive_by_height[h] = f
        for f in adaptive_by_height.values():
            f["_needs_merge"] = True   # این فرمت صدا ندارد؛ موقع دانلود باید با bestaudio ادغام شود
        video_options += list(adaptive_by_height.values())
    else:
        all_video_heights = [f.get("height") or 0 for f in formats if f.get("vcodec") not in (None, "none")]
        max_available = max(all_video_heights, default=0)
        max_shown = max([f.get("height") or 0 for f in video_options], default=0)
        if max_available > max_shown:
            ffmpeg_hint = (f"کیفیت‌های بالاتر تا {max_available}p هم موجودند، ولی چون ffmpeg نصب "
                           f"نیست نمی‌شه نمایش/ادغامشون کرد. نصب ffmpeg این محدودیت را برمی‌دارد.")

    video_options = sorted(video_options, key=lambda f: f.get("height") or 0, reverse=True)[:max_options]

    # تشخیص اسلایدشوی عکس (مثل پست‌های چندعکسی تیک‌تاک): فرمت‌هایی که نه ویدیو دارند نه صدا
    # و پسوندشان تصویر است، یا لینکشان به یک فایل عکس اشاره می‌کند.
    slideshow_images = [
        f for f in formats
        if f.get("vcodec") in (None, "none") and f.get("acodec") in (None, "none")
        and (f.get("ext") in ("jpg", "jpeg", "png", "webp")
             or (f.get("url") and re.search(r"\.(jpe?g|png|webp)(\?|$)", f["url"], re.I)))
    ]

    return {
        "title": info.get("title") or url,
        "id": info.get("id"),
        "video_options": video_options,
        "audio_option": best_audio,
        "slideshow_images": slideshow_images,
        "ffmpeg_hint": ffmpeg_hint,
    }


def download_images(image_formats, outdir, prefix="image"):
    """دانلود چند فایل عکس (برای اسلایدشوی تیک‌تاک و مشابه). خروجی: لیست مسیر فایل‌ها."""
    ensure_dir(outdir)
    paths = []
    for i, f in enumerate(image_formats, 1):
        url = f.get("url")
        if not url:
            continue
        ext = f.get("ext") or "jpg"
        filename = safe_filename(f"{prefix}_{i:02d}.{ext}")
        try:
            paths.append(stream_download(url, outdir, filename=filename, headers=f.get("http_headers") or {}))
        except Exception:
            continue
    if not paths:
        raise ProviderError("هیچ‌کدام از عکس‌ها دانلود نشدند")
    return paths


def ytdlp_download_format(url, outdir, format_selector, audio_only=False):
    """دانلود با یک format_id/selector مشخص (یا bestaudio برای فقط-صدا، یا 'videoId+bestaudio' برای merge)."""
    ensure_dir(outdir)
    opts = {
        "outtmpl": _outtmpl(outdir),
        "noplaylist": True, "quiet": True, "no_warnings": True,
        "retries": DOWNLOAD_RETRIES, "fragment_retries": DOWNLOAD_RETRIES, "socket_timeout": DOWNLOAD_SOCKET_TIMEOUT,
        "concurrent_fragment_downloads": 8, "http_chunk_size": 10 * 1024 * 1024,
        "merge_output_format": "mp4",
    }
    if which("aria2c"):
        opts["external_downloader"] = "aria2c"
        opts["external_downloader_args"] = {"aria2c": ["-x", "8", "-s", "8", "-k", "1M"]}
    cf = find_cookiefile()
    if cf:
        opts["cookiefile"] = cf
    if audio_only:
        opts["format"] = "bestaudio/best"
        if which("ffmpeg"):
            opts["postprocessors"] = [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
    else:
        opts["format"] = format_selector

    if not has_module("yt_dlp"):
        raise ProviderError("yt-dlp نصب نیست")
    import yt_dlp
    before = set(os.listdir(outdir))
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    after = set(os.listdir(outdir))
    new_files = [os.path.join(outdir, f) for f in (after - before)]
    if not new_files:
        raise ProviderError("دانلود با فرمت انتخابی فایلی تولید نکرد")
    return new_files


def convert_audio_to_mp3(path):
    """تبدیل فایل صوتی دانلودشده (webm/m4a/opus) به mp3 با ffmpeg. اگر ffmpeg نصب نباشد خطا می‌دهد."""
    if not which("ffmpeg"):
        raise ProviderError("ffmpeg نصب نیست")
    base, _ = os.path.splitext(path)
    out_path = base + ".mp3"
    cmd = ["ffmpeg", "-y", "-i", path, "-vn", "-ar", "44100", "-ac", "2", "-b:a", "192k", out_path]
    try:
        subprocess.run(cmd, check=True, timeout=300,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        raise ProviderError(f"تبدیل به mp3 ناموفق: {e.stderr.decode(errors='ignore')[:200]}")
    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        try:
            os.remove(path)
        except OSError:
            pass
        return out_path
    raise ProviderError("تبدیل به mp3 فایلی تولید نکرد")


def direct_stream_from_format(fmt, outdir, title_hint="download"):
    """
    سریع‌ترین راه: اگر فرمتِ انتخاب‌شده یک URL مستقیم HTTP دارد (اکثر فرمت‌های
    progressive این‌طورند)، به‌جای صدا زدن دوباره‌ی yt-dlp (که یعنی استخراج مجدد
    کل صفحه)، مستقیم فایل را stream می‌کنیم. این کار یک مرحله‌ی کامل شبکه‌ای را
    حذف می‌کند و دانلود را قابل‌توجه سریع‌تر می‌کند.
    """
    if fmt.get("protocol") not in ("https", "http"):
        raise ProviderError("این فرمت نیاز به دانلود جریانی (yt-dlp) دارد، نه دانلود مستقیم")
    url = fmt.get("url")
    if not url:
        raise ProviderError("این فرمت URL مستقیم ندارد")
    ext = fmt.get("ext", "mp4")
    filename = safe_filename(f"{title_hint}.{ext}")
    headers = fmt.get("http_headers") or {}
    ensure_dir(outdir)
    return stream_download(url, outdir, filename=filename, headers=headers)


def download_chosen_format(url, outdir, fmt=None, format_id=None, audio_only=False, title_hint="download"):
    """
    نقطه‌ی ورودی مشترک برای CLI و بات هنگام دانلود یک کیفیت انتخاب‌شده.
    - برای فرمت‌های progressive (ویدیو+صدا در یک فایل): ابتدا سریع‌ترین راه
      (دانلود مستقیم از روی fmt) را امتحان می‌کند؛ اگر شکست خورد، با yt-dlp ادامه می‌دهد.
    - برای فرمت‌های adaptive که نیاز به ادغام دارند (fmt["_needs_merge"]==True، مثل
      کیفیت‌های 1080p+/4K یوتیوب): مسیر مستقیم اصلاً امتحان نمی‌شود (چون فایل بی‌صدا
      تولید می‌کند)؛ مستقیماً با yt-dlp و فرمت‌ترکیبی "videoId+bestaudio" merge می‌شود.
    خروجی: dict {ok, files, used, errors} هم‌شکل با core.download()
    """
    errors = []
    needs_merge = bool(fmt) and fmt.get("_needs_merge")

    if fmt is not None and not needs_merge:
        try:
            path = direct_stream_from_format(fmt, outdir, title_hint=title_hint)
            used_label = "direct-url (سریع)"
            if audio_only and not path.lower().endswith(".mp3"):
                if which("ffmpeg"):
                    try:
                        path = convert_audio_to_mp3(path)
                        used_label += " + mp3"
                    except Exception as e:
                        errors.append(f"mp3 conversion: {e} (فایل با فرمت اصلی نگه داشته شد)")
                else:
                    errors.append("ffmpeg نصب نیست؛ صدا با فرمت اصلی (نه mp3) ذخیره شد")
            return {"ok": True, "files": [path], "used": used_label, "errors": errors}
        except Exception as e:
            errors.append(f"direct-url: {e}")

    try:
        if needs_merge:
            merge_selector = f"{format_id}+bestaudio/best"
            files = ytdlp_download_format(url, outdir, merge_selector, audio_only=False)
            return {"ok": True, "files": files, "used": "yt-dlp (ادغام ویدیو+صدا)", "errors": errors}
        if format_id is None and not audio_only:
            files = ytdlp_download(url, outdir)
        else:
            files = ytdlp_download_format(url, outdir, format_id or "best", audio_only=audio_only)
        return {"ok": True, "files": files, "used": "yt-dlp", "errors": errors}
    except Exception as e:
        errors.append(str(e))
        return {"ok": False, "files": [], "used": None, "errors": errors}


# ───────────────────────── ۱) یوتیوب ─────────────────────────

def yt_p1_ytdlp(url, outdir):
    return ytdlp_download(url, outdir)

def yt_p2_ytdlp_tv_client(url, outdir):
    """کلاینت tv معمولاً به‌روزترین راه برای دور زدن 'Sign in to confirm' است."""
    return ytdlp_download(url, outdir, extra_opts={
        "extractor_args": {"youtube": {"player_client": ["tv"]}},
        "format": "best",
    })

def yt_p3_ytdlp_ios_client(url, outdir):
    return ytdlp_download(url, outdir, extra_opts={
        "extractor_args": {"youtube": {"player_client": ["ios"]}},
        "format": "best",
    })

def yt_p4_ytdlp_android_client(url, outdir):
    return ytdlp_download(url, outdir, extra_opts={
        "extractor_args": {"youtube": {"player_client": ["android"]}},
        "format": "best",
    })

def yt_p5_pytube(url, outdir):
    if not has_module("pytube"):
        raise ProviderError("pytube نصب نیست")
    from pytube import YouTube
    ensure_dir(outdir)
    try:
        yt = YouTube(url)
        stream = yt.streams.get_highest_resolution() or yt.streams.first()
        if not stream:
            raise ProviderError("pytube: هیچ استریمی پیدا نشد")
        path = stream.download(output_path=outdir)
        return [path]
    except Exception as e:
        raise ProviderError(f"pytube: {e}")

INVIDIOUS_INSTANCES = [
    "https://invidious.nerdvpn.de",
    "https://yewtu.be",
    "https://inv.nadeko.net",
    "https://iv.ggtyler.dev",
    "https://invidious.privacyredirect.com",
    "https://inv.tux.pizza",
    "https://invidious.materialio.us",
    "https://invidious.jing.rocks",
]
# نکته: این‌ها سرورهای عمومی رایگانند و ممکن است هرکدام گاهی از دسترس خارج شوند؛
# اگر همه شکست خوردند، اسم یک اینستنس سالم (جستجوی "invidious instances list") را
# به ابتدای این لیست اضافه کن.

def yt_p6_invidious(url, outdir):
    m = re.search(r"(?:v=|youtu\.be/|shorts/)([\w-]{11})", url)
    if not m:
        raise ProviderError("invidious: video id پیدا نشد")
    vid = m.group(1)
    last_err = None
    for inst in INVIDIOUS_INSTANCES:
        try:
            r = requests.get(f"{inst}/api/v1/videos/{vid}", headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json()
            fmts = data.get("formatStreams") or []
            if not fmts:
                fmts = data.get("adaptiveFormats") or []
            if not fmts:
                continue
            best = fmts[0]
            title = safe_filename(data.get("title", vid))
            ext = "mp4"
            fpath = stream_download(best["url"], outdir, filename=f"{title}.{ext}")
            return [fpath]
        except Exception as e:
            last_err = e
            continue
    raise ProviderError(f"invidious: هیچ اینستنسی جواب نداد ({last_err})")

YOUTUBE_PROVIDERS = [
    yt_p1_ytdlp, yt_p2_ytdlp_tv_client, yt_p3_ytdlp_ios_client,
    yt_p4_ytdlp_android_client, yt_p5_pytube, yt_p6_invidious,
]


# ───────────────────────── ۲) تیک‌تاک ─────────────────────────

def tt_p1_ytdlp(url, outdir):
    return ytdlp_download(url, outdir)

def tt_p2_tikwm(url, outdir):
    try:
        r = requests.post("https://www.tikwm.com/api/", data={"url": url},
                           headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            raise ProviderError(f"tikwm: {data.get('msg')}")
        play = data["data"].get("play") or data["data"].get("hdplay")
        if not play:
            raise ProviderError("tikwm: لینک ویدیو پیدا نشد")
        if play.startswith("/"):
            play = "https://www.tikwm.com" + play
        title = safe_filename(data["data"].get("title") or "tiktok")
        return [stream_download(play, outdir, filename=f"{title}.mp4")]
    except ProviderError:
        raise
    except Exception as e:
        raise ProviderError(f"tikwm: {e}")

def tt_p3_ssstik(url, outdir):
    try:
        html, _ = fetch_html("https://ssstik.io/en", headers=HEADERS)
        m = re.search(r'name="tt" value="([^"]+)"', html)
        tt = m.group(1) if m else ""
        r = requests.post(
            "https://ssstik.io/abc?url=dl",
            data={"id": url, "locale": "en", "tt": tt},
            headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
            timeout=TIMEOUT)
        r.raise_for_status()
        m = re.search(r'href="(https?://[^"]+)"[^>]*>\s*(?:Download|بدون واترمارک)', r.text, re.I)
        if not m:
            m = re.search(r'href="(https?://tikcdn[^"]+)"', r.text)
        if not m:
            raise ProviderError("ssstik: لینک دانلود پیدا نشد")
        return [stream_download(m.group(1), outdir, filename="tiktok_ssstik.mp4")]
    except ProviderError:
        raise
    except Exception as e:
        raise ProviderError(f"ssstik: {e}")

TIKTOK_PROVIDERS = [tt_p1_ytdlp, tt_p2_tikwm, tt_p3_ssstik]


# ───────────────────────── ۳) پینترست ─────────────────────────

def pin_p1_ytdlp(url, outdir):
    return ytdlp_download(url, outdir)

def pin_p2_scrape(url, outdir):
    try:
        html, final_url = fetch_html(url)
        vid = find_meta(html, "og:video") or find_meta(html, "og:video:url")
        if vid:
            return [stream_download(vid, outdir, filename="pinterest.mp4")]
        m = re.search(r'"url":"(https:\\/\\/i\.pinimg\.com\\/originals\\/[^"]+)"', html)
        if m:
            img_url = m.group(1).replace("\\/", "/")
            return [stream_download(img_url, outdir)]
        img = find_meta(html, "og:image")
        if img:
            return [stream_download(img, outdir)]
        raise ProviderError("pinterest scrape: مدیایی پیدا نشد")
    except ProviderError:
        raise
    except Exception as e:
        raise ProviderError(f"pinterest scrape: {e}")

PINTEREST_PROVIDERS = [pin_p1_ytdlp, pin_p2_scrape]


# ───────────────────────── ۴) Sora ─────────────────────────

def sora_p1_meta(url, outdir):
    try:
        html, _ = fetch_html(url)
        vid = find_meta(html, "og:video") or find_meta(html, "og:video:secure_url")
        if not vid:
            raise ProviderError("sora meta: og:video پیدا نشد")
        return [stream_download(vid, outdir, filename="sora.mp4")]
    except ProviderError:
        raise
    except Exception as e:
        raise ProviderError(f"sora meta: {e}")

def sora_p2_regex(url, outdir):
    try:
        html, _ = fetch_html(url)
        m = re.search(r'https?://[^\s"\'\\]+\.mp4[^\s"\'\\]*', html)
        if not m:
            raise ProviderError("sora regex: لینک mp4 پیدا نشد در HTML")
        link = m.group(0).replace("\\u002F", "/").replace("\\/", "/")
        return [stream_download(link, outdir, filename="sora.mp4")]
    except ProviderError:
        raise
    except Exception as e:
        raise ProviderError(f"sora regex: {e}")

SORA_PROVIDERS = [sora_p1_meta, sora_p2_regex]
# نکته: sora.chatgpt.com بسیار وابسته به جاوااسکریپت است؛ اگر هر دو روش شکست خورد
# یعنی لینک نیاز به لاگین دارد یا با JS رندر می‌شود (خارج از توان دانلود ساده).


# ───────────────────────── ۵) اینستاگرام ─────────────────────────

def ig_p1_ytdlp(url, outdir):
    return ytdlp_download(url, outdir)

def ig_p2_instaloader(url, outdir):
    if not has_module("instaloader"):
        raise ProviderError("instaloader نصب نیست")
    import instaloader
    m = re.search(r"/(p|reel|tv)/([^/?]+)", url)
    if not m:
        raise ProviderError("instaloader: shortcode پیدا نشد")
    shortcode = m.group(2)
    ensure_dir(outdir)
    try:
        L = instaloader.Instaloader(dirname_pattern=outdir, save_metadata=False,
                                     download_comments=False, post_metadata_txt_pattern="")
        post = instaloader.Post.from_shortcode(L.context, shortcode)
        before = set(os.listdir(outdir)) if os.path.exists(outdir) else set()
        L.download_post(post, target=shortcode)
        after = set(os.listdir(outdir))
        new_files = [os.path.join(outdir, f) for f in (after - before)]
        if not new_files:
            raise ProviderError("instaloader: فایلی تولید نشد")
        return new_files
    except Exception as e:
        raise ProviderError(f"instaloader: {e}")

def ig_p3_scrape(url, outdir):
    try:
        html, _ = fetch_html(url)
        vid = find_meta(html, "og:video")
        if vid:
            return [stream_download(vid, outdir, filename="instagram.mp4")]
        img = find_meta(html, "og:image")
        if img:
            return [stream_download(img, outdir)]
        raise ProviderError("instagram scrape: مدیایی پیدا نشد (احتمالا نیاز به لاگین دارد)")
    except ProviderError:
        raise
    except Exception as e:
        raise ProviderError(f"instagram scrape: {e}")

INSTAGRAM_PROVIDERS = [ig_p1_ytdlp, ig_p2_instaloader, ig_p3_scrape]


# ───────────────────────── ۶) ایکس/توییتر ─────────────────────────

def tw_p1_ytdlp(url, outdir):
    return ytdlp_download(url, outdir)

def _twitter_status_id(url):
    m = re.search(r"status(?:es)?/(\d+)", url)
    return m.group(1) if m else None

def tw_p2_vxtwitter(url, outdir):
    sid = _twitter_status_id(url)
    if not sid:
        raise ProviderError("vxtwitter: id پیدا نشد")
    try:
        api = url.replace("twitter.com", "api.vxtwitter.com").replace("x.com", "api.vxtwitter.com")
        r = requests.get(api, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        medias = data.get("media_extended") or []
        if not medias:
            raise ProviderError("vxtwitter: مدیایی پیدا نشد")
        out = []
        for i, med in enumerate(medias):
            link = med.get("url")
            if link:
                out.append(stream_download(link, outdir, filename=f"tweet_{sid}_{i}.mp4"))
        if not out:
            raise ProviderError("vxtwitter: دانلود ناموفق")
        return out
    except ProviderError:
        raise
    except Exception as e:
        raise ProviderError(f"vxtwitter: {e}")

def tw_p3_fxtwitter(url, outdir):
    sid = _twitter_status_id(url)
    if not sid:
        raise ProviderError("fxtwitter: id پیدا نشد")
    try:
        r = requests.get(f"https://api.fxtwitter.com/status/{sid}", headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        medias = (data.get("tweet") or {}).get("media", {}).get("all", [])
        if not medias:
            raise ProviderError("fxtwitter: مدیایی پیدا نشد")
        out = []
        for i, med in enumerate(medias):
            link = med.get("url")
            if link:
                out.append(stream_download(link, outdir, filename=f"tweet_{sid}_{i}.mp4"))
        if not out:
            raise ProviderError("fxtwitter: دانلود ناموفق")
        return out
    except ProviderError:
        raise
    except Exception as e:
        raise ProviderError(f"fxtwitter: {e}")

def tw_p4_syndication(url, outdir):
    sid = _twitter_status_id(url)
    if not sid:
        raise ProviderError("syndication: id پیدا نشد")
    try:
        r = requests.get(
            f"https://cdn.syndication.twimg.com/tweet-result?id={sid}&token=a",
            headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        ents = (data.get("mediaDetails") or [])
        out = []
        for i, med in enumerate(ents):
            variants = med.get("video_info", {}).get("variants", [])
            mp4s = [v for v in variants if v.get("content_type") == "video/mp4"]
            if mp4s:
                best = max(mp4s, key=lambda v: v.get("bitrate", 0))
                out.append(stream_download(best["url"], outdir, filename=f"tweet_{sid}_{i}.mp4"))
            elif med.get("media_url_https"):
                out.append(stream_download(med["media_url_https"], outdir))
        if not out:
            raise ProviderError("syndication: مدیایی پیدا نشد")
        return out
    except ProviderError:
        raise
    except Exception as e:
        raise ProviderError(f"syndication: {e}")

TWITTER_PROVIDERS = [tw_p1_ytdlp, tw_p2_vxtwitter, tw_p3_fxtwitter, tw_p4_syndication]


# ───────────────────────── ۷) گوگل‌پلی (دانلود APK) ─────────────────────────

def _extract_package_id(url_or_id):
    if re.fullmatch(r"[\w.]+", url_or_id) and "." in url_or_id and "/" not in url_or_id:
        return url_or_id
    q = parse_qs(urlparse(url_or_id).query)
    if "id" in q:
        return q["id"][0]
    raise ProviderError("package id پیدا نشد (لینک play.google.com/store/apps/details?id=... بفرستید)")


def _validate_apk_or_raise(path, provider_name):
    """
    فایل APK واقعی همیشه با امضای ZIP شروع می‌شود (چون APK خودش یک بایگانی ZIP است).
    این چک دقیقاً همان مشکلی را می‌گیرد که باعث می‌شد یک صفحه‌ی HTML/خطا (مثلاً از یک
    آینه‌ی از کار افتاده) به‌جای APK واقعی «موفق» گزارش شود و برای کاربر ارسال شود.
    """
    ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
    try:
        with open(path, "rb") as f:
            head = f.read(4)
    except OSError as e:
        raise ProviderError(f"{provider_name}: نتوانستم فایل دانلودشده را بخوانم ({e})")
    if head not in ZIP_SIGNATURES:
        try:
            os.remove(path)
        except OSError:
            pass
        raise ProviderError(
            f"{provider_name}: فایل دانلودشده یک APK واقعی نبود (احتمالاً آینه یک صفحه‌ی خطا/تبلیغ برگردانده)")
    return path


def gp_p1_apkpure_direct(url, outdir):
    pkg = _extract_package_id(url)
    link = f"https://d.apkpure.com/b/APK/{pkg}?version=latest"
    try:
        path = stream_download(link, outdir, filename=f"{pkg}.apk")
        return [_validate_apk_or_raise(path, "apkpure direct")]
    except ProviderError:
        raise
    except Exception as e:
        raise ProviderError(f"apkpure direct: {e}")

def gp_p2_apkpure_scrape(url, outdir):
    pkg = _extract_package_id(url)
    try:
        html, _ = fetch_html(f"https://apkpure.com/x/{pkg}")
        m = re.search(r'href="(https?://download\.apkpure\.com/[^"]+)"', html)
        if not m:
            m = re.search(r'href="(https?://[^"]*apkpure[^"]*\.apk[^"]*)"', html)
        if not m:
            raise ProviderError("apkpure scrape: لینک دانلود پیدا نشد")
        path = stream_download(m.group(1), outdir, filename=f"{pkg}.apk")
        return [_validate_apk_or_raise(path, "apkpure scrape")]
    except ProviderError:
        raise
    except Exception as e:
        raise ProviderError(f"apkpure scrape: {e}")

def gp_p3_apkcombo(url, outdir):
    pkg = _extract_package_id(url)
    try:
        html, _ = fetch_html(f"https://apkcombo.com/checkin/{pkg}/")
        m = re.search(r'href="([^"]+\.apk[^"]*)"', html)
        if not m:
            raise ProviderError("apkcombo: لینک دانلود پیدا نشد")
        link = m.group(1)
        if link.startswith("/"):
            link = "https://apkcombo.com" + link
        path = stream_download(link, outdir, filename=f"{pkg}.apk")
        return [_validate_apk_or_raise(path, "apkcombo")]
    except ProviderError:
        raise
    except Exception as e:
        raise ProviderError(f"apkcombo: {e}")

GOOGLEPLAY_PROVIDERS = [gp_p1_apkpure_direct, gp_p2_apkpure_scrape, gp_p3_apkcombo]
# نکته: گوگل پلی مستقیماً APK نمی‌دهد؛ این سایت‌ها آینه‌ی (mirror) عمومی هستند و
# ممکن است روی برخی اپ‌های محافظت‌شده/پولی کار نکنند.


# ───────────────────────── ۸) گیت‌هاب ─────────────────────────

def gh_p1_direct_asset(url, outdir):
    if "/releases/download/" not in url and "raw.githubusercontent.com" not in url:
        raise ProviderError("github direct: این یک لینک مستقیم asset/raw نیست")
    return [stream_download(url, outdir)]

def gh_p2_latest_release(url, outdir):
    m = re.search(r"github\.com/([^/]+)/([^/]+?)(?:\.git)?(?:/|$)", url)
    if not m:
        raise ProviderError("github release: owner/repo پیدا نشد")
    owner, repo = m.group(1), m.group(2)
    try:
        r = requests.get(f"https://api.github.com/repos/{owner}/{repo}/releases/latest",
                          headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        assets = data.get("assets") or []
        if not assets:
            raise ProviderError("github release: هیچ assetای در آخرین ریلیز نیست")
        out = []
        for a in assets:
            out.append(stream_download(a["browser_download_url"], outdir, filename=a["name"]))
        return out
    except ProviderError:
        raise
    except Exception as e:
        raise ProviderError(f"github release: {e}")

def gh_p3_zipball(url, outdir):
    m = re.search(r"github\.com/([^/]+)/([^/]+?)(?:\.git)?(?:/|$)", url)
    if not m:
        raise ProviderError("github zipball: owner/repo پیدا نشد")
    owner, repo = m.group(1), m.group(2)
    for branch in ("main", "master"):
        try:
            link = f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{branch}"
            return [stream_download(link, outdir, filename=f"{repo}-{branch}.zip")]
        except Exception:
            continue
    raise ProviderError("github zipball: دانلود zip ناموفق (main/master)")

GITHUB_PROVIDERS = [gh_p1_direct_asset, gh_p2_latest_release, gh_p3_zipball]


# ───────────────────────── ردیت ─────────────────────────

def reddit_p1_ytdlp(url, outdir):
    return ytdlp_download(url, outdir)

def reddit_p2_media_unwrap(url, outdir):
    """
    لینک‌های reddit.com/media?url=... فقط یک wrapper دور یک لینک مستقیم (معمولاً
    i.redd.it/v.redd.it) هستند. به‌جای اینکه yt-dlp/redditsave را درگیرش کنیم،
    مستقیم پارامتر url را دربیاور و دانلودش کن — سریع‌تر و بدون رفتن سراغ API ردیت.
    """
    q = parse_qs(urlparse(url).query)
    inner = q.get("url", [None])[0]
    if not inner:
        raise ProviderError("media unwrap: پارامتر url در لینک پیدا نشد")
    inner = unquote(inner)
    try:
        return [stream_download(inner, outdir)]
    except Exception as e:
        raise ProviderError(f"media unwrap: {e}")

def reddit_p3_redditsave(url, outdir):
    """آینه‌ی عمومی redditsave.com برای وقتی yt-dlp به هر دلیلی جواب نداد (مثلاً 429)."""
    try:
        r = requests.post("https://redditsave.com/info",
                           data={"url": url}, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        html = r.text
        m = re.search(r'href="(https?://[^"]+\.mp4[^"]*)"', html)
        if not m:
            m = re.search(r'href="(https?://v\.redd\.it/[^"]+)"', html)
        if not m:
            raise ProviderError("redditsave: لینک دانلود پیدا نشد")
        return [stream_download(m.group(1), outdir, filename="reddit.mp4")]
    except ProviderError:
        raise
    except Exception as e:
        raise ProviderError(f"redditsave: {e}")

def reddit_p4_json_api(url, outdir):
    """
    fallback مستقل: API عمومی JSON خودِ ردیت (نیازی به کلید ندارد). چون این یک
    مسیر متفاوت از extractor داخلی yt-dlp است، وقتی yt-dlp با 429 مواجه می‌شود
    (rate-limit خود ردیت روی همون مسیر)، این روش گاهی همچنان جواب می‌دهد.
    """
    try:
        clean = url.split("?")[0].rstrip("/")
        r = requests.get(clean + ".json", headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        post = data[0]["data"]["children"][0]["data"]
        media_url = None
        if post.get("is_video") and post.get("media", {}).get("reddit_video"):
            media_url = post["media"]["reddit_video"].get("fallback_url")
        if not media_url:
            url_overridden = post.get("url_overridden_by_dest") or post.get("url")
            if url_overridden and re.search(r"\.(mp4|gif|jpg|jpeg|png|webp)(\?|$)", url_overridden, re.I):
                media_url = url_overridden
        if not media_url:
            raise ProviderError("json api: مدیایی در پست پیدا نشد")
        return [stream_download(media_url, outdir)]
    except ProviderError:
        raise
    except Exception as e:
        raise ProviderError(f"json api: {e}")

REDDIT_PROVIDERS = [reddit_p2_media_unwrap, reddit_p1_ytdlp, reddit_p3_redditsave, reddit_p4_json_api]


# ───────────────────────── گوگل‌درایو ─────────────────────────

def _gdrive_file_id(url):
    m = re.search(r"/file/d/([\w-]+)", url) or re.search(r"[?&]id=([\w-]+)", url)
    if not m:
        raise ProviderError("googledrive: شناسه‌ی فایل پیدا نشد")
    return m.group(1)

def gdrive_p1_ytdlp(url, outdir):
    return ytdlp_download(url, outdir)

def gdrive_p2_direct(url, outdir):
    """
    دانلود مستقیم گوگل‌درایو با هندل‌کردن صفحه‌ی تایید ویروس‌اسکن برای فایل‌های بزرگ.
    فقط برای فایل‌های عمومی (Anyone with the link) کار می‌کند.
    """
    file_id = _gdrive_file_id(url)
    session = requests.Session()
    base = "https://drive.google.com/uc?export=download"
    try:
        r = session.get(base, params={"id": file_id}, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        token = None
        m = re.search(r'confirm=([0-9A-Za-z_-]+)&', r.text)
        if m:
            token = m.group(1)
        else:
            for k, v in r.cookies.items():
                if k.startswith("download_warning"):
                    token = v
        if token:
            r = session.get(base, params={"id": file_id, "confirm": token},
                             headers=HEADERS, timeout=TIMEOUT, stream=True)
        else:
            r = session.get(base, params={"id": file_id}, headers=HEADERS, timeout=TIMEOUT, stream=True)
        r.raise_for_status()
        cd = r.headers.get("content-disposition", "")
        m2 = re.search(r'filename="?([^";]+)"?', cd)
        filename = safe_filename(m2.group(1)) if m2 else f"gdrive_{file_id}"
        ensure_dir(outdir)
        fpath = os.path.join(outdir, filename)
        with open(fpath, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                if chunk:
                    f.write(chunk)
        if os.path.getsize(fpath) == 0:
            raise ProviderError("گوگل‌درایو: فایل خالی دریافت شد (احتمالاً لینک عمومی نیست)")
        return [fpath]
    except ProviderError:
        raise
    except Exception as e:
        raise ProviderError(f"googledrive direct: {e}")

GOOGLEDRIVE_PROVIDERS = [gdrive_p1_ytdlp, gdrive_p2_direct]


# ───────────────────────── تراباکس ─────────────────────────
# نکته‌ی مهم: Terabox هیچ API رسمی‌ای ندارد و لینک‌های دانلود واقعی پشت جاوااسکریپت/امضای
# موقت هستند. آینه‌های عمومی زیر مرتب تغییر می‌کنند و ممکن است در آینده از کار بیفتند؛
# اگر این بخش شکست خورد، نیاز به به‌روزرسانی آدرس آینه دارد.

def terabox_p1_ytdlp(url, outdir):
    return ytdlp_download(url, outdir)

def terabox_p2_mirror(url, outdir):
    try:
        api = "https://terabox.hnn.workers.dev/api/get-info"
        r = requests.get(api, params={"shorturl": url}, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        link = None
        if isinstance(data, dict):
            link = data.get("downloadLink") or data.get("download_url") or data.get("url")
        if not link:
            raise ProviderError("terabox mirror: لینک دانلود پیدا نشد (ممکن است آینه از کار افتاده باشد)")
        return [stream_download(link, outdir, filename="terabox_file")]
    except ProviderError:
        raise
    except Exception as e:
        raise ProviderError(f"terabox mirror: {e}")

TERABOX_PROVIDERS = [terabox_p1_ytdlp, terabox_p2_mirror]
# اگر هر دو روش شکست خورد، احتمالاً لینک نیاز به تعامل مرورگر (captcha/لاگین) دارد
# که خارج از توان یک دانلودر خط‌فرمانی ساده است.


# ───────────────────────── ۹) عمومی/سایر ─────────────────────────

def other_p1_ytdlp(url, outdir):
    return ytdlp_download(url, outdir)

def other_p2_gallery_dl(url, outdir):
    return gallery_dl_download(url, outdir)

def other_p3_direct(url, outdir):
    try:
        return [stream_download(url, outdir)]
    except Exception as e:
        raise ProviderError(f"direct download: {e}")

OTHER_PROVIDERS = [other_p1_ytdlp, other_p2_gallery_dl, other_p3_direct]


# ───────────────────────── تشخیص دسته‌بندی از روی URL ─────────────────────────

CATEGORY_DOMAINS = {
    "youtube":     ["youtube.com", "youtu.be"],
    "tiktok":      ["tiktok.com"],
    "pinterest":   ["pinterest.com", "pin.it"],
    "sora":        ["sora.chatgpt.com", "sora.com"],
    "instagram":   ["instagram.com"],
    "twitter":     ["twitter.com", "x.com"],
    "googleplay":  ["play.google.com"],
    "github":      ["github.com", "raw.githubusercontent.com", "codeload.github.com"],
    "reddit":      ["reddit.com", "redd.it"],
    "googledrive": ["drive.google.com"],
    "terabox":     ["terabox.com", "1024terabox.com", "teraboxapp.com"],
}

PROVIDER_MAP = {
    "youtube":     YOUTUBE_PROVIDERS,
    "tiktok":      TIKTOK_PROVIDERS,
    "pinterest":   PINTEREST_PROVIDERS,
    "sora":        SORA_PROVIDERS,
    "instagram":   INSTAGRAM_PROVIDERS,
    "twitter":     TWITTER_PROVIDERS,
    "googleplay":  GOOGLEPLAY_PROVIDERS,
    "github":      GITHUB_PROVIDERS,
    "reddit":      REDDIT_PROVIDERS,
    "googledrive": GOOGLEDRIVE_PROVIDERS,
    "terabox":     TERABOX_PROVIDERS,
    "other":       OTHER_PROVIDERS,
}

CATEGORY_FA = {
    "youtube": "یوتیوب", "tiktok": "تیک‌تاک", "pinterest": "پینترست",
    "sora": "Sora", "instagram": "اینستاگرام", "twitter": "ایکس/توییتر",
    "googleplay": "گوگل‌پلی", "github": "گیت‌هاب", "reddit": "ردیت",
    "googledrive": "گوگل‌درایو", "terabox": "تراباکس", "other": "عمومی",
}

def detect_category(url):
    """
    تشخیص دسته‌بندی بر اساس دامنه‌ی دقیق (نه جستجوی زیررشته‌ای متنی)، تا مثلاً
    "1024terabox.com" به‌اشتباه با الگوی "x.com" قاطی نشود.
    """
    try:
        netloc = (urlparse(url).netloc or "").lower()
    except Exception:
        netloc = ""
    if netloc.startswith("www."):
        netloc = netloc[4:]
    if not netloc:
        return "other"
    for cat, domains in CATEGORY_DOMAINS.items():
        for d in domains:
            if netloc == d or netloc.endswith("." + d):
                return cat
    return "other"


# ───────────────────────── موتور اصلی با fallback ─────────────────────────

def download(url, outdir, category=None, on_step=None):
    """
    تلاش برای دانلود url با زنجیره‌ی fallback مربوط به دسته‌ی تشخیص داده‌شده.
    on_step(provider_name, index, total): اختیاری، برای گزارش پیشرفت (مثلا در بات تلگرام).

    خروجی: dict {
        ok: bool, category: str, files: [paths], used: provider_name یا None,
        errors: [ "provider: پیام خطا", ... ]
    }
    """
    cat = category or detect_category(url)
    providers = PROVIDER_MAP.get(cat, OTHER_PROVIDERS)
    ensure_dir(outdir)
    errors = []
    total = len(providers)
    for i, provider in enumerate(providers, 1):
        name = provider.__name__
        if on_step:
            try:
                on_step(name, i, total)
            except Exception:
                pass
        log.info(f"[{cat}] در حال تلاش با provider {i}/{total}: {name}")
        try:
            files = provider(url, outdir)
            files = [f for f in files if f and os.path.exists(f) and os.path.getsize(f) > 0]
            if files:
                log.info(f"[{cat}] موفق با {name}: {len(files)} فایل")
                return {"ok": True, "category": cat, "files": files,
                        "used": name, "errors": errors}
            errors.append(f"{name}: فایلی تولید نشد")
        except ProviderError as e:
            errors.append(str(e))
            log.warning(f"[{cat}] {name} شکست خورد: {e}")
        except Exception as e:
            errors.append(f"{name}: خطای غیرمنتظره: {e}")
            log.warning(f"[{cat}] {name} خطای غیرمنتظره: {e}")
    return {"ok": False, "category": cat, "files": [], "used": None, "errors": errors}


def check_deps():
    """بررسی و چاپ وضعیت پیش‌نیازها (برای اجرای مستقیم multidl_core.py)."""
    rows = [
        ("yt-dlp (module)", has_module("yt_dlp")),
        ("yt-dlp (cli)",     which("yt-dlp")),
        ("requests",         has_module("requests")),
        ("pytube (اختیاری)",     has_module("pytube")),
        ("instaloader (اختیاری)", has_module("instaloader")),
        ("gallery-dl (اختیاری)",  which("gallery-dl") or has_module("gallery_dl")),
        ("ffmpeg (اختیاری، merge)", which("ffmpeg")),
        ("cookies.txt (برای دور زدن خطای Sign in)", bool(find_cookiefile())),
    ]
    print("📦 وضعیت پیش‌نیازها:")
    for name, ok in rows:
        print(f"  {'✅' if ok else '⚠️ '} {name}")


if __name__ == "__main__":
    check_deps()
