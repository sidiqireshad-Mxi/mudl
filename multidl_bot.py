#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
multidl_bot.py  –  ربات تلگرام دانلودر چندسایته   v2.0
Python  : 3.9+
Requires: pip install python-telegram-bot yt-dlp requests
"""

import sys, os

def _check_deps():
    try:
        import telegram
        ver = tuple(int(x) for x in telegram.__version__.split(".")[:2])
        if ver < (20, 0):
            print("❌ نسخه python-telegram-bot باید 20.0+ باشد.\n   pip install -U python-telegram-bot")
            sys.exit(1)
    except ImportError:
        print("❌ کتابخانه python-telegram-bot نصب نیست.\n   pip install python-telegram-bot")
        sys.exit(1)

_check_deps()

# ╔══════════════════════════════════════════════════════════╗
# ║           ⚙️  BOT USER CONFIGURATION  ⚙️                 ║
# ╠══════════════════════════════════════════════════════════╣
TELEGRAM_BOT_TOKEN = "8963294537:AAHJch9MOwO7HBe3zM7v1SfgfufDo3nVPtk"
BOT_MAX_FILE_MB     = 1900          # سقف تلگرام برای ربات‌ها ~2000MB
BOT_MAX_WORKERS     = 1000             # چند دانلود همزمان
PENDING_TTL_SECONDS = 15 * 60       # مدت اعتبار دکمه‌های کیفیت قبل از منقضی‌شدن
# ╚══════════════════════════════════════════════════════════╝

import time
import uuid
import logging
import asyncio
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (Application, CommandHandler, MessageHandler,
                           CallbackQueryHandler, ContextTypes, filters)

_BOT_DIR = Path(__file__).parent
if not (_BOT_DIR / "multidl_core.py").exists():
    print(f"❌ multidl_core.py پیدا نشد در {_BOT_DIR}")
    sys.exit(1)
sys.path.insert(0, str(_BOT_DIR))
import multidl_core as core

if TELEGRAM_BOT_TOKEN == "PUT_YOUR_TOKEN_HERE":
    print("❌ توکن ربات را در بالای همین فایل (TELEGRAM_BOT_TOKEN) تنظیم کنید.")
    sys.exit(1)

DL_DIR  = _BOT_DIR / "bot_downloads"
LOG_DIR = _BOT_DIR / "bot_logs"
DL_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_DIR / "bot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("multidl_bot")

_executor = ThreadPoolExecutor(max_workers=BOT_MAX_WORKERS)

# req_id -> {"url":..., "info":..., "ts":...}  (منتظر انتخاب کیفیت کاربر)
PENDING = {}


def _cleanup_pending():
    now = time.time()
    for k in list(PENDING.keys()):
        if now - PENDING[k]["ts"] > PENDING_TTL_SECONDS:
            PENDING.pop(k, None)


WELCOME = (
    "👋 سلام!\n\n"
    "📥 <b>ربات دانلودر چندسایته</b>\n"
    "فقط لینک را برایم بفرستید، بقیه‌اش با من!\n"
    "برای یوتیوب/تیک‌تاک/اینستاگرام/ایکس، کیفیت‌های موجود را با دکمه نشان می‌دهم.\n\n"
    "✅ سایت‌های پشتیبانی‌شده:\n"
    "• یوتیوب\n• تیک‌تاک\n• پینترست\n• Sora\n"
    "• اینستاگرام\n• ایکس/توییتر (X)\n"
    "• گوگل‌پلی (لینک صفحه‌ی اپ)\n• گیت‌هاب (فایل/ریلیز/ریپو)\n"
    "• هر لینک دیگر (تلاش با روش‌های عمومی)\n\n"
    "دستورات:\n"
    "/start — این پیام\n"
    "/status — بررسی پیش‌نیازهای سرور\n"
)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME, parse_mode=ParseMode.HTML)


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        core.check_deps()
    await update.message.reply_text(f"<pre>{buf.getvalue()}</pre>", parse_mode=ParseMode.HTML)


def _sync_list_qualities(url):
    return core.list_video_qualities(url)


def _sync_download_auto(url, outdir):
    return core.download(url, outdir, category=None, on_step=None)


def _sync_download_chosen(url, outdir, fmt, format_id, audio_only, title_hint):
    return core.download_chosen_format(url, outdir, fmt=fmt, format_id=format_id,
                                        audio_only=audio_only, title_hint=title_hint)


def _user_dirs(uid):
    base = DL_DIR / str(uid)
    video_dir = base / "Video"
    audio_dir = base / "Audio"
    images_dir = base / "Images"
    video_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)
    images_dir.mkdir(parents=True, exist_ok=True)
    return str(video_dir), str(audio_dir), str(images_dir)


def _build_title_hint(title, vid_id):
    if core.INCLUDE_ID_SUFFIX and vid_id:
        return f"{title} [{vid_id}]"
    return title or "download"


async def _send_result_files(update: Update, result):
    for fpath in result["files"]:
        try:
            size_mb = os.path.getsize(fpath) / (1024 * 1024)
            if size_mb > BOT_MAX_FILE_MB:
                await update.effective_chat.send_message(
                    f"⚠️ فایل {os.path.basename(fpath)} ({size_mb:.0f}MB) بزرگ‌تر از سقف تلگرام است و ارسال نشد.")
                continue
            ext = os.path.splitext(fpath)[1].lower()
            with open(fpath, "rb") as fh:
                if ext in (".mp4", ".mkv", ".mov", ".webm"):
                    await update.effective_chat.send_video(
                        video=fh, filename=os.path.basename(fpath), caption=os.path.basename(fpath),
                        read_timeout=120, write_timeout=120, connect_timeout=60)
                elif ext in (".mp3", ".m4a", ".opus", ".ogg", ".wav"):
                    await update.effective_chat.send_audio(
                        audio=fh, filename=os.path.basename(fpath),
                        read_timeout=120, write_timeout=120, connect_timeout=60)
                elif ext in (".jpg", ".jpeg", ".png", ".webp"):
                    await update.effective_chat.send_photo(photo=fh, filename=os.path.basename(fpath))
                else:
                    await update.effective_chat.send_document(
                        document=fh, filename=os.path.basename(fpath),
                        read_timeout=120, write_timeout=120, connect_timeout=60)
        except Exception as e:
            log.error(f"send fail file={fpath}: {e}")
            await update.effective_chat.send_message(f"⚠️ ارسال {os.path.basename(fpath)} ناموفق بود: {e}")
    for fpath in result["files"]:
        try:
            if os.path.exists(fpath):
                os.remove(fpath)
        except Exception:
            pass


async def handle_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not text.startswith(("http://", "https://")):
        await update.message.reply_text("⚠️ لطفاً یک لینک معتبر (http/https) بفرستید.")
        return

    uid = update.effective_user.id
    cat = core.detect_category(text)
    cat_fa = core.CATEGORY_FA.get(cat, cat)
    loop = asyncio.get_event_loop()

    if cat not in core.QUALITY_MENU_CATEGORIES:
        # دسته‌های بدون منوی کیفیت (پینترست/Sora/گوگل‌پلی/گیت‌هاب/ردیت/گوگل‌درایو/تراباکس/عمومی)
        video_dir, _, _ = _user_dirs(uid)
        msg = await update.message.reply_text(f"🔎 دسته‌بندی: {cat_fa}\n⏳ در حال دانلود...")
        try:
            result = await loop.run_in_executor(_executor, _sync_download_auto, text, video_dir)
        except Exception as e:
            await msg.edit_text(f"❌ خطای غیرمنتظره: {e}")
            return
        if not result["ok"]:
            err_lines = "\n".join(f"• {e}" for e in result["errors"][-6:])
            await msg.edit_text(f"❌ دانلود ناموفق بود ({cat_fa}).\n\nخطاها:\n{err_lines}")
            return
        await msg.edit_text(f"✅ دانلود موفق ({result['used']}). در حال ارسال...")
        await _send_result_files(update, result)
        return

    msg = await update.message.reply_text(f"🔎 دسته‌بندی: {cat_fa}\n⏳ در حال گرفتن کیفیت‌های موجود...")
    try:
        info = await loop.run_in_executor(_executor, _sync_list_qualities, text)
    except Exception as e:
        info = None
        log.warning(f"list_video_qualities failed: {e}")

    _cleanup_pending()
    req_id = uuid.uuid4().hex[:10]
    title = (info or {}).get("title") or text

    # حالت ۱: پست چندعکسی/اسلایدشو (مثل بعضی پست‌های تیک‌تاک)
    if info and info.get("slideshow_images") and not info.get("video_options"):
        PENDING[req_id] = {"kind": "slideshow", "url": text, "info": info, "ts": time.time()}
        n = len(info["slideshow_images"])
        buttons = [
            [InlineKeyboardButton(f"🖼 فقط عکس‌ها ({n} عکس)", callback_data=f"dl:{req_id}:img")],
            [InlineKeyboardButton("🎵 فقط صدا", callback_data=f"dl:{req_id}:aud")],
            [InlineKeyboardButton("🖼+🎵 هر دو", callback_data=f"dl:{req_id}:both")],
        ]
        await msg.edit_text(
            f"🖼 {title}\nاین یک پست چندعکسی است ({n} عکس + موزیک). چی می‌خوای؟",
            reply_markup=InlineKeyboardMarkup(buttons))
        return

    # حالت ۲: کیفیت‌های واقعی ویدیو موجود است
    if info and info.get("video_options"):
        PENDING[req_id] = {"kind": "list", "url": text, "info": info, "ts": time.time()}
        buttons = []
        for i, f in enumerate(info["video_options"]):
            size = f.get("filesize") or f.get("filesize_approx")
            size_txt = f"{size/1024/1024:.1f}MB" if size else "؟"
            merge_tag = " 🔗" if f.get("_needs_merge") else ""
            buttons.append([InlineKeyboardButton(
                f"🎬 {f.get('height', '?')}p{merge_tag} — {size_txt}", callback_data=f"dl:{req_id}:{i}")])
        audio = info.get("audio_option")
        audio_txt = f" (~{int(audio.get('abr') or 0)}kbps)" if audio else ""
        buttons.append([InlineKeyboardButton(f"🎵 فقط صدا{audio_txt}", callback_data=f"dl:{req_id}:aud")])
        hint = f"\n\nℹ️ {info['ffmpeg_hint']}" if info.get("ffmpeg_hint") else ""
        merge_note = "\n🔗 = نیاز به ادغام خودکار صدا+تصویر (کمی کندتر)" if any(
            f.get("_needs_merge") for f in info["video_options"]) else ""
        await msg.edit_text(f"🎬 {title}{merge_note}{hint}\n\nکیفیت مورد نظر را انتخاب کن:",
                             reply_markup=InlineKeyboardMarkup(buttons))
        return

    # حالت ۳: جزئیات کیفیت در دسترس نبود (مثلاً مشکل شبکه)؛ طبق درخواست، بازهم
    # صریحاً می‌پرسیم ویدیو می‌خواهی یا فقط صدا، به‌جای دانلود خودکار بی‌سروصدا.
    PENDING[req_id] = {"kind": "simple", "url": text, "info": info, "cat": cat, "ts": time.time()}
    buttons = [
        [InlineKeyboardButton("🎬 ویدیو (کیفیت خودکار)", callback_data=f"simple:{req_id}:video")],
        [InlineKeyboardButton("🎵 فقط صدا", callback_data=f"simple:{req_id}:audio")],
    ]
    note = "" if info is not None else "\n(نتونستم جزئیات کیفیت رو بگیرم؛ احتمالاً مشکل شبکه‌ست)"
    await msg.edit_text(f"🔎 دسته‌بندی: {cat_fa}{note}\nچی می‌خوای دانلود کنم؟",
                         reply_markup=InlineKeyboardMarkup(buttons))


async def handle_quality_choice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    parts = data.split(":", 2)
    if len(parts) != 3 or parts[0] not in ("dl", "simple"):
        return
    prefix, req_id, sel = parts

    entry = PENDING.get(req_id)
    if not entry:
        await query.edit_message_text("⌛ این درخواست منقضی شده؛ لینک را دوباره بفرست.")
        return

    url = entry["url"]
    info = entry["info"]
    kind = entry["kind"]
    uid = update.effective_user.id
    video_dir, audio_dir, images_dir = _user_dirs(uid)
    title = (info or {}).get("title") or url
    title_hint = _build_title_hint(title, (info or {}).get("id") if info else None)
    loop = asyncio.get_event_loop()

    # ── حالت ساده: بدون جزئیات فرمت، فقط ویدیو یا فقط صدا ──
    if kind == "simple":
        PENDING.pop(req_id, None)
        cat_for_retry = entry.get("cat", "other")
        if sel == "audio":
            await query.edit_message_text("⏳ در حال دانلود صدا...")
            result = await loop.run_in_executor(
                _executor, _sync_download_chosen, url, audio_dir, None, None, True, title_hint)
            label = "صوت"
        else:
            await query.edit_message_text("⏳ در حال دانلود ویدیو...")
            result = await loop.run_in_executor(
                _executor, _sync_download_chosen, url, video_dir, None, None, False, title_hint)
            if not result["ok"]:
                # تلاش تکی yt-dlp شکست خورد؛ قبل از تسلیم‌شدن، زنجیره‌ی کامل fallback
                # مخصوص همین دسته را هم امتحان کن (tikwm/vxtwitter/و غیره)
                await query.edit_message_text("⏳ روش اول جواب نداد؛ در حال امتحان روش‌های دیگر...")
                fallback_result = await loop.run_in_executor(
                    _executor, _sync_download_auto, url, video_dir)
                if fallback_result["ok"]:
                    result = fallback_result
                else:
                    result["errors"] += fallback_result["errors"]
            label = "ویدیو"
        if not result["ok"]:
            err_lines = "\n".join(f"• {e}" for e in result["errors"][-6:])
            await query.edit_message_text(f"❌ دانلود ناموفق بود ({label}).\n\nخطاها:\n{err_lines}")
            return
        await query.edit_message_text(f"✅ دانلود موفق ({label} — {result['used']}). در حال ارسال...")
        await _send_result_files(update, result)
        return

    # ── حالت اسلایدشو: عکس‌ها / صدا / هردو ──
    if kind == "slideshow":
        imgs = info.get("slideshow_images") or []
        audio_fmt = info.get("audio_option")
        ok_any = False
        msgs = []

        if sel in ("img", "both"):
            await query.edit_message_text("⏳ در حال دانلود عکس‌ها...")
            try:
                paths = await loop.run_in_executor(
                    _executor, core.download_images, imgs, images_dir, core.safe_filename(title))
                ok_any = True
                for p in paths:
                    try:
                        with open(p, "rb") as fh:
                            await update.effective_chat.send_photo(photo=fh)
                    except Exception as e:
                        log.error(f"send image fail: {e}")
                    finally:
                        try:
                            os.remove(p)
                        except OSError:
                            pass
                msgs.append(f"🖼 {len(paths)} عکس ارسال شد")
            except Exception as e:
                msgs.append(f"❌ دانلود عکس‌ها ناموفق: {e}")

        if sel in ("aud", "both") and audio_fmt:
            await query.edit_message_text("⏳ در حال دانلود صدا...")
            result = await loop.run_in_executor(
                _executor, _sync_download_chosen, url, audio_dir, audio_fmt, None, True, title_hint)
            if result["ok"]:
                ok_any = True
                await _send_result_files(update, result)
                msgs.append("🎵 صدا ارسال شد")
            else:
                msgs.append(f"❌ دانلود صدا ناموفق: {result['errors'][-1] if result['errors'] else '?'}")

        PENDING.pop(req_id, None)
        await query.edit_message_text("\n".join(msgs) if msgs else ("✅ انجام شد" if ok_any else "❌ ناموفق"))
        return

    # ── حالت لیست کیفیت معمولی (یوتیوب/تیک‌تاک/اینستاگرام/ایکس) ──
    if sel == "aud":
        fmt = info.get("audio_option")
        await query.edit_message_text("⏳ در حال دانلود صدا...")
        result = await loop.run_in_executor(
            _executor, _sync_download_chosen, url, audio_dir, fmt, None, True, title_hint)
        label = "صوت"
    else:
        try:
            idx = int(sel)
            fmt = info["video_options"][idx]
        except (ValueError, IndexError):
            await query.edit_message_text("❌ گزینه نامعتبر.")
            return
        await query.edit_message_text(f"⏳ در حال دانلود {fmt.get('height', '?')}p...")
        result = await loop.run_in_executor(
            _executor, _sync_download_chosen, url, video_dir, fmt, fmt.get("format_id"), False, title_hint)
        label = f"{fmt.get('height')}p"

    PENDING.pop(req_id, None)

    if not result["ok"]:
        err_lines = "\n".join(f"• {e}" for e in result["errors"][-6:])
        await query.edit_message_text(f"❌ دانلود ناموفق بود ({label}).\n\nخطاها:\n{err_lines}")
        return

    await query.edit_message_text(f"✅ دانلود موفق ({label} — {result['used']}). در حال ارسال...")
    await _send_result_files(update, result)


async def error_handler(update, ctx):
    log.error(f"Exception: {ctx.error}", exc_info=ctx.error)


def main():
    log.info(f"multidl_bot v2.0 starting — workers={BOT_MAX_WORKERS}")
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CallbackQueryHandler(handle_quality_choice, pattern=r"^(dl|simple):"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.add_error_handler(error_handler)

    print()
    print("━" * 48)
    print(f"  🤖 multidl_bot v2.0 — workers={BOT_MAX_WORKERS}")
    print(f"  📁 {DL_DIR}")
    print(f"  📋 {LOG_DIR / 'bot.log'}")
    print("  Ctrl+C برای توقف")
    print("━" * 48)
    print()

    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
