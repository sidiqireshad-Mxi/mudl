#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
multidl_vision.py  –  استخراج لینک از QR کد و اسکرین‌شات، با چند روش fallback

زنجیره‌ی QR:
    ۱. pyzbar + Pillow           (آفلاین، نیاز به لیبری سیستمی libzbar)
    ۲. OpenCV QRCodeDetector     (آفلاین، فقط pip، به libzbar نیاز ندارد)
    ۳. api.qrserver.com          (آنلاین، وقتی هر دو روش آفلاین نبودند/شکست خوردند)

زنجیره‌ی OCR (برای اسکرین‌شاتی که لینک به‌صورت متن داخلش هست، نه QR):
    ۱. pytesseract (+ باینری tesseract)   (آفلاین)
    ۲. easyocr                            (آفلاین، فقط pip، ولی حجیم/کند)
    ۳. api.ocr.space                      (آنلاین، رایگان با محدودیت نرخ)

نصب پیشنهادی (به‌ترتیب سبک‌ترین به سنگین‌ترین):
    pip install pillow opencv-python-headless requests   # QR آفلاین بدون نیاز به zbar
    pip install pyzbar                                    # نیاز به libzbar سیستمی؛ اگر نصب نشد مهم نیست
    pip install pytesseract        # + Termux: pkg install tesseract
    pip install easyocr            # حجیم (چند صد مگابایت)، فقط اگر واقعاً لازم شد
"""

import os
import re
import shutil

try:
    import requests
except ImportError:
    requests = None

URL_RE = re.compile(r"https?://[^\s\"'<>]+")
UA = {"User-Agent": "Mozilla/5.0 (compatible; multidl-vision/1.0)"}
TIMEOUT = 25


class VisionError(Exception):
    pass


def has_module(name):
    try:
        __import__(name)
        return True
    except ImportError:
        return False


def _has_tesseract_binary():
    return shutil.which("tesseract") is not None


# ───────────────────────── زنجیره‌ی QR ─────────────────────────

def qr_pyzbar(path):
    from pyzbar.pyzbar import decode
    from PIL import Image
    img = Image.open(path)
    results = decode(img)
    if not results:
        raise VisionError("چیزی پیدا نشد")
    return [r.data.decode("utf-8", errors="ignore") for r in results]


def qr_opencv(path):
    import cv2
    img = cv2.imread(path)
    if img is None:
        raise VisionError("نتوانست تصویر را باز کند")
    detector = cv2.QRCodeDetector()
    data, points, _ = detector.detectAndDecode(img)
    if data:
        return [data]
    if hasattr(detector, "detectAndDecodeMulti"):
        try:
            ok, decoded_info, points, _ = detector.detectAndDecodeMulti(img)
            found = [d for d in (decoded_info or []) if d]
            if found:
                return found
        except Exception:
            pass
    raise VisionError("QR در تصویر پیدا نشد")


def qr_online_qrserver(path):
    if requests is None:
        raise VisionError("requests نصب نیست")
    with open(path, "rb") as f:
        r = requests.post(
            "https://api.qrserver.com/v1/read-qr-code/",
            files={"file": ("qr.png", f, "image/png")},
            headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    out = []
    for item in data:
        for sym in item.get("symbol", []):
            if sym.get("data"):
                out.append(sym["data"])
    if not out:
        raise VisionError("سرور چیزی decode نکرد")
    return out


QR_METHODS = [
    ("pyzbar (آفلاین)", qr_pyzbar),
    ("opencv (آفلاین)", qr_opencv),
    ("qrserver.com (آنلاین)", qr_online_qrserver),
]


def decode_qr(path):
    """زنجیره‌ی fallback برای QR. خروجی: (لیست‌رشته‌های decode‌شده, نام‌روش موفق)"""
    for name, fn in QR_METHODS:
        try:
            result = fn(path)
            if result:
                return result, name
        except VisionError:
            continue
        except Exception:
            continue
    return [], None


# ───────────────────────── زنجیره‌ی OCR ─────────────────────────

def ocr_pytesseract(path):
    if not _has_tesseract_binary():
        raise VisionError("باینری tesseract نصب نیست")
    import pytesseract
    from PIL import Image
    text = pytesseract.image_to_string(Image.open(path))
    if not text.strip():
        raise VisionError("متنی پیدا نشد")
    return text


def ocr_easyocr(path):
    import easyocr
    reader = easyocr.Reader(["en"], gpu=False, verbose=False)
    results = reader.readtext(path, detail=0)
    text = "\n".join(results)
    if not text.strip():
        raise VisionError("متنی پیدا نشد")
    return text


def ocr_online_ocrspace(path):
    if requests is None:
        raise VisionError("requests نصب نیست")
    with open(path, "rb") as f:
        r = requests.post(
            "https://api.ocr.space/parse/image",
            files={"file": f},
            data={"apikey": "helloworld", "language": "eng", "isOverlayRequired": False},
            timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if data.get("IsErroredOnProcessing"):
        raise VisionError(str(data.get("ErrorMessage")))
    parsed = data.get("ParsedResults") or []
    text = "\n".join(p.get("ParsedText", "") for p in parsed)
    if not text.strip():
        raise VisionError("متنی پیدا نشد")
    return text


OCR_METHODS = [
    ("pytesseract (آفلاین)", ocr_pytesseract),
    ("easyocr (آفلاین)", ocr_easyocr),
    ("ocr.space (آنلاین، محدود)", ocr_online_ocrspace),
]


def ocr_image(path):
    """زنجیره‌ی fallback برای OCR. خروجی: (متن‌استخراج‌شده یا None, نام‌روش موفق)"""
    for name, fn in OCR_METHODS:
        try:
            text = fn(path)
            if text and text.strip():
                return text, name
        except VisionError:
            continue
        except Exception:
            continue
    return None, None


# ───────────────────────── تابع اصلی ─────────────────────────

def extract_links_from_image(path):
    """
    ابتدا QR (۳ روش)، اگر لینکی نداد سراغ OCR (۳ روش) می‌رود.
    خروجی: (links: list[str], method: str|None)
    """
    qr_texts, qr_method = decode_qr(path)
    links = []
    for text in qr_texts:
        found = URL_RE.findall(text)
        if found:
            links += found
        elif text.strip().startswith(("http://", "https://")):
            links.append(text.strip())
    if links:
        return links, f"QR ({qr_method})"

    text, ocr_method = ocr_image(path)
    if text:
        links = URL_RE.findall(text)
        if links:
            return links, f"OCR ({ocr_method})"

    return [], None


def check_vision_deps():
    rows = [
        ("Pillow", has_module("PIL")),
        ("pyzbar", has_module("pyzbar")),
        ("opencv (cv2)", has_module("cv2")),
        ("pytesseract (پایتون)", has_module("pytesseract")),
        ("tesseract (باینری سیستم)", _has_tesseract_binary()),
        ("easyocr", has_module("easyocr")),
        ("requests (برای روش‌های آنلاین)", has_module("requests")),
    ]
    print("📦 وضعیت پیش‌نیازهای QR/OCR:")
    for name, ok in rows:
        print(f"  {'✅' if ok else '⚠️ '} {name}")


if __name__ == "__main__":
    check_vision_deps()
