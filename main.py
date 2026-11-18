"""
🚀 Nexus Extractor - PRO TELEGRAM BOT & WEB GATEWAY (STABLE & TESTED)
"""
import os
import json
import logging
import secrets
import uuid
import random
import urllib.parse
import re
import time
import io
import html
import threading
import urllib3
import hashlib
import base64
from datetime import datetime

# ماژول‌های شبکه و وب
import requests
from bs4 import BeautifulSoup
from aiohttp import web
import redis

# ماژول ربات تلگرام
import telebot
from telebot import types

# غیرفعال کردن هشدارهای SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ================= Configuration =================
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
admin_ids_env = os.environ.get("ADMIN_IDS", "123456789")
ADMIN_IDS = [int(x.strip()) for x in admin_ids_env.split(",") if x.strip().isdigit()]

WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://your-domain.com").rstrip('/')
PORT = int(os.environ.get("PORT", "8080"))
REDIS_URL = os.environ.get(
    "REDIS_URL",
    "redis://default:DWYgsUKwUJTjTMQpasIEHBYVPEMMhoPt@altaria.proxy.rlwy.net:22748"
)

APP_SECRET_HEADER = "JetApp-Secure-Client"
JET_API_BASE = "https://api.digikalajet.ir"
JET_APP_ID = "08f293bd-0e29-4794-897c-a111b19da003"
CLEAN_ACCOUNTS_FILE = "clean_accounts.txt"

# اتصال امن با تایم‌اوت به دیتابیس ردیس
try:
    redis_client = redis.Redis.from_url(
        REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5
    )
except Exception as e:
    logging.error(f"Redis Init Error: {e}")
    redis_client = None

bot = telebot.TeleBot(TOKEN)
logging.basicConfig(format='%(asctime)s - %(message)s', level=logging.INFO)

# حافظه موقت ربات
user_data = {}
user_proxies = {}

MENU_BUTTONS = {
    "📦 دریافت حساب‌های بدون سفارش",
    "📊 آمار لینک‌های ساخته شده",
    "🌐 تنظیم پروکسی",
    "⚙️ پنل مدیریت",
}


# ================= Helper Functions =================
def generate_pkce():
    verifier = secrets.token_urlsafe(64)
    hashed = hashlib.sha256(verifier.encode('ascii')).digest()
    challenge = base64.urlsafe_b64encode(hashed).decode('ascii').rstrip('=')
    return verifier, challenge


def normalize_digits(text):
    """تبدیل اعداد فارسی و عربی به انگلیسی"""
    p2e = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')
    return text.translate(p2e).strip()


def format_proxy(p_str):
    p_str = p_str.strip()
    if not p_str or p_str.startswith('#'):
        return None
    if not re.match(r'^(http|https|socks4|socks5)://', p_str):
        p_str = f"http://{p_str}"
    return p_str


def extract_hidden_inputs(form):
    """استخراج فیلدهای مخفی فرم برای جلوگیری از رد شدن درخواست توسط Keycloak"""
    payload = {}
    if not form:
        return payload
    for inp in form.find_all('input', {'type': 'hidden'}):
        name = inp.get('name')
        if name:
            payload[name] = inp.get('value', '')
    return payload


def extract_all_form_inputs(form):
    """استخراج تمام فیلدهای یک فرم (برای auto-submit-form)"""
    payload = {}
    if not form:
        return payload
    for inp in form.find_all('input'):
        name = inp.get('name')
        if name:
            payload[name] = inp.get('value', '')
    return payload


def extract_auth_code_from_url(url_str):
    """استخراج دقیق پارامتر code از query یا fragment بدون اشتباه گرفتن با session_code"""
    if not url_str:
        return None
    parsed = urllib.parse.urlparse(url_str)
    for part in (parsed.fragment, parsed.query):
        if part:
            qs = urllib.parse.parse_qs(part)
            if 'code' in qs and qs['code']:
                return qs['code'][0]
    match = re.search(r'(?:^|[?&#])code=([^&#"\'\s<>]+)', url_str)
    return urllib.parse.unquote(match.group(1)) if match else None


def extract_html_error(html_text):
    """استخراج پیام خطای صفحه Keycloak در صورت عدم ورود موفق"""
    try:
        soup = BeautifulSoup(html_text, 'html.parser')
        for selector in (
            '.kc-feedback-text',
            '.alert-error',
            '[id*="error"]',
            '[class*="error"]',
            '.input-error',
        ):
            el = soup.select_one(selector)
            if el and el.get_text(strip=True):
                return el.get_text(" ", strip=True)[:250]
        title = soup.title.get_text(strip=True) if soup.title else ""
        return title[:150] if title else ""
    except Exception:
        return ""


def get_main_keyboard(is_admin=False):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    btn_clean = types.KeyboardButton("📦 دریافت حساب‌های بدون سفارش")
    btn_stats = types.KeyboardButton("📊 آمار لینک‌های ساخته شده")
    btn_proxy = types.KeyboardButton("🌐 تنظیم پروکسی")
    if is_admin:
        btn_admin = types.KeyboardButton("⚙️ پنل مدیریت")
        markup.add(btn_clean, btn_stats)
        markup.add(btn_proxy, btn_admin)
    else:
        markup.add(btn_clean, btn_stats, btn_proxy)
    return markup


