#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cookies_json2netscape.py  –  تبدیل خروجی JSON افزونه Cookie-Editor
                              به فرمت Netscape (cookies.txt) برای yt-dlp

استفاده:
    ۱. خروجی Cookie-Editor (Export) رو تو یه فایل به اسم cookies_raw.json
       کنار همین اسکریپت ذخیره کن (از داخل Notes کپی/پیست کن و ذخیره کن).
    ۲. اجرا کن:
       python cookies_json2netscape.py
    ۳. فایل cookies.txt ساخته می‌شود؛ آن را کنار multidl_core.py بگذار.

نکته امنیتی: cookies.txt حاوی نشست لاگین شماست، آن را جایی به اشتراک نگذارید.
"""

import json
import os
import sys
import time

SRC = "cookies_raw.json"
DST = "cookies.txt"

def convert(json_path=SRC, out_path=DST):
    if not os.path.exists(json_path):
        print(f"❌ فایل {json_path} پیدا نشد. اول خروجی Cookie-Editor را با همین اسم ذخیره کن.")
        sys.exit(1)

    with open(json_path, "r", encoding="utf-8") as f:
        cookies = json.load(f)

    lines = [
        "# Netscape HTTP Cookie File",
        "# ساخته‌شده توسط cookies_json2netscape.py — این فایل حاوی نشست لاگین شماست، محرمانه نگه دارید.",
    ]
    now = time.time()
    count = 0
    for c in cookies:
        domain = c.get("domain", "")
        if not domain:
            continue
        # ستون‌های فرمت Netscape:
        # domain  include_subdomains  path  secure  expiry  name  value
        include_sub = "TRUE" if domain.startswith(".") else "FALSE"
        path = c.get("path", "/")
        secure = "TRUE" if c.get("secure") else "FALSE"
        expiry = c.get("expirationDate")
        if expiry is None:
            expiry = int(now + 60 * 60 * 24 * 365)  # کوکی session: یک سال اعتبار فرضی
        else:
            expiry = int(float(expiry))
        name = c.get("name", "")
        value = c.get("value", "")
        if not name:
            continue
        lines.append(f"{domain}\t{include_sub}\t{path}\t{secure}\t{expiry}\t{name}\t{value}")
        count += 1

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"✅ {count} کوکی تبدیل شد → {out_path}")
    print("⚠️ این فایل را جایی آپلود/فوروارد نکن؛ فقط کنار multidl_core.py نگهش دار.")

if __name__ == "__main__":
    convert()
