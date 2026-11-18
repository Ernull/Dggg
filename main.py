"""
🚀 Nexus Extractor - PRO TELEGRAM BOT & WEB GATEWAY (UPDATED & FIXED)
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
REDIS_URL = os.environ.get("REDIS_URL", "redis://default:DWYgsUKwUJTjTMQpasIEHBYVPEMMhoPt@altaria.proxy.rlwy.net:22748")

APP_SECRET_HEADER = "JetApp-Secure-Client"
JET_API = "https://api.digikalajet.ir"

# اتصال امن با تایم‌اوت به دیتابیس ردیس
try:
    redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=5, socket_timeout=5)
except Exception as e:
    logging.error(f"Redis Init Error: {e}")
    redis_client = None

bot = telebot.TeleBot(TOKEN)
logging.basicConfig(format='%(asctime)s - %(message)s', level=logging.INFO)

# حافظه موقت ربات
user_data = {}
user_proxies = {}

# ================= Helper Functions =================
def generate_pkce():
    verifier = secrets.token_urlsafe(64)
    hashed = hashlib.sha256(verifier.encode('ascii')).digest()
    challenge = base64.urlsafe_b64encode(hashed).decode('ascii').rstrip('=')
    return verifier, challenge

def normalize_digits(text):
    """تبدیل اعداد فارسی به انگلیسی"""
    p2e = str.maketrans('۰۱۲۳۴۵۶۷۸۹', '0123456789')
    return text.translate(p2e).strip()

def format_proxy(p_str):
    p_str = p_str.strip()
    if not p_str or p_str.startswith('#'):
        return None
    if not re.match(r'^(http|https|socks4|socks5)://', p_str):
        p_str = f"http://{p_str}"
    return p_str

def get_main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    btn_stats = types.KeyboardButton("📊 آمار و پنل مدیریت")
    btn_clean = types.KeyboardButton("📦 دریافت حساب‌های بدون سفارش")
    btn_proxy = types.KeyboardButton("🌐 تنظیم پروکسی")
    markup.add(btn_stats)
    markup.add(btn_clean, btn_proxy)
    return markup

def get_admin_inline_keyboard():
    """ساخت کیبورد شیشه‌ای پنل مدیریت به همراه آمار لحظه‌ای"""
    bulk_count = 0
    sessions_count = 0
    processed_count = 0
    if redis_client:
        try:
            bulk_count = redis_client.hlen("jet:bulk_accounts")
            sessions_count = len(redis_client.keys("jet_session:*"))
            processed_count = redis_client.scard("jet:processed_phones")
        except Exception:
            pass

    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton(f"🔗 دریافت لینک‌های ساخته شده ({bulk_count} عدد)", callback_data="adm_export_links"),
        types.InlineKeyboardButton(f"📊 بروزرسانی آمار (سشن فعال: {sessions_count} | ثبت‌شده: {processed_count})", callback_data="adm_refresh_stats"),
        types.InlineKeyboardButton("🌐 تنظیم پروکسی", callback_data="adm_set_proxy"),
        types.InlineKeyboardButton("🗑 پاک‌سازی اکانت‌های خریددار احتمالی از ردیس", callback_data="adm_purge_ordered_hash")
    )
    return markup, bulk_count, sessions_count, processed_count

def send_final_file(chat_id, status_message, logs, extracted_data=None):
    try:
        bot.send_message(chat_id, status_message, reply_markup=get_main_keyboard())
        report_text = "\n".join(logs)
        if extracted_data:
            report_text += "\n\n====== [ اطلاعات خروجی نشست ] ======\n"
            report_text += json.dumps(extracted_data, ensure_ascii=False, indent=4)
            
        file_obj = io.BytesIO(report_text.encode('utf-8'))
        file_obj.name = f"Nexus_Report_{int(time.time())}.txt"
        bot.send_document(chat_id, file_obj, caption="📄 فایل لاگ و دیتای نشست")
    except Exception as e:
        bot.send_message(chat_id, f"❌ خطا در ارسال فایل لاگ: {str(e)}")

# ================= Jet API & Order History (Fixed via jet_cleaner logic) =================
def build_jet_headers(tok=None):
    """ساخت هدرهای استاندارد و دقیق دیجی‌کالاجت مطابق jet_cleaner.py"""
    cid = 'FINGERPRINTV2-' + uuid.uuid4().hex
    h = {
        'Accept': 'application/json, text/plain, */*',
        'X-Request-UUID': str(uuid.uuid4()),
        'ClientId': cid,
        'clientid-v2': cid,
        'ClientOs': 'Android',
        'Client': 'mobile',
        'platform-sso-disable-prod': '1',
        'session': f"{uuid.uuid4()}-V3*{int(time.time())}",
        'app-id': '08f293bd-0e29-4794-897c-a111b19da003',
        'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36',
        'Origin': 'https://www.digikalajet.com',
        'Referer': 'https://www.digikalajet.com/',
    }
    if tok:
        h['Authorization'] = tok
    return h

def count_orders(payload):
    """تعداد کل سفارش‌ها با هر وضعیتی (تحویل‌شده، در جریان یا لغوشده) دقیقاً مطابق jet_cleaner.py"""
    data = payload.get('data') or {}
    total = (data.get('pager') or {}).get('total_items', 0) or 0
    orders = data.get('orders') or {}
    if isinstance(orders, dict):
        total = max(total, sum(len(b) for b in orders.values() if isinstance(b, list)))
    return total

def build_jet_origins(jet_token, jet_refresh_token, phone):
    """ساخت ساختار استاندارد persist:DKNow برای سازگاری کامل با پنل و jet_cleaner.py"""
    if not jet_token:
        return []
    user_dict = {
        "token": jet_token,
        "refreshToken": jet_refresh_token or "",
        "phone": phone
    }
    persist_val = {
        "user": json.dumps(user_dict, ensure_ascii=False),
        "_persist": '{"version":-1,"rehydrated":true}'
    }
    return [{
        "origin": "https://www.digikalajet.com",
        "localStorage": [{
            "name": "persist:DKNow",
            "value": json.dumps(persist_val, ensure_ascii=False)
        }]
    }]

def check_purchase_history(session, dk_token, logs):
    """
    بررسی دقیق سوابق خرید از طریق API دیجی‌کالاجت.
    خروجی: (status_verdict, total_orders, jet_token, jet_refresh_token)
    status_verdict: 'clean' | 'ordered' | 'error'
    """
    try:
        logs.append("▶️ در حال بررسی سوابق خرید (ارتباط با دیجی‌کالاجت)...")
        sa_headers = {
            "x-web-client": "desktop",
            "x-web-client-id": "web",
            "x-web-optimize-response": "1",
            "Referer": "https://www.digikala.com/",
            "Authorization": f"Bearer {dk_token}" if not dk_token.startswith("Bearer") else dk_token,
            "User-Agent": session.headers.get("User-Agent", "Mozilla/5.0")
        }

        sa_token = None
        res_sa = session.get(
            "https://api.digikala.com/super-app/v1/sso/jet/",
            params={"redirect_url": "/?utm_source=digikala-superweb"},
            headers=sa_headers,
            allow_redirects=False,
            timeout=12
        )
        if 'Location' in res_sa.headers:
            match = re.search(r'sa_token=([^&]+)', res_sa.headers['Location'])
            if match:
                sa_token = match.group(1)

        if not sa_token:
            res_sa_redirect = session.get(
                "https://api.digikala.com/super-app/v1/sso/jet/",
                params={"redirect_url": "/?utm_source=digikala-superweb"},
                headers=sa_headers,
                allow_redirects=True,
                timeout=12
            )
            parsed = urllib.parse.urlparse(res_sa_redirect.url)
            qs = urllib.parse.parse_qs(parsed.query)
            if 'sa_token' in qs:
                sa_token = qs['sa_token'][0]

        if not sa_token:
            logs.append("❌ خطا در دریافت توکن SA_TOKEN از دیجی‌کالا")
            return 'error', -1, None, None

        # لاگین در سوپراپ جت
        sso_headers = build_jet_headers()
        sso_headers['Content-Type'] = 'application/json'
        res_jet = session.post(
            f"{JET_API}/super-app-sso/",
            headers=sso_headers,
            json={"dsa_token": sa_token},
            timeout=15
        )
        if res_jet.status_code != 200:
            logs.append(f"❌ خطا در لاگین جت | Status: {res_jet.status_code}")
            return 'error', -1, None, None

        jet_data = (res_jet.json() or {}).get('data') or {}
        jet_token = jet_data.get('token')
        jet_refresh = jet_data.get('refresh_token') or jet_data.get('refreshToken') or ""

        if not jet_token:
            logs.append("❌ توکن نهایی جت در پاسخ یافت نشد.")
            return 'error', -1, None, None

        # دریافت لیست سفارش‌ها با تلاش مجدد و پشتیبانی از Refresh Token (مشابه jet_cleaner.py)
        for attempt in range(3):
            res_orders = session.get(
                f"{JET_API}/order-shipments/?ch=jj",
                headers=build_jet_headers(jet_token),
                timeout=15
            )
            try:
                payload = res_orders.json()
            except Exception:
                payload = {}

            api_status = payload.get("status") if isinstance(payload, dict) else None

            if res_orders.status_code == 200 and api_status == 200:
                total_orders = count_orders(payload)
                logs.append(f"✅ سوابق خرید با موفقیت بررسی شد | تعداد کل سفارش‌ها: {total_orders}")
                verdict = 'ordered' if total_orders > 0 else 'clean'
                return verdict, total_orders, jet_token, jet_refresh

            # در صورت انقضای توکن، رفرش انجام شود
            if (res_orders.status_code in (401, 402) or api_status in (401, 402)) and jet_refresh:
                logs.append("🔄 توکن جت نیاز به رفرش دارد، در حال ارسال درخواست رفرش...")
                ref_res = session.post(
                    f"{JET_API}/user/refresh-token/?ch=jj",
                    json={"refresh_token": jet_refresh, "token": jet_token},
                    headers={'User-Agent': 'Mozilla/5.0', 'Content-Type': 'application/json'},
                    timeout=15
                )
                if ref_res.status_code == 200:
                    ref_json = ref_res.json() or {}
                    ref_pay = ref_json.get('data') if isinstance(ref_json.get('data'), dict) else ref_json
                    if ref_pay.get('token'):
                        jet_token = ref_pay['token']
                        jet_refresh = ref_pay.get('refresh_token') or jet_refresh
                        continue

        logs.append("❌ خطا در استعلام سفارش‌ها از سرور جت پس از ۳ بار تلاش.")
        return 'error', -1, jet_token, jet_refresh

    except Exception as e:
        logs.append(f"❌ خطای چک کردن خرید: {str(e)}")
        return 'error', -1, None, None

def delete_account_from_redis(phone):
    """حذف کامل اکانت خریددار از ردیس دقیقاً به روش jet_cleaner.py"""
    if not redis_client:
        return
    for hk in ('jet:bulk_accounts', 'jet:ordered_accounts'):
        rec_raw = redis_client.hget(hk, phone)
        if rec_raw:
            try:
                tok = (json.loads(rec_raw) or {}).get('token')
            except Exception:
                tok = None
            pipe = redis_client.pipeline()
            if tok:
                pipe.delete(f"jet_session:{tok}")
                pipe.hdel('jet:session_backups', tok)
            pipe.hdel(hk, phone)
            pipe.srem('jet:processed_phones', phone)
            pipe.execute()

# ================= Telegram Handlers =================
@bot.message_handler(commands=['start', 'help', 'admin'])
@bot.message_handler(func=lambda m: m.text == "📊 آمار و پنل مدیریت")
def send_welcome(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    markup, bulk_cnt, sess_cnt, proc_cnt = get_admin_inline_keyboard()
    panel_text = (
        "⚙️ **پنل مدیریت مرکزی Nexus:**\n\n"
        f"🔗 **لینک‌های بدون خرید (آماده):** `{bulk_cnt}`\n"
        f"🟢 **سشن‌های فعال در ردیس:** `{sess_cnt}`\n"
        f"📱 **شماره‌های پردازش‌شده:** `{proc_cnt}`\n\n"
        "💡 *توجه: اکانت‌های دارای سابقه خرید به هیچ عنوان در سیستم ذخیره نمی‌شوند.*\n"
        "لطفاً یکی از گزینه‌های زیر را انتخاب کنید:"
    )
    bot.send_message(message.chat.id, panel_text, reply_markup=markup, parse_mode="Markdown")
    if message.text and message.text.startswith("/"):
        bot.send_message(message.chat.id, "محیط آماده به کار است. برای ورود شماره موبایل بفرستید:", reply_markup=get_main_keyboard())

@bot.callback_query_handler(func=lambda call: call.data.startswith("adm_"))
def admin_callback(call):
    if call.from_user.id not in ADMIN_IDS:
        return
    chat_id = call.message.chat.id

    if not redis_client:
        bot.answer_callback_query(call.id, "❌ اتصال ردیس برقرار نیست.", show_alert=True)
        return

    if call.data == "adm_refresh_stats":
        markup, bulk_cnt, sess_cnt, proc_cnt = get_admin_inline_keyboard()
        panel_text = (
            "⚙️ **پنل مدیریت مرکزی Nexus:**\n\n"
            f"🔗 **لینک‌های بدون خرید (آماده):** `{bulk_cnt}`\n"
            f"🟢 **سشن‌های فعال در ردیس:** `{sess_cnt}`\n"
            f"📱 **شماره‌های پردازش‌شده:** `{proc_cnt}`\n\n"
            "💡 *توجه: اکانت‌های دارای سابقه خرید به هیچ عنوان در سیستم ذخیره نمی‌شوند.*\n"
            "لطفاً یکی از گزینه‌های زیر را انتخاب کنید:"
        )
        try:
            bot.edit_message_text(panel_text, chat_id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
        except Exception:
            pass
        bot.answer_callback_query(call.id, "✅ آمار بروزرسانی شد.")

    elif call.data == "adm_export_links":
        bot.answer_callback_query(call.id)
        try:
            records = redis_client.hgetall("jet:bulk_accounts")
            if not records:
                bot.send_message(chat_id, "❌ هیچ لینکی در سیستم موجود نیست.")
                return
            text_content = f"🔗 لیست کل لینک‌های بدون سابقه خرید (تعداد کل: {len(records)}):\n\n"
            valid_count = 0
            for phone, val in records.items():
                try:
                    data = json.loads(val)
                    token = data.get('token', '')
                    if not token:
                        continue
                    link = f"{WEBHOOK_URL}/auth/{token}"
                    text_content += f"📱 شماره: {phone}\n🔗 لینک: {link}\n──────────────────\n"
                    valid_count += 1
                except Exception:
                    continue
            file_bytes = io.BytesIO(text_content.encode('utf-8'))
            file_bytes.name = f"Nexus_Clean_Links_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            bot.send_document(chat_id, file_bytes, caption=f"✅ لیست {valid_count} لینک ورود (بدون سابقه خرید) آماده شد.")
        except Exception as e:
            bot.send_message(chat_id, f"❌ خطای ردیس: {e}")

    elif call.data == "adm_purge_ordered_hash":
        try:
            ordered_records = redis_client.hgetall("jet:ordered_accounts")
            removed = 0
            for phone in ordered_records.keys():
                delete_account_from_redis(phone)
                removed += 1
            bot.answer_callback_query(call.id, f"✅ تعداد {removed} اکانت خریددار قدیمی حذف شد.", show_alert=True)
        except Exception as e:
            bot.answer_callback_query(call.id, f"❌ خطا: {e}", show_alert=True)

    elif call.data == "adm_set_proxy":
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "🌐 لطفاً پروکسی‌های خود را ارسال کنید (هر خط یک پروکسی یا فایل txt):")
        bot.register_next_step_handler(call.message, process_proxy_list)

@bot.message_handler(func=lambda m: m.text == "📦 دریافت حساب‌های بدون سفارش")
def get_clean_accounts(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    chat_id = message.chat.id
    file_path = "clean_accounts.txt"
    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
        with open(file_path, "rb") as f:
            bot.send_document(chat_id, f, caption="📦 لیست حساب‌های خامی که هیچ سفارشی نداشته‌اند.")
    else:
        bot.send_message(chat_id, "❌ هنوز هیچ حساب کاربری بدون سفارشی در فایل محلی ثبت نشده است.", reply_markup=get_main_keyboard())

@bot.message_handler(func=lambda m: m.text == "🌐 تنظیم پروکسی" or m.text == "/proxy")
def setup_proxy(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    msg = ("🌐 **تنظیمات پروکسی:**\n\n"
           "لطفاً پروکسی‌های خود را بفرستید. (پیام متنی یا فایل .txt)")
    bot.reply_to(message, msg, parse_mode="Markdown")
    bot.register_next_step_handler(message, process_proxy_list)

@bot.message_handler(content_types=['document'])
def handle_docs(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    if message.document and message.document.file_name.endswith('.txt'):
        process_proxy_list(message)

def process_proxy_list(message):
    chat_id = message.chat.id
    proxies_list = []
    try:
        if message.document:
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            content = downloaded_file.decode('utf-8').splitlines()
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
            bot.send_message(chat_id, f"✅ تعداد {len(proxies_list)} پروکسی تنظیم شد.\n\nاکنون شماره موبایل را بفرستید.", reply_markup=get_main_keyboard())
        else:
            bot.send_message(chat_id, "❌ پروکسی معتبری یافت نشد!", reply_markup=get_main_keyboard())
    except Exception as e:
        bot.send_message(chat_id, f"❌ خطا در پردازش پروکسی: {e}", reply_markup=get_main_keyboard())

# ================= Phone & OTP Handlers =================
@bot.message_handler(func=lambda m: m.text and normalize_digits(m.text).startswith("09") and len(normalize_digits(m.text)) == 11)
def process_phone_step(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    chat_id = message.chat.id
    phone = normalize_digits(message.text)

    try:
        bot.send_message(chat_id, f"⏳ در حال برقراری ارتباط برای شماره {phone}...")
        session = requests.Session()
        logs = [f"====== [ شروع عملیات برای: {phone} ] ======"]

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
            send_final_file(chat_id, "❌ آی‌پی سرور توسط دیجی‌کالا مسدود است (403). حتماً پروکسی ایرانی تنظیم کنید.", logs)
            return

        soup = BeautifulSoup(res.text, 'html.parser')
        form = soup.find('form')

        if not form:
            send_final_file(chat_id, "❌ خطا: فرم ورود در سایت یافت نشد.", logs)
            return

        action_url = form.get('action').replace('&amp;', '&')
        session.headers.update({"Referer": res.url})
        res = session.post(action_url, data={"username": phone, "rememberMe": "on"}, timeout=12)
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
                send_final_file(chat_id, "❌ ساختار فرم رمز تغییر کرده است.", logs)
                return
        else:
            logs.append("⚡ اکانت بدون رمز عبور است. هدایت مستقیم به پیامک.")

        form_otp = soup.find('form')
        if not form_otp or not soup.find('input', {'name': 'code'}):
            snippet = res.text[:200].replace('\n', ' ')
            logs.append(f"❌ فرم دریافت پیامک یافت نشد:\n{snippet}")
            send_final_file(chat_id, "❌ فرم پیامک باز نشد. پروکسی خود را تغییر دهید.", logs)
            return

        otp_action_url = form_otp.get('action').replace('&amp;', '&')
        session.headers.update({"Referer": res.url})

        user_data[chat_id] = {
            'phone': phone,
            'session': session,
            'otp_action_url': otp_action_url,
            'verifier': verifier,
            'redirect_uri': redirect_uri,
            'logs': logs
        }

        msg_reply = bot.send_message(chat_id, "✅ پیامک ارسال شد!\n\nلطفاً کد تایید ۵ یا ۶ رقمی را بفرستید:")
        bot.register_next_step_handler(msg_reply, process_code_step)

    except requests.exceptions.ProxyError:
        send_final_file(chat_id, "❌ پروکسی شما کار نمی‌کند، لطفاً پروکسی جدید ثبت کنید.", logs)
    except requests.exceptions.Timeout:
        send_final_file(chat_id, "❌ زمان اتصال به سرور دیجی‌کالا به پایان رسید (Timeout).", logs)
    except Exception as e:
        bot.send_message(chat_id, f"❌ خطای پیش‌بینی نشده در شروع عملیات: {str(e)}", reply_markup=get_main_keyboard())

def process_code_step(message):
    chat_id = message.chat.id
    code = normalize_digits(message.text.strip())

    if chat_id not in user_data:
        bot.send_message(chat_id, "⚠️ نشست شما منقضی شد. لطفاً مجدداً شماره را بفرستید.", reply_markup=get_main_keyboard())
        return

    udata = user_data[chat_id]
    session = udata['session']
    logs = udata['logs']
    phone = udata['phone']

    bot.send_message(chat_id, "⏳ در حال بررسی کد و استعلام سوابق خرید...")
    logs.append(f"💬 کد وارد شده: {code}")

    try:
        session.headers.update({"Referer": "https://auth.digikala.com/"})
        res = session.post(udata['otp_action_url'], data={"code": code}, allow_redirects=False, timeout=12)
        logs.append(f"▶️ POST OTP Code | Status: {res.status_code}")

        auth_code = None
        if 'Location' in res.headers:
            cmatch = re.search(r'code=([^&]+)', res.headers['Location'])
            if cmatch:
                auth_code = cmatch.group(1)
        if not auth_code:
            cmatch = re.search(r'code=([^&]+)', res.text)
            if cmatch:
                auth_code = cmatch.group(1)

        if not auth_code:
            send_final_file(chat_id, "❌ کد تایید اشتباه یا منقضی شده است.", logs)
            return

        token_data = {
            "code": auth_code,
            "grant_type": "authorization_code",
            "client_id": "digikala-web-public",
            "redirect_uri": udata['redirect_uri'],
            "code_verifier": udata['verifier']
        }
        res_token = session.post("https://auth.digikala.com/realms/dk-group/protocol/openid-connect/token", data=token_data, timeout=12)
        logs.append(f"◀️ Token Exchange | Status: {res_token.status_code}")

        if res_token.status_code == 200:
            tokens = res_token.json()
            access_token = tokens.get('access_token')
            refresh_token = tokens.get('refresh_token')

            session.cookies.set("Digikala:User:Token:v2", access_token, domain=".api.digikala.com", path="/", secure=True)
            if refresh_token:
                session.cookies.set("Digikala:User:Refresh:Token:v2", refresh_token, domain=".api.digikala.com", path="/v1/user/", secure=True)

            # بررسی دقیق سابقه خرید به روش jet_cleaner
            verdict, total_orders, jet_tok, jet_ref = check_purchase_history(session, access_token, logs)

            # ۱. اگر اکانت خرید داشته باشد -> ذخیره نشود و در صورت وجود قبلی از ردیس پاک شود
            if verdict == 'ordered':
                delete_account_from_redis(phone)
                logs.append(f"🚫 اکانت دارای {total_orders} سابقه خرید است. طبق دستور، ذخیره نشد و از ردیس حذف گردید.")
                final_message = (
                    f"🚫 **اکانت دارای سابقه خرید است!**\n\n"
                    f"📱 شماره: `{phone}`\n"
                    f"🛍 تعداد سفارش‌ها: `{total_orders}`\n\n"
                    f"❌ این اکانت در دیتابیس ذخیره نشد و هیچ لینکی برای آن ساخته نشد."
                )
                send_final_file(chat_id, final_message, logs)
                return

            # ۲. اگر در ارتباط با جت خطا رخ داده باشد -> برای جلوگیری از ورود اکانت خریددار، ذخیره نشود
            if verdict == 'error':
                logs.append("⚠️ به دلیل عدم امکان استعلام دقیق سابقه خرید از سرور جت، اکانت ذخیره نشد.")
                final_message = (
                    f"⚠️ **خطا در استعلام سابقه خرید از جت!**\n\n"
                    f"برای جلوگیری از ذخیره شدن اکانت‌های خریددار، این نشست ذخیره نشد. لطفاً مجدداً تلاش کنید."
                )
                send_final_file(chat_id, final_message, logs)
                return

            # ۳. اکانت کاملاً تمیز و بدون سابقه خرید است (verdict == 'clean' and total_orders == 0)
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

            # قرار دادن توکن جت در origins برای سازگاری با jet_cleaner.py و اپلیکیشن
            jet_origins = build_jet_origins(jet_tok, jet_ref, phone)
            final_json = {"cookies": extension_cookies, "origins": jet_origins}
            final_json_str = json.dumps(final_json, ensure_ascii=False)
            session_token = secrets.token_urlsafe(14)
            generated_link = f"{WEBHOOK_URL}/auth/{session_token}"

            # ذخیره اکانت تمیز در ردیس همراه با بکاپ و لیست شماره‌های پردازش‌شده
            if redis_client:
                try:
                    # اگر قبلاً لینک قدیمی داشته، سشن قدیمی را پاک می‌کنیم
                    old_rec = redis_client.hget("jet:bulk_accounts", phone)
                    if old_rec:
                        try:
                            old_tok = json.loads(old_rec).get("token")
                            if old_tok:
                                redis_client.delete(f"jet_session:{old_tok}")
                                redis_client.hdel("jet:session_backups", old_tok)
                        except Exception:
                            pass

                    record = {
                        "phone": phone,
                        "token": session_token,
                        "name": "کاربر دیجی‌کالا",
                        "data": final_json,
                        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
                    pipe = redis_client.pipeline()
                    pipe.setex(f"jet_session:{session_token}", 30 * 24 * 3600, final_json_str)
                    pipe.hset("jet:session_backups", session_token, final_json_str)
                    pipe.hset("jet:bulk_accounts", phone, json.dumps(record, ensure_ascii=False))
                    pipe.sadd("jet:processed_phones", phone)
                    pipe.execute()

                    total_links = redis_client.hlen("jet:bulk_accounts")
                    order_status_text = (
                        f"✅ اکانت خام و بدون سابقه خرید است (0 سفارش).\n"
                        f"🔗 لینک ورود: {generated_link}\n"
                        f"📊 کل لینک‌های بدون خرید موجود: {total_links}"
                    )
                except Exception as e:
                    logs.append(f"❌ خطای ردیس: {e}")
                    order_status_text = "⚠️ اکانت بدون خرید است اما در ذخیره ردیس خطا رخ داد."
            else:
                order_status_text = "⚠️ ردیس متصل نیست."

            try:
                with open("clean_accounts.txt", "a", encoding="utf-8") as f:
                    f.write(f"{json.dumps({'phone': phone, 'link': generated_link, 'data': final_json}, ensure_ascii=False)}\n")
            except Exception:
                pass

            payload = {"phone": phone, "name": "کاربر دیجی‌کالا", "orders_count": 0, "link": generated_link, "data": final_json}
            final_message = f"✅ ورود موفقیت‌آمیز بود!\n\n{order_status_text}\n\nفایل دیتای کاربر در حال ارسال است..."
            send_final_file(chat_id, final_message, logs, payload)
        else:
            send_final_file(chat_id, "❌ خطا در دریافت توکن نهایی از سرور دیجی‌کالا.", logs)

    except Exception as e:
        bot.send_message(chat_id, f"❌ خطا در پردازش کد تایید: {str(e)}", reply_markup=get_main_keyboard())
    finally:
        if chat_id in user_data:
            del user_data[chat_id]

# ================= Secure Gateway Web Route =================
async def web_secure_gateway(request: web.Request):
    token_key = request.match_info.get("token")
    if not redis_client:
        return web.Response(text="خطای سرویس دیتابیس", status=500)

    try:
        session_data_str = redis_client.get(f"jet_session:{token_key}")
        # بازیابی خودکار از بکاپ در صورت منقضی شدن کلید اصلی
        if not session_data_str:
            session_data_str = redis_client.hget("jet:session_backups", token_key)
    except Exception:
        session_data_str = None

    if not session_data_str:
        return web.Response(text="پیوند منقضی یا نامعتبر است.", status=404, content_type="text/plain;charset=utf-8")

    user_agent = request.headers.get("User-Agent", "")
    app_header = request.headers.get("X-Client-App", "")

    if app_header == APP_SECRET_HEADER or "JetAppClient" in user_agent:
        return web.json_response({"status": "success", "session": json.loads(session_data_str)})

    html_blocked = '<html dir="rtl" lang="fa"><meta charset="UTF-8"><body style="font-family:Tahoma;text-align:center;margin-top:50px;"><h3>دسترسی مسدود است</h3><p>لینک فقط در اپلیکیشن باز می‌شود.</p></body></html>'
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