def get_admin_inline_markup():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("📊 بروزرسانی آمار لینک‌ها", callback_data="adm_stats"),
        types.InlineKeyboardButton("🔗 دریافت فایل لینک‌های ساخته شده", callback_data="adm_export_links"),
        types.InlineKeyboardButton("📦 دریافت فایل حساب‌های بدون سفارش", callback_data="adm_export_clean"),
        types.InlineKeyboardButton("🌐 تنظیم پروکسی", callback_data="adm_set_proxy"),
    )
    return markup


def get_link_stats(chat_id=None):
    """محاسبه آمار دقیق لینک‌ها و اکانت‌های ذخیره‌شده"""
    stats = {
        "redis_ok": False,
        "bulk_links": 0,
        "active_sessions": 0,
        "total_created_counter": 0,
        "skipped_ordered_counter": 0,
        "clean_file_count": 0,
        "proxy_count": len(user_proxies.get(chat_id, [])) if chat_id else 0,
    }

    if os.path.exists(CLEAN_ACCOUNTS_FILE):
        try:
            with open(CLEAN_ACCOUNTS_FILE, "r", encoding="utf-8") as f:
                stats["clean_file_count"] = sum(1 for line in f if line.strip())
        except Exception:
            pass

    if redis_client:
        try:
            redis_client.ping()
            stats["redis_ok"] = True
            stats["bulk_links"] = redis_client.hlen("jet:bulk_accounts") or 0
            stats["active_sessions"] = sum(1 for _ in redis_client.scan_iter("jet_session:*", count=500))
            created_raw = redis_client.get("jet:stats:created_links")
            skipped_raw = redis_client.get("jet:stats:skipped_ordered")
            stats["total_created_counter"] = int(created_raw) if created_raw and str(created_raw).isdigit() else stats["bulk_links"]
            stats["skipped_ordered_counter"] = int(skipped_raw) if skipped_raw and str(skipped_raw).isdigit() else 0
        except Exception as e:
            logging.error(f"Redis Stats Error: {e}")

    return stats


def format_stats_message(chat_id=None):
    s = get_link_stats(chat_id)
    redis_status = "🟢 متصل" if s["redis_ok"] else "🔴 قطع"
    return (
        "📊 <b>آمار لینک‌ها و وضعیت سیستم Nexus</b>\n\n"
        f"• وضعیت دیتابیس Redis: {redis_status}\n"
        f"• لینک‌های سالم ذخیره‌شده: <b>{s['bulk_links']}</b>\n"
        f"• سشن‌های فعال فعلی: <b>{s['active_sessions']}</b>\n"
        f"• کل لینک‌های ساخته‌شده (ثبت‌شده): <b>{s['total_created_counter']}</b>\n"
        f"• اکانت‌های خریددارِ رد شده (ذخیره‌نشده): <b>{s['skipped_ordered_counter']}</b>\n"
        f"• تعداد رکورد فایل متنی بدون سفارش: <b>{s['clean_file_count']}</b>\n"
        f"• پروکسی‌های فعال شما: <b>{s['proxy_count']}</b>\n\n"
        f"🕒 بروزرسانی: <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>"
    )


def send_final_file(chat_id, status_message, logs, extracted_data=None, is_admin=False):
    try:
        bot.send_message(chat_id, status_message, reply_markup=get_main_keyboard(is_admin))
        report_text = "\n".join(logs)
        if extracted_data:
            report_text += "\n\n====== [ اطلاعات خروجی ] ======\n"
            report_text += json.dumps(extracted_data, ensure_ascii=False, indent=4)

        file_obj = io.BytesIO(report_text.encode('utf-8'))
        file_obj.name = f"Nexus_Report_{int(time.time())}.txt"
        bot.send_document(chat_id, file_obj, caption="📄 فایل لاگ و دیتای نشست")
    except Exception as e:
        bot.send_message(chat_id, f"❌ خطا در ارسال فایل لاگ: {str(e)}")


def build_jet_headers(jet_token=None):
    """ساخت هدرهای استاندارد دیجی‌کالاجت مطابق jet_cleaner.py"""
    cid = 'FINGERPRINTV2-' + uuid.uuid4().hex
    hdrs = {
        'Accept': 'application/json, text/plain, */*',
        'X-Request-UUID': str(uuid.uuid4()),
        'ClientId': cid,
        'clientid-v2': cid,
        'ClientOs': 'Android',
        'Client': 'mobile',
        'platform-sso-disable-prod': '1',
        'session': f"{uuid.uuid4()}-V3*{int(time.time())}",
        'app-id': JET_APP_ID,
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36',
        'Origin': 'https://www.digikalajet.com',
        'Referer': 'https://www.digikalajet.com/',
    }
    if jet_token:
        hdrs['Authorization'] = jet_token
    return hdrs


