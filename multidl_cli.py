#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
multidl_cli.py  –  دانلودر چندسایته برای Termux / Pydroid 3 / a-Shell   v2.0

ویژگی‌های این نسخه:
  • فقط لینک را بفرست؛ دسته‌بندی و روش دانلود خودکار انتخاب می‌شود.
  • برای یوتیوب/تیک‌تاک/اینستاگرام/ایکس، قبل از دانلود کیفیت‌ها را نشان می‌دهد
    (شماره‌ها بر اساس کیفیت موجود، گزینه‌ی 9 همیشه = فقط صدا).
  • فایل‌های ویدیویی و صوتی در دو پوشه‌ی جدا داخل Download ذخیره می‌شوند.
  • حالت دسته‌ای: پوشه‌ی Input را اسکن می‌کند (فایل txt چندلینکی، عکس حاوی
    QR کد یا اسکرین‌شات) و همه را پردازش می‌کند؛ موفق‌ها → Complete،
    لینک‌های ناموفق → یک فایل txt در Failed برای تلاش بعدی.

نصب پیش‌نیازها:
    pip install yt-dlp requests
    (اختیاری) pip install pytube instaloader gallery-dl
    (اختیاری، برای خواندن QR/اسکرین‌شات در پردازش دسته‌ای)
        pip install pyzbar pillow pytesseract
        Termux:  pkg install tesseract zbar
