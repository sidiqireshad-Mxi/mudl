#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
multidl_bot.py  –  ربات تلگرام دانلودر چندسایته   v3.0
Python  : 3.9+
Requires: pip install python-telegram-bot yt-dlp requests

قابل اجرا هم روی گوشی (Termux/Pydroid/a-Shell، با مقادیر هاردکد پایین همین فایل)
و هم روی سرویس‌های هاست مثل Railway (با متغیرهای محیطی — هیچ‌کدام از این دو روش
یکدیگر را خراب نمی‌کنند؛ اول متغیر محیطی چک می‌شود، اگر نبود از مقدار هاردکد
همین فایل استفاده می‌شود).
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
# هر مقدار اول از متغیر محیطی خوانده می‌شود (برای هاست‌هایی مثل Railway که
# توکن را در بخش Variables می‌گذاری)؛ اگر آن متغیر تنظیم نشده بود، از همین
# مقدار هاردکد پایین استفاده می‌شود (برای اجرای مستقیم روی گوشی).

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or "PUT_YOUR_TOKEN_HERE"

# سقف واقعی حجم فایل برای آپلود توسط ربات‌ها از طریق Bot API معمولیِ تلگرام
# (api.telegram.org) در واقع ۵۰ مگابایته، نه ۲۰۰۰ مگابایت — عدد ۲۰۰۰ فقط وقتی
# صادق است که خودت یک Local Bot API Server راه‌اندازی کرده باشی (پیشرفته و
# نیازمند سرور اختصاصی). برای اکثر کاربران همون ۵۰ درسته.
BOT_MAX_FILE_MB = int(os.environ.get("BOT_MAX_FILE_MB", "50"))

# چند دانلود واقعی هم‌زمان انجام شود. رو گوشی (Termux/Pydroid) عدد کم بذار (2-3)؛
# رو یه سرور واقعی (Railway و مشابه) می‌تونی بیشترش کنی (10-20). این فقط تعداد
# دانلودهای *هم‌زمان* رو محدود می‌کنه، نه تعداد کاربرانی که می‌تونن هم‌زمان با بات
# صحبت کنن (که خیلی بیشتر از اینه، چون اکثر تعامل‌ها فقط منوی دکمه‌ست نه دانلود).
BOT_MAX_WORKERS = int(os.environ.get("BOT_MAX_WORKERS", "4"))

PENDING_TTL_SECONDS = 15 * 60

# ── عضویت اجباری در کانال/گروه (اختیاری) ──────────────────────────────────
# اگر خالی بمونه، این قابلیت غیرفعاله. برای فعال‌کردن، یوزرنیم کانال/گروه‌ها
# رو با @ وارد کن (کانال/گروه باید public باشه و بات باید توش admin باشه تا
# بتونه عضویت رو چک کنه). می‌تونی چندتا بذاری، با کاما جدا کن.
# مثال هاردکد:   REQUIRED_CHANNELS_LOCAL = ["@my_channel", "@my_group"]
REQUIRED_CHANNELS_LOCAL = []
REQUIRED_CHANNELS = [c.strip() for c in os.environ.get("REQUIRED_CHANNELS", "").split(",") if c.strip()] \
    or REQUIRED_CHANNELS_LOCAL

# ── کانال/گروه لاگ ادمین (اختیاری) ────────────────────────────────────────
# اگر ست بشه، هر کاربر جدید و هر درخواست دانلود یک خط کوتاه اونجا لاگ میشه.
# باید بات رو تو اون کانال/گروه (خصوصی هم می‌تونه باشه) ادمین کنی.
# فرمت: یا @username کانال، یا آیدی عددی (مثل -1001234567890).
ADMIN_LOG_CHAT_ID_LOCAL = ""
ADMIN_LOG_CHAT_ID = os.environ.get("ADMIN_LOG_CHAT_ID") or ADMIN_LOG_CHAT_ID_LOCAL or None