def count_orders_payload(payload):
    """تعداد کل سفارش‌ها با هر وضعیتی (تحویل‌شده، در جریان یا لغوشده) مطابق jet_cleaner.py"""
    if not isinstance(payload, dict):
        return 0
    data_obj = payload.get('data') or {}
    total = (data_obj.get('pager') or {}).get('total_items', 0) or 0
    orders = data_obj.get('orders') or {}
    if isinstance(orders, dict):
        total = max(total, sum(len(b) for b in orders.values() if isinstance(b, list)))
    return total


def extract_sa_token_from_url(url_str):
    if not url_str:
        return None
    parsed = urllib.parse.urlparse(url_str)
    qs = urllib.parse.parse_qs(parsed.query)
    if 'sa_token' in qs and qs['sa_token']:
        return qs['sa_token'][0]
    match = re.search(r'(?:^|[?&#])sa_token=([^&#"\'\s<>]+)', url_str)
    return urllib.parse.unquote(match.group(1)) if match else None


def check_purchase_history(session, dk_token, logs):
    """
    استخراج تاریخچه خریدها از طریق API دیجی‌کالاجت.
    خروجی: تاپل (total_orders, jet_token, jet_refresh).
    اگر بررسی با خطا مواجه شود، total_orders برابر None برمی‌گردد تا اکانت اشتباهاً خام ذخیره نشود.
    """
    try:
        logs.append("▶️ در حال بررسی سوابق خرید (ارتباط با دیجی‌کالاجت)...")
        sa_headers = {
            "x-web-client": "desktop",
            "x-web-client-id": "web",
            "x-web-optimize-response": "1",
            "Referer": "https://www.digikala.com/",
            "Authorization": f"Bearer {dk_token}" if not dk_token.startswith("Bearer") else dk_token,
            "User-Agent": session.headers.get("User-Agent", "Mozilla/5.0"),
        }

        res_sa = session.get(
            "https://api.digikala.com/super-app/v1/sso/jet/",
            params={"redirect_url": "/?utm_source=digikala-superweb"},
            headers=sa_headers,
            allow_redirects=False,
            timeout=12,
        )
        sa_token = extract_sa_token_from_url(res_sa.headers.get('Location'))

        if not sa_token:
            res_sa_redirect = session.get(
                "https://api.digikala.com/super-app/v1/sso/jet/",
                params={"redirect_url": "/?utm_source=digikala-superweb"},
                headers=sa_headers,
                allow_redirects=True,
                timeout=12,
            )
            sa_token = extract_sa_token_from_url(res_sa_redirect.url)

        if not sa_token:
            logs.append(f"❌ خطا در دریافت SA_TOKEN | Status: {res_sa.status_code}")
            return None, None, None

        # لاگین در API جت با هدرهای صحیح jet_cleaner.py
        jet_headers = build_jet_headers()
        res_jet = None
        for attempt in range(3):
            try:
                res_jet = session.post(
                    f"{JET_API_BASE}/super-app-sso/",
                    headers=jet_headers,
                    json={"dsa_token": sa_token},
                    timeout=15,
                )
                if res_jet.status_code == 200:
                    break
            except requests.RequestException:
                if attempt == 2:
                    raise

        if not res_jet or res_jet.status_code != 200:
            code = res_jet.status_code if res_jet else "NoResponse"
            logs.append(f"❌ خطا در لاگین جت | Status: {code}")
            return None, None, None

        jet_json = res_jet.json()
        jet_data = jet_json.get('data') or {}
        jet_token = jet_data.get('token')
        jet_refresh = jet_data.get('refresh_token') or jet_data.get('refreshToken')

        if not jet_token:
            logs.append("❌ توکن نهایی جت یافت نشد.")
            return None, None, None

        # دریافت لیست سفارش‌ها با منطق jet_cleaner.py (شامل تلاش مجدد و refresh در 401/402)
        def fetch_orders(tok):
            last_code, last_payload = None, {}
            for _ in range(3):
                try:
                    hdrs = build_jet_headers(tok)
                    r_ord = session.get(f"{JET_API_BASE}/order-shipments/?ch=jj", headers=hdrs, timeout=20)
                    last_code = r_ord.status_code
                    try:
                        last_payload = r_ord.json()
                    except Exception:
                        last_payload = {}
                    if last_code == 200:
                        return last_code, last_payload
                except requests.RequestException:
                    continue
            return last_code, last_payload

        code, payload = fetch_orders(jet_token)
        api_status = payload.get('status') if isinstance(payload, dict) else None

        if (code in (401, 402) or api_status in (401, 402)) and jet_refresh:
            logs.append("⚠️ توکن جت نیاز به بروزرسانی داشت، در حال refresh...")
            try:
                r_ref = session.post(
                    f"{JET_API_BASE}/user/refresh-token/?ch=jj",
                    headers={'User-Agent': 'Mozilla/5.0', 'Content-Type': 'application/json'},
                    json={"refresh_token": jet_refresh, "token": jet_token},
                    timeout=20,
                )
                if r_ref.status_code == 200:
                    ref_json = r_ref.json()
                    pay = ref_json.get('data') if isinstance(ref_json, dict) else None
                    if not isinstance(pay, dict):
                        pay = ref_json if isinstance(ref_json, dict) else {}
                    new_tok = pay.get('token')
                    new_ref = pay.get('refresh_token') or pay.get('refreshToken') or jet_refresh
                    if new_tok:
                        jet_token = new_tok
                        jet_refresh = new_ref
                        code, payload = fetch_orders(jet_token)
                        api_status = payload.get('status') if isinstance(payload, dict) else None
            except Exception as ref_err:
                logs.append(f"⚠️ خطا در refresh توکن جت: {ref_err}")

        if code == 200 and api_status == 200:
            total_orders = count_orders_payload(payload)
            logs.append(f"✅ سوابق خرید استخراج شد: {total_orders}")
            return total_orders, jet_token, jet_refresh

        logs.append(f"❌ پاسخ نامعتبر از سرویس سفارش‌ها | HTTP: {code} | API: {api_status}")
        return None, jet_token, jet_refresh
    except Exception as e:
        logs.append(f"❌ خطای چک کردن خرید: {str(e)}")
        return None, None, None