"""

import os
import re
import sys
import time
import shutil
import argparse
from pathlib import Path
from urllib.parse import urlparse

_DIR = Path(__file__).parent
sys.path.insert(0, str(_DIR))

try:
    import multidl_core as core
except ImportError:
    print("❌ multidl_core.py پیدا نشد. باید کنار همین فایل باشد.")
    sys.exit(1)

try:
    import multidl_vision as vision
except ImportError:
    print("⚠️  multidl_vision.py پیدا نشد؛ پردازش QR/اسکرین‌شات غیرفعال می‌ماند.")
    vision = None

# ───────────────────────── تشخیص محیط اجرا ─────────────────────────

def detect_runtime():
    """
    تشخیص best-effort محیط اجرا: pydroid / termux / a-shell / macos / linux / windows / unknown.
    فقط برای انتخاب مسیر پیش‌فرض دانلود و نمایش اطلاعاتی استفاده می‌شود؛
    هیچ‌کدام از منطق دانلود (در core) به این تشخیص وابسته نیست.
    """
    try:
        exe = (sys.executable or "").lower()
    except Exception:
        exe = ""
    env = os.environ

    if "pydroid" in exe or os.path.exists("/data/user/0/ru.iiec.pydroid3"):
        return "pydroid"
    if env.get("PREFIX", "").startswith("/data/data/com.termux") or \
            os.path.exists("/data/data/com.termux/files/usr"):
        return "termux"
    if sys.platform == "ios" or "a-shell" in exe or env.get("A_SHELL"):
        return "a-shell"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform.startswith("win"):
        return "windows"
    return "unknown"


RUNTIME = detect_runtime()


# ───────────────────────── مسیرها (قابل‌تنظیم بر اساس پلتفرم) ─────────────────────────

def _detect_default_download_root():
    """انتخاب خودکار پوشه‌ی دانلود مناسب هر پلتفرم؛ همیشه قابل بازنویسی با -o یا متغیر محیطی."""
    android_download = "/storage/emulated/0/Download"
    if os.path.isdir(android_download):
        return android_download
    if RUNTIME == "a-shell":
        ios_docs = os.path.expanduser("~/Documents")
        if os.path.isdir(ios_docs):
            return ios_docs
    home_downloads = os.path.expanduser("~/Downloads")
    if os.path.isdir(home_downloads):
        return home_downloads
    # آخرین راه (مثلاً محیط‌های محدود بدون پوشه‌ی خانگی استاندارد): کنار خود اسکریپت
    return str(_DIR / "Downloads")


def configure_directories(root_override=None):
    """
    مسیرهای کاری را می‌سازد. اگر root_override داده نشود، به ترتیب از متغیر محیطی
    MULTIDL_DOWNLOAD_DIR و سپس تشخیص خودکار پلتفرم استفاده می‌شود.
    در صورت نبود دسترسی نوشتن، بدون کرش کردن به پوشه‌ی کنار اسکریپت سوییچ می‌کند.
    """
    global DOWNLOAD_ROOT, VIDEO_DIR, AUDIO_DIR, IMAGES_DIR, INPUT_DIR, COMPLETE_DIR, FAILED_DIR

    DOWNLOAD_ROOT = root_override or os.environ.get("MULTIDL_DOWNLOAD_DIR") or _detect_default_download_root()
    VIDEO_DIR = os.path.join(DOWNLOAD_ROOT, "multidl_Video")
    AUDIO_DIR = os.path.join(DOWNLOAD_ROOT, "multidl_Audio")
    IMAGES_DIR = os.path.join(DOWNLOAD_ROOT, "multidl_Images")
    INPUT_DIR = str(_DIR / "Input")
    COMPLETE_DIR = str(_DIR / "Complete")
    FAILED_DIR = str(_DIR / "Failed")

    fallback_root = str(_DIR / "Downloads")
    for d in (VIDEO_DIR, AUDIO_DIR, IMAGES_DIR, INPUT_DIR, COMPLETE_DIR, FAILED_DIR):
        try:
            os.makedirs(d, exist_ok=True)
        except (PermissionError, OSError) as e:
            print(f"⚠️  اجازه‌ی ساخت پوشه‌ی {d} نبود ({e}).")
            if d.startswith(DOWNLOAD_ROOT):
                # اگر خود ریشه‌ی دانلود در دسترس نیست، کل مسیرها را به کنار اسکریپت منتقل کن
                DOWNLOAD_ROOT = fallback_root
                VIDEO_DIR = os.path.join(DOWNLOAD_ROOT, "multidl_Video")
                AUDIO_DIR = os.path.join(DOWNLOAD_ROOT, "multidl_Audio")
                IMAGES_DIR = os.path.join(DOWNLOAD_ROOT, "multidl_Images")
                print(f"   → استفاده از مسیر جایگزین: {DOWNLOAD_ROOT}")
                for d2 in (VIDEO_DIR, AUDIO_DIR, IMAGES_DIR, INPUT_DIR, COMPLETE_DIR, FAILED_DIR):
                    os.makedirs(d2, exist_ok=True)
            break


configure_directories()

URL_RE = re.compile(r"https?://[^\s\"'<>]+")

BANNER = """
╔══════════════════════════════════════════╗
║   📥  دانلودر چندسایته  v2.0              ║
╠══════════════════════════════════════════╣
║  لینک را بفرست؛ بقیه‌اش خودکار انجام میشه  ║
║  دستورات:  batch = پردازش پوشه‌ی Input     ║
║            0     = بررسی پیش‌نیازها       ║
║            q     = خروج                   ║
╚══════════════════════════════════════════╝
"""


def validate_url(raw):
    """
    اعتبارسنجی ساده‌ی لینک. خروجی: (url_تمیزشده, None) در صورت معتبر بودن،
    یا (None, پیام_خطا) در غیر این صورت.
    """
    url = (raw or "").strip()
    if not url:
        return None, "لینک خالی است"
    if not url.startswith(("http://", "https://")):
        return None, "لینک باید با http:// یا https:// شروع شود"
    try:
        parsed = urlparse(url)
    except Exception as e:
        return None, f"لینک نامعتبر است ({e})"
    if not parsed.netloc:
        return None, "لینک نامعتبر است (دامنه پیدا نشد)"
    return url, None


def progress_cb(name, i, total):
    print(f"   ⏳ [{i}/{total}] در حال امتحان: {name} ...")


def print_result(result, label=""):
    if result["ok"]:
        print(f"\n✅ دانلود موفق{(' - ' + label) if label else ''} ({result.get('used', '?')})")
        for f in result["files"]:
            try:
                size = os.path.getsize(f) / (1024 * 1024)
                print(f"   📄 {f}  ({size:.1f} MB)")
            except OSError:
                print(f"   📄 {f}")
    else:
        print(f"\n❌ دانلود ناموفق{(' - ' + label) if label else ''}")
        for e in result["errors"]:
            print(f"   • {e}")


# ───────────────────────── دانلود با فرمت انتخابی ─────────────────────────

def build_title_hint(title, vid_id):
    if core.INCLUDE_ID_SUFFIX and vid_id:
        return f"{title} [{vid_id}]"
    return title or "download"


def ask_simple_choice():
    print("   1) 🎬 ویدیو (کیفیت خودکار)")
    print("   2) 🎵 فقط صدا")
    ch = input("انتخاب (Enter = ویدیو): ").strip()
    return "audio" if ch == "2" else "video"


def process_single_link(url, interactive_mode=True):
    """
    خروجی: True اگر دانلود موفق بود، False اگر ناموفق. برای حالت غیرتعاملی
    (Apple Shortcuts و اسکریپت‌های خودکار) این مقدار مبنای exit code است.
    """
    url = url.strip()
    cat = core.detect_category(url)
    print(f"\n🔎 دسته‌بندی: {core.CATEGORY_FA.get(cat, cat)}")

    if cat not in core.QUALITY_MENU_CATEGORIES:
        # سایر دسته‌ها (پینترست/Sora/گوگل‌پلی/گیت‌هاب/ردیت/گوگل‌درایو/تراباکس/عمومی)
        result = core.download(url, VIDEO_DIR, category=cat, on_step=progress_cb)
        print_result(result)
        return result["ok"]

    try:
        info = core.list_video_qualities(url)
    except Exception as e:
        print(f"⚠️  نتونستم لیست کیفیت‌ها رو بگیرم ({e})")
        info = None

    title = (info or {}).get("title") or url
    title_hint = build_title_hint(title, (info or {}).get("id"))

    # حالت ۱: پست چندعکسی (اسلایدشو تیک‌تاک و مشابه)
    if info and info.get("slideshow_images") and not info.get("video_options"):
        imgs = info["slideshow_images"]
        if interactive_mode:
            print(f"🖼  این یک پست چندعکسی/اسلایدشو است ({len(imgs)} عکس + موزیک)")
            print("   1) فقط عکس‌ها")
            print("   2) فقط صدا")
            print("   3) هر دو")
            ch = input("انتخاب (Enter = هر دو): ").strip()
        else:
            print(f"🖼  پست چندعکسی تشخیص داده شد ({len(imgs)} عکس + موزیک) — دانلود خودکار: هر دو")
            ch = "3"
        ok_any = False
        if ch in ("", "1", "3"):
            try:
                paths = core.download_images(imgs, IMAGES_DIR, prefix=core.safe_filename(title))
                print(f"✅ {len(paths)} عکس دانلود شد → {IMAGES_DIR}")
                ok_any = True
            except Exception as e:
                print(f"❌ دانلود عکس‌ها ناموفق: {e}")
        if ch in ("", "2", "3") and info.get("audio_option"):
            result = core.download_chosen_format(
                url, AUDIO_DIR, fmt=info["audio_option"], audio_only=True, title_hint=title_hint)
            print_result(result, label="صوت اسلایدشو")
            ok_any = ok_any or result["ok"]
        if not ok_any:
            print("❌ هیچ‌کدام موفق نشد.")
        return ok_any

    # حالت ۲: کیفیت‌های واقعی ویدیو پیدا شد
    if info and info.get("video_options"):
        print(f"🎬 {title}")
        for i, f in enumerate(info["video_options"], 1):
            size = f.get("filesize") or f.get("filesize_approx")
            size_txt = f"{size/1024/1024:.1f}MB" if size else "حجم نامشخص"
            merge_tag = " (ادغام خودکار صدا+تصویر)" if f.get("_needs_merge") else ""
            print(f"   {i}) {f.get('height', '?')}p{merge_tag}  —  {size_txt}")
        audio = info.get("audio_option")
        audio_txt = f" (~{int(audio.get('abr') or 0)}kbps)" if audio else ""
        print(f"   9) 🎵 فقط صدا{audio_txt}")
        if info.get("ffmpeg_hint"):
            print(f"ℹ️  {info['ffmpeg_hint']}")

        if interactive_mode:
            choice = input("انتخاب کیفیت (Enter = بهترین کیفیت): ").strip()
        else:
            choice = ""
            print("   → حالت غیرتعاملی: بهترین کیفیت خودکار انتخاب شد")

        if choice == "9":
            result = core.download_chosen_format(
                url, AUDIO_DIR, fmt=audio, format_id=None, audio_only=True, title_hint=title_hint)
            print_result(result, label="صوت")
        elif choice.isdigit() and 1 <= int(choice) <= len(info["video_options"]):
            fmt = info["video_options"][int(choice) - 1]
            result = core.download_chosen_format(
                url, VIDEO_DIR, fmt=fmt, format_id=fmt["format_id"], audio_only=False, title_hint=title_hint)
            print_result(result, label=f"{fmt.get('height')}p")
        else:
            # «بهترین کیفیت» یعنی دقیقاً همون گزینه‌ی اول لیستی که نشون دادیم (WYSIWYG)،
            # نه یک انتخاب‌گر جداگانه‌ی yt-dlp که ممکنه چیز کاملاً متفاوتی (و خیلی بزرگ‌تر) بگیره.
            fmt = info["video_options"][0]
            result = core.download_chosen_format(
                url, VIDEO_DIR, fmt=fmt, format_id=fmt["format_id"], audio_only=False, title_hint=title_hint)
            print_result(result, label=f"بهترین کیفیت ({fmt.get('height')}p)")
        return result["ok"]

    # حالت ۳: نتونستیم جزئیات بگیریم (خطای شبکه و ...) یا فرمتی پیدا نشد؛
    # طبق درخواست، بازهم صریحاً می‌پرسیم ویدیو می‌خواهی یا فقط صدا (فقط در حالت تعاملی).
    if interactive_mode:
        print("ℹ️  جزئیات کیفیت در دسترس نبود؛ حالت کلی رو انتخاب کن:")
        choice = ask_simple_choice()
    else:
        print("ℹ️  جزئیات کیفیت در دسترس نبود؛ حالت غیرتعاملی: تلاش خودکار برای دانلود ویدیو")
        choice = "video"

    if choice == "audio":
        fmt = (info or {}).get("audio_option")
        result = core.download_chosen_format(
            url, AUDIO_DIR, fmt=fmt, format_id=None, audio_only=True, title_hint=title_hint)
        print_result(result, label="صوت")
    else:
        result = core.download_chosen_format(
            url, VIDEO_DIR, fmt=None, format_id=None, audio_only=False, title_hint=title_hint)
        if not result["ok"]:
            # تلاش تکی yt-dlp شکست خورد (مثلاً چون این لینک اصلاً پشتیبانی نمیشه، مثل
            # پست‌های عکسی تیک‌تاک). قبل از تسلیم‌شدن، زنجیره‌ی کامل fallback مخصوص
            # همین دسته را هم امتحان کن (برای تیک‌تاک: tikwm/ssstik، برای ایکس:
            # vxtwitter/fxtwitter و غیره) — دقیقاً همون‌هایی که تو حالت عادی هم داریم.
            print("   ↻ تلاش با روش‌های دیگر مخصوص همین سایت...")
            fallback_result = core.download(url, VIDEO_DIR, category=cat, on_step=progress_cb)
            if fallback_result["ok"]:
                result = fallback_result
            else:
                result["errors"] += fallback_result["errors"]
        print_result(result, label="ویدیو")
    return result["ok"]


# ───────────────────────── استخراج لینک از txt / عکس (QR یا OCR) ─────────────────────────

def extract_links_from_txt(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except Exception:
        return []
    return [u for u in URL_RE.findall(text) if not u.strip().startswith("#")]


def batch_process():
    files = [f for f in os.listdir(INPUT_DIR) if os.path.isfile(os.path.join(INPUT_DIR, f))]
    if not files:
        print(f"📭 پوشه‌ی Input خالیه:\n   {INPUT_DIR}")
        return

    print(f"📂 {len(files)} فایل در Input پیدا شد. شروع پردازش...\n")
    failed_lines = []
    ok_count = 0
    fail_count = 0

    for fname in files:
        fpath = os.path.join(INPUT_DIR, fname)
        ext = os.path.splitext(fname)[1].lower()

        if ext == ".txt":
            links = extract_links_from_txt(fpath)
            method_note = ""
        elif ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
            if vision is None:
                links, method = [], None
            else:
                links, method = vision.extract_links_from_image(fpath)
            method_note = f"  [{method}]" if method else ""
        else:
            links, method_note = [], ""

        print(f"📄 {fname} → {len(links)} لینک{method_note}")
        if not links:
            shutil.move(fpath, os.path.join(FAILED_DIR, fname))
            failed_lines.append(f"# {fname}: هیچ لینکی پیدا نشد (QR/OCR ناموفق یا نصب‌نشده)")
            fail_count += 1
            continue

        all_ok = True
        for url in links:
            cat = core.detect_category(url)
            print(f"   ⏳ {url}  ({core.CATEGORY_FA.get(cat, cat)})")
            result = core.download(url, VIDEO_DIR, category=cat)
            if result["ok"]:
                print(f"   ✅ موفق ({result['used']})")
                ok_count += 1
            else:
                print("   ❌ ناموفق")
                failed_lines.append(url)
                all_ok = False
                fail_count += 1

        dest_dir = COMPLETE_DIR if all_ok else FAILED_DIR
        shutil.move(fpath, os.path.join(dest_dir, fname))

    if failed_lines:
        log_path = os.path.join(FAILED_DIR, f"failed_links_{int(time.time())}.txt")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("\n".join(failed_lines) + "\n")
        print(f"\n⚠️  {fail_count} مورد ناموفق. لینک‌ها ذخیره شد در:\n   {log_path}")
        print("   (این فایل txt رو می‌تونی دوباره تو Input بذاری تا بعداً دوباره امتحان بشه)")

    print(f"\n✅ پردازش تمام شد. موفق: {ok_count} | ناموفق: {fail_count}")
    print(f"   ویدیوها: {VIDEO_DIR}")
    print(f"   صداها:   {AUDIO_DIR}")


# ───────────────────────── main ─────────────────────────

def interactive():
    print(BANNER)
    print(f"💻 محیط تشخیص‌داده‌شده: {RUNTIME}")
    print(f"📁 ویدیو → {VIDEO_DIR}")
    print(f"🎵 صدا   → {AUDIO_DIR}")
    print(f"🖼  عکس   → {IMAGES_DIR}")
    print(f"📥 Input  → {INPUT_DIR}\n")
    while True:
        raw = input("🔗 لینک (یا batch / 0 / q): ").strip()
        low = raw.lower()
        if low == "q":
            print("👋 خداحافظ!")
            break
        if low == "batch":
            batch_process()
            continue
        if raw == "0":
            core.check_deps()
            if vision:
                print()
                vision.check_vision_deps()
            continue
        if not raw:
            continue
        url, err = validate_url(raw)
        if err:
            print(f"⚠️  {err}")
            continue
        process_single_link(url)


def build_arg_parser():
    p = argparse.ArgumentParser(
        prog="multidl_cli.py",
        description="دانلودر چندسایته (یوتیوب/تیک‌تاک/اینستاگرام/ایکس/ردیت/گوگل‌درایو/گیت‌هاب/...)",
    )
    p.add_argument("urls", nargs="*",
                    help="یک یا چند لینک برای دانلود مستقیم (بدون حالت تعاملی، بهترین کیفیت خودکار)")
    p.add_argument("--batch", action="store_true",
                    help="پردازش پوشه‌ی Input (فایل txt چندلینکی / عکس QR / اسکرین‌شات)")
    p.add_argument("-o", "--output", metavar="DIR",
                    help="مسیر دلخواه برای پوشه‌ی دانلود (پیش‌فرض: تشخیص خودکار بر اساس پلتفرم)")
    return p


def _process_urls_noninteractive(urls):
    """پردازش یک یا چند لینک بدون هیچ input()ی — مناسب Apple Shortcuts / اسکریپت‌های خودکار."""
    had_error = False
    for i, raw in enumerate(urls, 1):
        if len(urls) > 1:
            print(f"\n{'═' * 44}\n[{i}/{len(urls)}] {raw}")
        url, err = validate_url(raw)
        if err:
            print(f"⚠️  رد شد: {raw!r} → {err}")
            had_error = True
            continue
        try:
            ok = process_single_link(url, interactive_mode=False)
            if not ok:
                had_error = True
        except Exception as e:
            print(f"❌ خطای غیرمنتظره برای {url}: {e}")
            had_error = True
    return not had_error


def main():
    parser = build_arg_parser()
    args = parser.parse_args()

    if args.output:
        configure_directories(root_override=args.output)

    if args.batch:
        batch_process()
        return

    if args.urls:
        ok = _process_urls_noninteractive(args.urls)
        sys.exit(0 if ok else 1)
        return

    # بدون هیچ آرگومانی: اگر ورودی از پایپ/اسکریپت خارجی (نه ترمینال تعاملی) بیاید،
    # هر خط را به‌عنوان یک لینک در نظر می‌گیریم (مفید برای Apple Shortcuts/اتوماسیون).
    # در ترمینال معمولی (isatty=True) این بخش هرگز اجرا نمی‌شود و رفتار قبلی حفظ می‌شود.
    if not sys.stdin.isatty():
        piped = [line.strip() for line in sys.stdin if line.strip()]
        if piped:
            ok = _process_urls_noninteractive(piped)
            sys.exit(0 if ok else 1)
            return

    interactive()


if __name__ == "__main__":
    main()