# آیدی عددی تلگرام خودت (برای دستور /stats). با کاما چندتا آیدی می‌تونی بدی.
ADMIN_USER_IDS_LOCAL = []
ADMIN_USER_IDS = set()
for _x in (os.environ.get("ADMIN_USER_IDS", "").split(",") or []):
    _x = _x.strip()
    if _x.isdigit():
        ADMIN_USER_IDS.add(int(_x))
ADMIN_USER_IDS |= set(ADMIN_USER_IDS_LOCAL)
# ╚══════════════════════════════════════════════════════════╝

import re
import json
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
from telegram.error import TelegramError

_BOT_DIR = Path(__file__).parent
if not (_BOT_DIR / "multidl_core.py").exists():
    print(f"❌ multidl_core.py پیدا نشد در {_BOT_DIR}")
    sys.exit(1)
sys.path.insert(0, str(_BOT_DIR))
import multidl_core as core

if TELEGRAM_BOT_TOKEN == "PUT_YOUR_TOKEN_HERE":
    print("❌ توکن ربات تنظیم نشده.\n"
          "   یا متغیر محیطی TELEGRAM_BOT_TOKEN رو ست کن (مثلاً تو Railway → Variables)،\n"
          "   یا بالای همین فایل، TELEGRAM_BOT_TOKEN رو با توکن واقعی پر کن.")
    sys.exit(1)

DL_DIR  = _BOT_DIR / "bot_downloads"
LOG_DIR = _BOT_DIR / "bot_logs"
DL_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)
USERS_FILE = _BOT_DIR / "users.json"
# نکته: روی هاست‌های ephemeral (مثل Railway در پلن رایگان)، این فایل با هر
# redeploy پاک می‌شود. برای آماری که واقعاً ماندگار باشد، کانال لاگ ادمین
# (ADMIN_LOG_CHAT_ID) منبع قابل‌اتکاتری است.

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

PENDING = {}


def _cleanup_pending():
    now = time.time()
    for k in list(PENDING.keys()):
        if now - PENDING[k]["ts"] > PENDING_TTL_SECONDS:
            PENDING.pop(k, None)


# ───────────────────────── کاربران و لاگ ادمین ─────────────────────────