# ================= Telegram Handlers =================
@bot.message_handler(commands=['start', 'help', 'admin'])
def send_welcome(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    chat_id = message.chat.id
    stats_text = format_stats_message(chat_id)
    bot.send_message(
        chat_id,
        f"⚙️ <b>پنل مدیریت مرکزی Nexus</b>\n\n{stats_text}",
        reply_markup=get_admin_inline_markup(),
        parse_mode="HTML",
    )
    bot.send_message(
        chat_id,
        "📱 برای شروع استخراج، شماره موبایل (مثلاً 09123456789) را ارسال کنید یا از دکمه‌های زیر استفاده کنید:",
        reply_markup=get_main_keyboard(is_admin=True),
    )


@bot.message_handler(func=lambda m: m.text == "⚙️ پنل مدیریت")
def open_admin_panel(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    chat_id = message.chat.id
    bot.send_message(
        chat_id,
        f"⚙️ <b>پنل مدیریت مرکزی Nexus</b>\n\n{format_stats_message(chat_id)}",
        reply_markup=get_admin_inline_markup(),
        parse_mode="HTML",
    )


@bot.message_handler(func=lambda m: m.text == "📊 آمار لینک‌های ساخته شده")
def show_stats_handler(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    bot.send_message(
        message.chat.id,
        format_stats_message(message.chat.id),
        reply_markup=get_main_keyboard(is_admin=True),
        parse_mode="HTML",
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("adm_"))
def admin_callback(call):
    if call.from_user.id not in ADMIN_IDS:
        return
    chat_id = call.message.chat.id
    bot.answer_callback_query(call.id)

    if call.data == "adm_stats":
        try:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=call.message.message_id,
                text=f"⚙️ <b>پنل مدیریت مرکزی Nexus</b>\n\n{format_stats_message(chat_id)}",
                reply_markup=get_admin_inline_markup(),
                parse_mode="HTML",
            )
        except Exception:
            bot.send_message(
                chat_id,
                f"⚙️ <b>پنل مدیریت مرکزی Nexus</b>\n\n{format_stats_message(chat_id)}",
                reply_markup=get_admin_inline_markup(),
                parse_mode="HTML",
            )
        return

    if call.data == "adm_export_clean":
        if os.path.exists(CLEAN_ACCOUNTS_FILE) and os.path.getsize(CLEAN_ACCOUNTS_FILE) > 0:
            with open(CLEAN_ACCOUNTS_FILE, "rb") as f:
                bot.send_document(chat_id, f, caption="📦 لیست حساب‌های بدون سفارش ثبت‌شده.")
        else:
            bot.send_message(chat_id, "❌ هنوز هیچ حساب بدون سفارشی در فایل ثبت نشده است.")
        return

    if call.data == "adm_set_proxy":
        msg = bot.send_message(chat_id, "🌐 لطفاً پروکسی‌های خود را ارسال کنید (هر خط یک پروکسی یا فایل .txt):")
        bot.register_next_step_handler(msg, process_proxy_list)
        return

    if not redis_client:
        bot.send_message(chat_id, "❌ خطا: اتصال ردیس برقرار نیست.")
        return

    if call.data == "adm_export_links":
        try:
            records = redis_client.hgetall("jet:bulk_accounts")
            if not records:
                bot.send_message(chat_id, "❌ هیچ لینکی در سیستم موجود نیست.")
                return
            lines = [f"🔗 لیست لینک‌های سالم ساخته شده (تعداد: {len(records)}):\n"]
            for phone, val in records.items():
                try:
                    data = json.loads(val)
                    link = f"{WEBHOOK_URL}/auth/{data.get('token', '')}"
                    created_at = data.get("created_at", "")
                    line = f"📱 شماره: {phone}\n🔗 لینک: {link}"
                    if created_at:
                        line += f"\n🕒 زمان: {created_at}"
                    lines.append(line + "\n──────────────────")
                except Exception:
                    continue
            text_content = "\n".join(lines)
            file_bytes = io.BytesIO(text_content.encode('utf-8'))
            file_bytes.name = f"Nexus_Clean_Links_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            bot.send_document(chat_id, file_bytes, caption=f"✅ لیست {len(records)} لینک ورود سالم آماده شد.")
        except Exception as e:
            bot.send_message(chat_id, f"❌ خطای ردیس: {html.escape(str(e))}", parse_mode="HTML")


@bot.message_handler(func=lambda m: m.text == "📦 دریافت حساب‌های بدون سفارش")
def get_clean_accounts(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    chat_id = message.chat.id
    if os.path.exists(CLEAN_ACCOUNTS_FILE) and os.path.getsize(CLEAN_ACCOUNTS_FILE) > 0:
        with open(CLEAN_ACCOUNTS_FILE, "rb") as f:
            bot.send_document(chat_id, f, caption="📦 لیست حساب‌های خامی که سفارش نداشته‌اند.")
    else:
        bot.send_message(chat_id, "❌ هنوز هیچ حساب کاربری بدون سفارشی ثبت نشده است.", reply_markup=get_main_keyboard(True))


@bot.message_handler(func=lambda m: m.text == "🌐 تنظیم پروکسی" or m.text == "/proxy")
def setup_proxy(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    msg_text = (
        "🌐 <b>تنظیمات پروکسی:</b>\n\n"
        "لطفاً پروکسی‌های خود را بفرستید (پیام متنی یا فایل .txt).\n"
        "برای لغو، <code>/cancel</code> را ارسال کنید."
    )
    msg = bot.reply_to(message, msg_text, parse_mode="HTML")
    bot.register_next_step_handler(msg, process_proxy_list)


@bot.message_handler(content_types=['document'])
def handle_docs(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    if message.document and message.document.file_name and message.document.file_name.endswith('.txt'):
        process_proxy_list(message)


def process_proxy_list(message):
    chat_id = message.chat.id
    is_admin = message.from_user.id in ADMIN_IDS

    if message.text and (message.text.startswith('/') or message.text in MENU_BUTTONS):
        bot.send_message(chat_id, "↩️ تنظیم پروکسی لغو شد.", reply_markup=get_main_keyboard(is_admin))
        return

    proxies_list = []
    try:
        if message.document:
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            content = downloaded_file.decode('utf-8', errors='replace').splitlines()
            for line in content:
                p = format_proxy(line)
                if p:
                    proxies_list.append(p)
        elif message.text:
            for line in message.text.splitlines():
                p = format_proxy(line)
                if p:
                    proxies_list.append(p)

        if proxies_list:
            user_proxies[chat_id] = proxies_list
            bot.send_message(
                chat_id,
                f"✅ تعداد {len(proxies_list)} پروکسی تنظیم شد.\n\nاکنون شماره موبایل را بفرستید.",
                reply_markup=get_main_keyboard(is_admin),
            )
        else:
            bot.send_message(chat_id, "❌ پروکسی معتبری یافت نشد!", reply_markup=get_main_keyboard(is_admin))
    except Exception as e:
        bot.send_message(chat_id, f"❌ خطا در پردازش پروکسی: {e}", reply_markup=get_main_keyboard(is_admin))


# ================= Phone & OTP Handlers =================
@bot.message_handler(func=lambda m: m.text and normalize_digits(m.text).startswith("09") and len(normalize_digits(m.text)) == 11)
def process_phone_step(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    chat_id = message.chat.id
    is_admin = True
    phone = normalize_digits(message.text)
    logs = [f"====== [ شروع عملیات برای: {phone} ] ======"]

    try:
        bot.send_message(chat_id, f"⏳ در حال برقراری ارتباط برای شماره {phone}...")
        session = requests.Session()

        proxies = user_proxies.get(chat_id, [])
        if proxies:
            proxy_url = random.choice(proxies)
            session.proxies = {"http": proxy_url, "https": proxy_url}
            logs.append(f"🌐 Proxy in use: {proxy_url}")
        else:
            logs.append("🌐 Proxy: No Proxy (Direct Connection)")
            logs.append("⚠️ توجه: اتصال مستقیم از سرور خارج ممکن است توسط دیجی‌کالا مسدود شود.")

        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'fa,en-US;q=0.7,en;q=0.3'
        })

        verifier, challenge = generate_pkce()
        nonce = secrets.token_urlsafe(16)
        state = secrets.token_urlsafe(16)
        redirect_uri = "https://www.digikala.com/sso-redirect/?back-url=%2Fprofile%2F"

        auth_url = (
            "https://auth.digikala.com/realms/dk-group/protocol/openid-connect/auth"
            f"?client_id=digikala-web-public&redirect_uri={urllib.parse.quote(redirect_uri)}"
            f"&response_mode=fragment&response_type=code&scope=openid&nonce={nonce}&state={state}"
            f"&code_challenge={challenge}&code_challenge_method=S256"
        )

        session.headers.update({"Referer": "https://www.digikala.com/"})
        res = session.get(auth_url, timeout=12)
        logs.append(f"▶️ GET Auth URL | Status: {res.status_code}")

        if res.status_code == 403:
            send_final_file(chat_id, "❌ آی‌پی سرور توسط دیجی‌کالا مسدود است (403). حتماً پروکسی ایرانی تنظیم کنید.", logs, is_admin=is_admin)
            return

        soup = BeautifulSoup(res.text, 'html.parser')
        form = soup.find('form')

        if not form:
            send_final_file(chat_id, "❌ خطا: فرم ورود در سایت یافت نشد.", logs, is_admin=is_admin)
            return

        action_url = form.get('action').replace('&amp;', '&')
        phone_payload = extract_hidden_inputs(form)
        phone_payload.update({"username": phone, "rememberMe": "on"})
        session.headers.update({"Referer": res.url})
        res = session.post(action_url, data=phone_payload, timeout=12)
        logs.append(f"◀️ POST Phone Response | Status: {res.status_code}")

        soup = BeautifulSoup(res.text, 'html.parser')

        # سوییچ هوشمند برای اکانت‌های رمزدار
        if soup.find('input', {'name': 'password'}) or soup.find('input', {'type': 'password'}):
            logs.append("⚠️ درخواست رمز عبور مشاهده شد. در حال ارسال توکن سوییچ پیامک...")
            otp_exec_input = soup.find('input', {'id': 'otp-auth-execution'})
            if otp_exec_input and otp_exec_input.has_attr('value'):
                exec_val = otp_exec_input['value']
                form_login = soup.find('form', {'id': 'dk-form-login'})
                form_action = form_login.get('action') if form_login else action_url
                if form_action:
                    form_action = form_action.replace('&amp;', '&')
                    session.headers.update({"Referer": res.url})
                    payload = {"authenticationExecution": exec_val}
                    res = session.post(form_action, data=payload, timeout=12)
                    logs.append(f"◀️ OTP Switch Response | Status: {res.status_code}")
                    soup = BeautifulSoup(res.text, 'html.parser')
            else:
                logs.append("❌ خطا: فیلد otp-auth-execution پیدا نشد.")
                send_final_file(chat_id, "❌ ساختار فرم رمز تغییر کرده است.", logs, is_admin=is_admin)
                return
        else:
            logs.append("⚡ اکانت بدون رمز عبور است. هدایت مستقیم به پیامک.")

        form_otp = soup.find('form')
        if not form_otp or not soup.find('input', {'name': 'code'}):
            snippet = res.text[:250].replace('\n', ' ')
            logs.append(f"❌ فرم دریافت پیامک یافت نشد:\n{snippet}")
            send_final_file(chat_id, "❌ فرم پیامک باز نشد. پروکسی خود را تغییر دهید.", logs, is_admin=is_admin)
            return

        otp_action_url = form_otp.get('action').replace('&amp;', '&')
        otp_hidden_fields = extract_hidden_inputs(form_otp)
        session.headers.update({"Referer": res.url})

        user_data[chat_id] = {
            'phone': phone,
            'session': session,
            'otp_action_url': otp_action_url,
            'otp_hidden_fields': otp_hidden_fields,
            'verifier': verifier,
            'redirect_uri': redirect_uri,
            'logs': logs
        }

        msg_reply = bot.send_message(chat_id, "✅ پیامک ارسال شد!\n\nلطفاً کد تایید ۵ یا ۶ رقمی را بفرستید (یا /cancel برای لغو):")
        bot.register_next_step_handler(msg_reply, process_code_step)

    except requests.exceptions.ProxyError:
        send_final_file(chat_id, "❌ پروکسی شما کار نمی‌کند، لطفاً پروکسی جدید ثبت کنید.", logs, is_admin=is_admin)
    except requests.exceptions.Timeout:
        send_final_file(chat_id, "❌ زمان اتصال به سرور دیجی‌کالا به پایان رسید (Timeout).", logs, is_admin=is_admin)
    except Exception as e:
        bot.send_message(chat_id, f"❌ خطای پیش‌بینی نشده در شروع عملیات: {str(e)}", reply_markup=get_main_keyboard(is_admin))


def process_code_step(message):
    chat_id = message.chat.id
    is_admin = message.from_user.id in ADMIN_IDS

    if message.text and (message.text.startswith('/') or message.text in MENU_BUTTONS):
        user_data.pop(chat_id, None)
        bot.send_message(chat_id, "↩️ عملیات ورود لغو شد.", reply_markup=get_main_keyboard(is_admin))
        return

    code = normalize_digits((message.text or "").strip())

    if chat_id not in user_data:
        bot.send_message(chat_id, "⚠️ نشست شما منقضی شد. لطفاً مجدداً شماره را بفرستید.", reply_markup=get_main_keyboard(is_admin))
        return

    udata = user_data[chat_id]
    session = udata['session']
    logs = udata['logs']
    phone = udata['phone']

    bot.send_message(chat_id, "⏳ در حال بررسی کد و استعلام سابقه خرید...")
    logs.append(f"💬 کد وارد شده: {code}")

    try:
        otp_payload = dict(udata.get('otp_hidden_fields') or {})
        otp_payload["code"] = code

        session.headers.update({"Referer": udata['otp_action_url']})
        res = session.post(udata['otp_action_url'], data=otp_payload, allow_redirects=False, timeout=12)
        logs.append(f"▶️ POST OTP Code | Status: {res.status_code}")

        auth_code = None
        curr_res = res

        # دنبال کردن ریدایرکت‌های HTTP و فرم‌های خودکار JS (مثل auto-submit-form دیجی‌کالا)
        for hop in range(6):
            loc = curr_res.headers.get('Location')
            if loc:
                logs.append(f"↪️ Redirect #{hop + 1}: {loc[:120]}")
                auth_code = extract_auth_code_from_url(loc)
                if auth_code:
                    break
                next_url = urllib.parse.urljoin(curr_res.url, loc)
                curr_res = session.get(next_url, allow_redirects=False, timeout=12)
                logs.append(f"▶️ Follow Redirect #{hop + 1} | Status: {curr_res.status_code}")
                continue

            if curr_res.status_code == 200:
                soup_step = BeautifulSoup(curr_res.text, 'html.parser')
                auto_form = soup_step.find('form', {'id': 'auto-submit-form'})
                if not auto_form and '.submit()' in curr_res.text:
                    auto_form = soup_step.find('form')

                if auto_form and auto_form.get('action'):
                    auto_action = urllib.parse.urljoin(
                        curr_res.url,
                        auto_form.get('action').replace('&amp;', '&')
                    )
                    auto_method = (auto_form.get('method') or 'post').lower()
                    auto_payload = extract_all_form_inputs(auto_form)
                    session.headers.update({"Referer": curr_res.url})
                    if auto_method == 'get':
                        curr_res = session.get(auto_action, params=auto_payload, allow_redirects=False, timeout=12)
                    else:
                        curr_res = session.post(auto_action, data=auto_payload, allow_redirects=False, timeout=12)
                    logs.append(f"↪️ Auto-Submit Form #{hop + 1} ({auto_method.upper()}) | Status: {curr_res.status_code}")
                    continue

            break

        if not auth_code:
            auth_code = extract_auth_code_from_url(curr_res.url)

        if not auth_code and curr_res.status_code == 200:
            sso_match = re.search(r'sso-redirect[^"\'\s<>]+', curr_res.text)
            if sso_match:
                auth_code = extract_auth_code_from_url(sso_match.group(0))

        if not auth_code:
            page_err = extract_html_error(curr_res.text)
            if page_err:
                logs.append(f"❌ پیام صفحه دیجی‌کالا: {page_err}")
            snippet = curr_res.text[:350].replace('\n', ' ')
            logs.append(f"🔎 HTML Snippet: {snippet}")
            err_notice = f" ({page_err})" if page_err else ""
            send_final_file(
                chat_id,
                f"❌ کد تایید پذیرفته نشد یا منقضی شده است{err_notice}.",
                logs,
                is_admin=is_admin,
            )
            return

        token_data = {
            "code": auth_code,
            "grant_type": "authorization_code",
            "client_id": "digikala-web-public",
            "redirect_uri": udata['redirect_uri'],
            "code_verifier": udata['verifier']
        }
        res_token = session.post(
            "https://auth.digikala.com/realms/dk-group/protocol/openid-connect/token",
            data=token_data,
            timeout=12,
        )
        logs.append(f"◀️ Token Exchange | Status: {res_token.status_code}")

        if res_token.status_code != 200:
            logs.append(f"❌ Token Error Body: {res_token.text[:400]}")
            send_final_file(chat_id, "❌ خطا در دریافت توکن نهایی از سرور دیجی‌کالا.", logs, is_admin=is_admin)
            return

        tokens = res_token.json()
        access_token = tokens.get('access_token')
        refresh_token = tokens.get('refresh_token')

        session.cookies.set("Digikala:User:Token:v2", access_token, domain=".api.digikala.com", path="/", secure=True)
        if refresh_token:
            session.cookies.set("Digikala:User:Refresh:Token:v2", refresh_token, domain=".api.digikala.com", path="/v1/user/", secure=True)

        total_orders, jet_token, jet_refresh = check_purchase_history(session, access_token, logs)

        # حالت ۱: خطا در بررسی سابقه خرید -> ذخیره نمی‌شود
        if total_orders is None:
            logs.append("⛔ به دلیل خطا در بررسی سابقه خرید، اکانت ذخیره نشد.")
            send_final_file(
                chat_id,
                "⚠️ ورود انجام شد اما بررسی سابقه خرید با خطا مواجه شد؛ بنابراین برای اطمینان این اکانت ذخیره نشد.",
                logs,
                is_admin=is_admin,
            )
            return

        # حالت ۲: اکانت دارای سابقه خرید است -> ذخیره نمی‌شود
        if total_orders > 0:
            logs.append(f"🚫 اکانت دارای {total_orders} سفارش است -> ذخیره نشد.")
            if redis_client:
                try:
                    redis_client.incr("jet:stats:skipped_ordered")
                except Exception:
                    pass
            send_final_file(
                chat_id,
                f"🚫 این اکانت دارای {total_orders} سابقه خرید است و طبق تنظیمات ذخیره نشد.",
                logs,
                is_admin=is_admin,
            )
            return

        # حالت ۳: اکانت خام است (total_orders == 0) -> ساخت لینک و ذخیره
        extension_cookies = []
        for cookie in session.cookies:
            extension_cookies.append({
                "domain": cookie.domain,
                "hostOnly": not cookie.domain.startswith('.'),
                "httpOnly": 'HttpOnly' in cookie._rest if cookie._rest else False,
                "name": cookie.name,
                "path": cookie.path,
                "sameSite": "lax",
                "secure": cookie.secure or True,
                "session": cookie.expires is None,
                "storeId": "0",
                "value": cookie.value,
                "expirationDate": int(time.time()) + (30 * 24 * 60 * 60)
            })

        # ذخیره توکن جت در origins مطابق ساختار jet_cleaner.py (persist:DKNow)
        origins = []
        if jet_token:
            dknow_user = {"token": jet_token}
            if jet_refresh:
                dknow_user["refreshToken"] = jet_refresh
            origins.append({
                "origin": "https://www.digikalajet.com",
                "localStorage": [
                    {
                        "name": "persist:DKNow",
                        "value": json.dumps(
                            {"user": json.dumps(dknow_user, ensure_ascii=False)},
                            ensure_ascii=False,
                        ),
                    }
                ],
            })

        final_json = {"cookies": extension_cookies, "origins": origins}
        final_json_str = json.dumps(final_json, ensure_ascii=False)
        session_token = secrets.token_urlsafe(14)
        generated_link = f"{WEBHOOK_URL}/auth/{session_token}"

        if redis_client:
            try:
                pipe = redis_client.pipeline()
                pipe.setex(f"jet_session:{session_token}", 30 * 24 * 3600, final_json_str)
                pipe.hset("jet:session_backups", session_token, final_json_str)
                record = {
                    "phone": phone,
                    "token": session_token,
                    "name": "کاربر دیجی‌کالا",
                    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "data": final_json,
                }
                pipe.hset("jet:bulk_accounts", phone, json.dumps(record, ensure_ascii=False))
                pipe.incr("jet:stats:created_links")
                pipe.execute()
                order_status_text = f"✅ اکانت بدون سفارش (0 خرید) است و ذخیره شد.\n🔗 لینک ورود: {generated_link}"
            except Exception as e:
                logs.append(f"❌ خطای ردیس: {e}")
                order_status_text = "⚠️ اکانت بدون سفارش است اما ذخیره در ردیس با خطا مواجه شد."
        else:
            order_status_text = "⚠️ اکانت بدون سفارش است (ردیس متصل نیست)."

        try:
            with open(CLEAN_ACCOUNTS_FILE, "a", encoding="utf-8") as f:
                f.write(f"{json.dumps({'phone': phone, 'link': generated_link, 'data': final_json}, ensure_ascii=False)}\n")
        except Exception:
            pass

        payload = {
            "phone": phone,
            "name": "کاربر دیجی‌کالا",
            "orders_count": 0,
            "link": generated_link,
            "data": final_json,
        }
        final_message = f"✅ ورود موفقیت‌آمیز بود!\n\n🛍 {order_status_text}\n\nفایل دیتای کاربر در حال ارسال است..."
        send_final_file(chat_id, final_message, logs, payload, is_admin=is_admin)

    except Exception as e:
        bot.send_message(chat_id, f"❌ خطا در پردازش کد تایید: {str(e)}", reply_markup=get_main_keyboard(is_admin))
    finally:
        user_data.pop(chat_id, None)


# ================= Secure Gateway Web Route =================
async def web_secure_gateway(request: web.Request):
    token_key = request.match_info.get("token")
    if not redis_client:
        return web.Response(text="خطای سرویس دیتابیس", status=500)

    try:
        session_data_str = redis_client.get(f"jet_session:{token_key}")
    except Exception:
        session_data_str = None

    if not session_data_str:
        return web.Response(text="پیوند منقضی یا نامعتبر است.", status=404, content_type="text/plain;charset=utf-8")

    user_agent = request.headers.get("User-Agent", "")
    app_header = request.headers.get("X-Client-App", "")

    if app_header == APP_SECRET_HEADER or "JetAppClient" in user_agent:
        return web.json_response({"status": "success", "session": json.loads(session_data_str)})

    html_blocked = (
        '<html dir="rtl" lang="fa"><meta charset="UTF-8">'
        '<body style="font-family:Tahoma;text-align:center;margin-top:50px;">'
        '<h3>دسترسی مسدود است</h3><p>لینک فقط در اپلیکیشن باز می‌شود.</p>'
        '</body></html>'
    )
    return web.Response(text=html_blocked, content_type="text/html")


def run_bot():
    logging.info("🤖 ربات تلگرام روشن شد...")
    while True:
        try:
            bot.infinity_polling(skip_pending=True, timeout=20, long_polling_timeout=20)
        except Exception as e:
            logging.error(f"Polling Crash Restarting... {e}")
            time.sleep(3)


if __name__ == "__main__":
    t = threading.Thread(target=run_bot, daemon=True)
    t.start()

    app = web.Application()
    app.router.add_get("/auth/{token}", web_secure_gateway)
    logging.info(f"🌐 وب‌سرور روی پورت {PORT} روشن شد...")
    web.run_app(app, host="0.0.0.0", port=PORT)