def _load_users():
    try:
        with open(USERS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_users(users):
    try:
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
    except OSError as e:
        log.warning(f"نتونستم users.json رو ذخیره کنم: {e}")


def _register_user(user):
    """اگر کاربر جدید بود ثبتش کن و برگردون True؛ اگر قبلاً بوده False."""
    users = _load_users()
    uid = str(user.id)
    is_new = uid not in users
    users[uid] = {
        "username": user.username, "first_name": user.first_name,
        "last_seen": int(time.time()),
    }
    _save_users(users)
    return is_new, len(users)


async def _admin_log(bot, text):
    if not ADMIN_LOG_CHAT_ID:
        return
    try:
        await bot.send_message(chat_id=ADMIN_LOG_CHAT_ID, text=text, parse_mode=ParseMode.HTML)
    except TelegramError as e:
        log.warning(f"ارسال لاگ به ادمین ناموفق بود: {e}")


def _user_tag(user):
    uname = f"@{user.username}" if user.username else user.first_name or str(user.id)
    return f"{uname} (<code>{user.id}</code>)"


# ───────────────────────── عضویت اجباری ─────────────────────────

async def _check_membership(bot, user_id):
    """
    خروجی: (ok: bool, missing: list[str])
    اگر REQUIRED_CHANNELS خالی باشد، همیشه ok=True (قابلیت غیرفعال است).
    اگر چک‌کردن یک کانال به هر دلیل خطا داد (مثلاً بات آنجا ادمین نیست)،
    fail-open می‌کنیم (یعنی همون یکی رو نادیده می‌گیریم) تا یک تنظیم اشتباه
    کل بات رو برای همه قفل نکنه؛ ولی خطا رو لاگ می‌کنیم تا ادمین بفهمه.
    """
    if not REQUIRED_CHANNELS:
        return True, []
    missing = []
    for channel in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status in ("left", "kicked"):
                missing.append(channel)
        except TelegramError as e:
            log.warning(f"نتونستم عضویت {channel} رو چک کنم (احتمالاً بات اونجا ادمین نیست): {e}")
    return (len(missing) == 0), missing


def _membership_keyboard(missing):
    buttons = []
    for ch in missing:
        uname = ch.lstrip("@")
        buttons.append([InlineKeyboardButton(f"عضویت در {ch}", url=f"https://t.me/{uname}")])
    buttons.append([InlineKeyboardButton("✅ عضو شدم، بررسی مجدد", callback_data="checkmember")])
    return InlineKeyboardMarkup(buttons)


# ───────────────────────── پیام‌ها ─────────────────────────

WELCOME = (
    "👋 سلام!\n\n"
    "📥 <b>ربات دانلودر چندسایته</b>\n"
    "فقط لینک را برایم بفرستید، بقیه‌اش با من!\n"
    "برای یوتیوب/تیک‌تاک/اینستاگرام/ایکس، کیفیت‌های موجود را با دکمه نشان می‌دهم.\n\n"
    "✅ سایت‌های پشتیبانی‌شده:\n"
    "• یوتیوب • تیک‌تاک • پینترست • Sora\n"
    "• اینستاگرام • ایکس/توییتر (X)\n"
    "• ردیت • گوگل‌درایو • تراباکس\n"
    "• گوگل‌پلی (لینک صفحه‌ی اپ) • گیت‌هاب\n"
    "• هر لینک دیگر (تلاش با روش‌های عمومی)\n\n"
    f"⚠️ سقف حجم فایل برای ارسال: {BOT_MAX_FILE_MB}MB (محدودیت خودِ تلگرام)\n\n"
    "دستورات:\n"
    "/start — این پیام\n"
    "/status — بررسی پیش‌نیازهای سرور\n"
)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    is_new, total = _register_user(user)
    if is_new:
        await _admin_log(ctx.bot, f"🆕 کاربر جدید: {_user_tag(user)}\n👥 مجموع: {total}")

    ok, missing = await _check_membership(ctx.bot, user.id)
    if not ok:
        await update.message.reply_text(
            "برای استفاده از بات، اول باید عضو این کانال/گروه(ها) بشی:",
            reply_markup=_membership_keyboard(missing))
        return

    await update.message.reply_text(WELCOME, parse_mode=ParseMode.HTML)


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        core.check_deps()
    await update.message.reply_text(f"<pre>{buf.getvalue()}</pre>", parse_mode=ParseMode.HTML)


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id not in ADMIN_USER_IDS:
        return  # سکوت کامل برای غیرادمین‌ها
    users = _load_users()
    await update.message.reply_text(
        f"👥 تعداد کل کاربران ثبت‌شده: {len(users)}\n"
        f"(این عدد فقط از آخرین بار اجرای بات روی این سرور است؛ اگر سرور ephemeral "
        f"است مثل Railway پلن رایگان، با هر ری‌استارت صفر می‌شود.)")


async def checkmember_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    ok, missing = await _check_membership(ctx.bot, user.id)
    if ok:
        await query.answer("✅ عضویت تایید شد!")
        await query.edit_message_text(WELCOME, parse_mode=ParseMode.HTML)
    else:
        await query.answer("هنوز عضو همه‌ی کانال‌ها نیستی.", show_alert=True)


# ───────────────────────── هلسپرهای دانلود ─────────────────────────

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


async def _send_one_file(chat, fpath, retries=2):
    """ارسال یک فایل با چند بار تلاش مجدد در صورت خطای شبکه‌ی موقتی (مثل httpx.ReadError)."""
    size_mb = os.path.getsize(fpath) / (1024 * 1024)
    if size_mb > BOT_MAX_FILE_MB:
        await chat.send_message(
            f"⚠️ فایل {os.path.basename(fpath)} ({size_mb:.0f}MB) از سقف {BOT_MAX_FILE_MB}MB "
            f"تلگرام برای بات‌ها بزرگ‌تره و ارسال نشد.\n"
            f"(این محدودیت خودِ تلگرامه، نه اسکریپت؛ فقط با کیفیت پایین‌تر یا صدای جدا از ویدیو حل میشه.)")
        return

    ext = os.path.splitext(fpath)[1].lower()
    last_err = None
    for attempt in range(1, retries + 2):
        try:
            with open(fpath, "rb") as fh:
                if ext in (".mp4", ".mkv", ".mov", ".webm"):
                    await chat.send_video(video=fh, filename=os.path.basename(fpath),
                                           caption=os.path.basename(fpath),
                                           read_timeout=180, write_timeout=180, connect_timeout=60)
                elif ext in (".mp3", ".m4a", ".opus", ".ogg", ".wav"):
                    await chat.send_audio(audio=fh, filename=os.path.basename(fpath),
                                           read_timeout=180, write_timeout=180, connect_timeout=60)
                elif ext in (".jpg", ".jpeg", ".png", ".webp"):
                    await chat.send_photo(photo=fh, filename=os.path.basename(fpath),
                                           read_timeout=180, write_timeout=180, connect_timeout=60)
                else:
                    await chat.send_document(document=fh, filename=os.path.basename(fpath),
                                              read_timeout=180, write_timeout=180, connect_timeout=60)
            return
        except Exception as e:
            last_err = e
            log.warning(f"تلاش {attempt} برای ارسال {fpath} شکست خورد: {e}")
            if attempt <= retries:
                await asyncio.sleep(2 * attempt)
    log.error(f"ارسال {fpath} بعد از {retries + 1} تلاش شکست خورد: {last_err}")
    await chat.send_message(f"⚠️ ارسال {os.path.basename(fpath)} ناموفق بود: {last_err}")


async def _send_result_files(update: Update, result):
    for fpath in result["files"]:
        await _send_one_file(update.effective_chat, fpath)
    for fpath in result["files"]:
        try:
            if os.path.exists(fpath):
                os.remove(fpath)
        except Exception:
            pass


# ───────────────────────── دریافت لینک ─────────────────────────

async def handle_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ok, missing = await _check_membership(ctx.bot, user.id)
    if not ok:
        await update.message.reply_text(
            "برای استفاده از بات، اول باید عضو این کانال/گروه(ها) بشی:",
            reply_markup=_membership_keyboard(missing))
        return

    text = (update.message.text or "").strip()
    if not text.startswith(("http://", "https://")):
        await update.message.reply_text("⚠️ لطفاً یک لینک معتبر (http/https) بفرستید.")
        return

    uid = user.id
    cat = core.detect_category(text)
    cat_fa = core.CATEGORY_FA.get(cat, cat)
    loop = asyncio.get_event_loop()

    await _admin_log(ctx.bot, f"📥 {_user_tag(user)} → [{cat_fa}]\n{text}")

    if cat not in core.QUALITY_MENU_CATEGORIES:
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
    if query.data == "checkmember":
        await checkmember_callback(update, ctx)
        return

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

    if kind == "simple":
        PENDING.pop(req_id, None)
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
    log.info(f"multidl_bot v3.0 starting — workers={BOT_MAX_WORKERS}, "
             f"max_file={BOT_MAX_FILE_MB}MB, required_channels={REQUIRED_CHANNELS or 'none'}")
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CallbackQueryHandler(handle_quality_choice, pattern=r"^(dl|simple):|^checkmember$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.add_error_handler(error_handler)

    print()
    print("━" * 48)
    print(f"  🤖 multidl_bot v3.0 — workers={BOT_MAX_WORKERS}")
    print(f"  📏 سقف حجم فایل: {BOT_MAX_FILE_MB}MB")
    print(f"  🔒 عضویت اجباری: {REQUIRED_CHANNELS or 'غیرفعال'}")
    print(f"  📋 لاگ ادمین: {'فعال' if ADMIN_LOG_CHAT_ID else 'غیرفعال'}")
    print(f"  📁 {DL_DIR}")
    print(f"  📋 {LOG_DIR / 'bot.log'}")
    print("  Ctrl+C برای توقف")
    print("━" * 48)
    print()

    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
