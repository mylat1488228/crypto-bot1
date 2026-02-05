import telebot
from telebot import types
import yfinance as yf
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import io
import threading
import time
import schedule
import sqlite3
import pandas as pd
import numpy as np
import random
import os
import requests # НУЖНО ДЛЯ ТОЧНЫХ КУРСОВ
from datetime import datetime

# --- КОНФИГУРАЦИЯ ---
BOT_TOKEN = os.getenv('BOT_TOKEN', '8212929038:AAEJ_P_Ttiy8-nrf1W2KfOqxQDiJNY1MlGk')

# !!! ВСТАВЬ СЮДА СВОЙ ЦИФРОВОЙ ID !!!
MAIN_ADMIN_ID = 7031015199

bot = telebot.TeleBot(BOT_TOKEN)

# --- СПИСКИ ВАЛЮТ (Для меню) ---
CURRENCIES = {
    '💵 USDT': 'USDT', '🇺🇸 USD': 'USD', '₿ BTC': 'BTC',
    '💎 ETH': 'ETH', '💎 TON': 'TON', '🇪🇺 EUR': 'EUR',
    '🇷🇺 RUB': 'RUB', '🇰🇬 KGS': 'KGS', '🇨🇳 CNY': 'CNY',
    '🇦🇪 AED': 'AED', '🇹🇯 TJS': 'TJS', '🇺🇿 UZS': 'UZS'
}

# --- КЭШ КУРСОВ (ЧТОБЫ БОТ РАБОТАЛ БЫСТРО) ---
# Храним курсы относительно USD (1 USD = X валюты)
rates_cache = {}
last_update = 0

def update_rates():
    """Обновляет курсы валют с точных API"""
    global rates_cache, last_update
    
    # Не обновляем чаще чем раз в 10 минут
    if time.time() - last_update < 600 and rates_cache:
        return

    print("🔄 Обновляю курсы валют...")
    new_rates = {'USD': 1.0, 'USDT': 1.0} # USDT считаем равным USD для простоты
    
    try:
        # 1. ФИАТНЫЕ ВАЛЮТЫ (Open Exchange Rates)
        resp = requests.get("https://open.er-api.com/v6/latest/USD").json()
        if 'rates' in resp:
            for code in ['RUB', 'KGS', 'CNY', 'AED', 'TJS', 'UZS', 'EUR']:
                if code in resp['rates']:
                    new_rates[code] = resp['rates'][code]
    except Exception as e:
        print(f"Ошибка Фиат API: {e}")

    try:
        # 2. КРИПТОВАЛЮТЫ (CoinGecko)
        # Получаем цену в USD
        cg_ids = "bitcoin,ethereum,the-open-network"
        resp = requests.get(f"https://api.coingecko.com/api/v3/simple/price?ids={cg_ids}&vs_currencies=usd").json()
        
        # CoinGecko дает цену 1 монеты в долларах. Нам нужно наоборот (сколько монет в 1 долларе),
        # либо мы будем использовать логику кросс-курса.
        # Для удобства сохраним прямую цену в долларах, а в конвертере учтем это.
        if 'bitcoin' in resp: new_rates['BTC_PRICE'] = resp['bitcoin']['usd']
        if 'ethereum' in resp: new_rates['ETH_PRICE'] = resp['ethereum']['usd']
        if 'the-open-network' in resp: new_rates['TON_PRICE'] = resp['the-open-network']['usd']
        
    except Exception as e:
        print(f"Ошибка Крипто API: {e}")

    rates_cache = new_rates
    last_update = time.time()
    print("✅ Курсы обновлены")

# --- БАЗА ДАННЫХ ---
DB_NAME = "bot_data.db"
user_states = {} 
global_logs = []

def init_db():
    with sqlite3.connect(DB_NAME) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            role TEXT DEFAULT 'executor'
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            type TEXT,
            limit_exp REAL,
            active INTEGER DEFAULT 1
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            project_id INTEGER,
            turnover REAL,
            expenses REAL,
            profit REAL,
            roi REAL,
            margin REAL,
            date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS watchlist (
            user_id INTEGER,
            ticker TEXT,
            UNIQUE(user_id, ticker)
        )''')
        db.commit()

init_db()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def safe_float(text):
    try:
        if not text: return 0.0
        clean_text = text.replace(',', '.').replace(' ', '').replace("'", "")
        return float(clean_text)
    except:
        return None

def log_action(uid, username, action):
    t = datetime.now().strftime("%d.%m %H:%M")
    u = username if username else "Unknown"
    entry = f"[{t}] @{u} ({uid}): {action}"
    global_logs.append(entry)
    if len(global_logs) > 100: global_logs.pop(0)

def get_user_role(uid):
    if uid == MAIN_ADMIN_ID: return 'admin'
    with sqlite3.connect(DB_NAME) as db:
        res = db.execute("SELECT role FROM users WHERE user_id = ?", (uid,)).fetchone()
        return res[0] if res else 'executor'

def set_state(uid, step, data=None):
    if uid not in user_states: user_states[uid] = {}
    user_states[uid]['step'] = step
    if data: 
        if 'data' not in user_states[uid]: user_states[uid]['data'] = {}
        user_states[uid]['data'].update(data)

def update_data(uid, key, value):
    if uid in user_states:
        if 'data' not in user_states[uid]: user_states[uid]['data'] = {}
        user_states[uid]['data'][key] = value

def clear_state(uid):
    if uid in user_states: del user_states[uid]

# --- ЛОГИКА КОНВЕРТАЦИИ ---
def convert_currency(amount, from_cur, to_cur):
    update_rates() # Проверяем актуальность
    
    # 1. Приводим исходную валюту к USD
    usd_amount = 0.0
    
    # Если исходная - Крипта (у нее цена записана как "сколько стоит 1 монета")
    if from_cur == 'BTC': usd_amount = amount * rates_cache.get('BTC_PRICE', 0)
    elif from_cur == 'ETH': usd_amount = amount * rates_cache.get('ETH_PRICE', 0)
    elif from_cur == 'TON': usd_amount = amount * rates_cache.get('TON_PRICE', 0)
    # Если исходная - Фиат/Стейбл (цена записана как "сколько в 1 долларе")
    else:
        rate = rates_cache.get(from_cur, 0)
        if rate == 0: return 0
        usd_amount = amount / rate

    # 2. Переводим USD в целевую валюту
    final_amount = 0.0
    
    if to_cur == 'BTC': final_amount = usd_amount / rates_cache.get('BTC_PRICE', 1)
    elif to_cur == 'ETH': final_amount = usd_amount / rates_cache.get('ETH_PRICE', 1)
    elif to_cur == 'TON': final_amount = usd_amount / rates_cache.get('TON_PRICE', 1)
    else:
        rate = rates_cache.get(to_cur, 0)
        final_amount = usd_amount * rate
        
    return final_amount

# --- ТЕКСТЫ ОБУЧЕНИЯ ---
def send_tutorial(uid):
    text = (
        "👋 **Добро пожаловать! Я твой Финансовый Ассистент.**\n\n"
        "📜 **Инструкция:**\n\n"
        "🧮 **Калькулятор**\n"
        "Считает обмен валют по РЕАЛЬНОМУ курсу биржи. Учитывает комиссию.\n"
        "Пример: Отдаю 100 USDT -> Получаю RUB (комиссия 1%).\n\n"
        "🔀 **Тройной Обмен**\n"
        "Считает цепочки арбитража. Например: USDT -> KGS -> RUB.\n\n"
        "➕ **Отчет (Проекты)**\n"
        "Вноси доходы и расходы. Я посчитаю чистую прибыль, ROI и Маржу.\n\n"
        "💬 **AI Советник**\n"
        "Анализирует рынок и подсказывает, что сейчас выгодно купить/продать."
    )
    bot.send_message(uid, text, parse_mode="Markdown")

# --- КЛАВИАТУРЫ ---
def main_menu(uid):
    role = get_user_role(uid)
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add("🧮 Калькулятор", "🔀 Тройной Обмен")
    markup.add("📈 Графики", "⭐ Мой список")
    markup.add("💬 AI Советник", "➕ Отчет (Проекты)")
    markup.add("❓ Помощь / Инструкция")
    
    if role == 'admin': 
        markup.add("🆕 Создать Проект", "⚙️ Админ Консоль")
        
    return markup

def tickers_kb(prefix):
    markup = types.InlineKeyboardMarkup(row_width=2)
    btns = []
    for name, code in CURRENCIES.items():
        btns.append(types.InlineKeyboardButton(name, callback_data=f"{prefix}_{code}"))
    markup.add(*btns)
    return markup

# --- СТАРТ ---
@bot.message_handler(commands=['start'])
def start(message):
    uid = message.chat.id
    clear_state(uid)
    uname = message.from_user.username
    role = 'admin' if uid == MAIN_ADMIN_ID else 'executor'
    
    is_new = False
    with sqlite3.connect(DB_NAME) as db:
        exists = db.execute("SELECT 1 FROM users WHERE user_id = ?", (uid,)).fetchone()
        if not exists:
            is_new = True
            db.execute("INSERT INTO users (user_id, username, role) VALUES (?, ?, ?)", (uid, uname, role))
        if role == 'admin':
            db.execute("UPDATE users SET role = 'admin' WHERE user_id = ?", (uid,))
        db.commit()
    
    log_action(uid, uname, "Start")
    update_rates() # Подгружаем курсы сразу при старте
    
    if is_new:
        send_tutorial(uid)
        time.sleep(2)
        bot.send_message(uid, "Готов к работе!", reply_markup=main_menu(uid))
    else:
        bot.send_message(uid, f"С возвращением!", reply_markup=main_menu(uid))

@bot.message_handler(func=lambda m: m.text == "❓ Помощь / Инструкция")
def help_btn(message):
    send_tutorial(message.chat.id)

# ===========================
# 1. СОЗДАНИЕ ПРОЕКТОВ
# ===========================
@bot.message_handler(func=lambda m: m.text == "🆕 Создать Проект")
def proj_start(message):
    if get_user_role(message.chat.id) != 'admin': return
    bot.send_message(message.chat.id, "Введите название проекта:", reply_markup=types.ReplyKeyboardRemove())
    set_state(message.chat.id, 'proj_name')

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'proj_name')
def proj_name(message):
    update_data(message.chat.id, 'name', message.text)
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add("Карта", "Сим", "Проект", "Другое")
    bot.send_message(message.chat.id, "Выберите тип проекта:", reply_markup=markup)
    set_state(message.chat.id, 'proj_type')

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'proj_type')
def proj_type(message):
    if message.text not in ["Карта", "Сим", "Проект", "Другое"]: return bot.send_message(message.chat.id, "Кнопкой!")
    update_data(message.chat.id, 'type', message.text)
    bot.send_message(message.chat.id, "Лимит расходов (число, или 0):", reply_markup=types.ReplyKeyboardRemove())
    set_state(message.chat.id, 'proj_limit')

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'proj_limit')
def proj_finish(message):
    limit = safe_float(message.text)
    if limit is None: return bot.send_message(message.chat.id, "Ошибка! Введите число.")
    
    data = user_states[message.chat.id]['data']
    with sqlite3.connect(DB_NAME) as db:
        db.execute("INSERT INTO projects (name, type, limit_exp) VALUES (?, ?, ?)", (data['name'], data['type'], limit))
        db.commit()
    bot.send_message(message.chat.id, f"✅ Проект **{data['name']}** создан!", parse_mode="Markdown", reply_markup=main_menu(message.chat.id))
    clear_state(message.chat.id)

# ===========================
# 2. ОТЧЕТЫ (ФИНАНСЫ)
# ===========================
@bot.message_handler(func=lambda m: m.text == "➕ Отчет (Проекты)")
def rep_start(message):
    clear_state(message.chat.id)
    with sqlite3.connect(DB_NAME) as db:
        projs = db.execute("SELECT id, name, type FROM projects WHERE active=1").fetchall()
    if not projs: return bot.send_message(message.chat.id, "Нет активных проектов.")
    markup = types.InlineKeyboardMarkup()
    for p in projs: markup.add(types.InlineKeyboardButton(f"{p[1]} ({p[2]})", callback_data=f"rep_p_{p[0]}"))
    bot.send_message(message.chat.id, "Выберите проект:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('rep_p_'))
def rep_sel(call):
    pid = int(call.data.split('_')[2])
    with sqlite3.connect(DB_NAME) as db:
        pname = db.execute("SELECT name FROM projects WHERE id=?", (pid,)).fetchone()[0]
    set_state(call.message.chat.id, 'rep_turn', {'pid': pid, 'pname': pname})
    bot.edit_message_text(f"Проект: {pname}\n\n💰 Введите **Оборот** (грязными):", call.message.chat.id, call.message.message_id, parse_mode="Markdown")

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'rep_turn')
def rep_turn(message):
    val = safe_float(message.text)
    if val is None: return bot.send_message(message.chat.id, "Введите число!")
    update_data(message.chat.id, 'turnover', val)
    bot.send_message(message.chat.id, "📦 Расход на **Материалы** (если нет - 0):", parse_mode="Markdown")
    set_state(message.chat.id, 'rep_mat')

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'rep_mat')
def rep_mat(message):
    val = safe_float(message.text)
    if val is None: return bot.send_message(message.chat.id, "Введите число!")
    update_data(message.chat.id, 'mat', val)
    bot.send_message(message.chat.id, "💳 Расход на **Комиссии**:", parse_mode="Markdown")
    set_state(message.chat.id, 'rep_com')

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'rep_com')
def rep_com(message):
    val = safe_float(message.text)
    if val is None: return bot.send_message(message.chat.id, "Введите число!")
    update_data(message.chat.id, 'com', val)
    bot.send_message(message.chat.id, "👥 **Проценты** другим людям:", parse_mode="Markdown")
    set_state(message.chat.id, 'rep_perc')

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'rep_perc')
def rep_perc(message):
    val = safe_float(message.text)
    if val is None: return bot.send_message(message.chat.id, "Введите число!")
    update_data(message.chat.id, 'perc', val)
    bot.send_message(message.chat.id, "🛠 **Дополнительные** расходы (или 0):", parse_mode="Markdown")
    set_state(message.chat.id, 'rep_extra')

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'rep_extra')
def rep_finish(message):
    extra = safe_float(message.text)
    if extra is None: return bot.send_message(message.chat.id, "Введите число!")
    
    d = user_states[message.chat.id]['data']
    turnover = d['turnover']
    total_expenses = d['mat'] + d['com'] + d['perc'] + extra
    net_profit = turnover - total_expenses
    roi = (net_profit / total_expenses * 100) if total_expenses > 0 else 0
    margin = (net_profit / turnover * 100) if turnover > 0 else 0
    
    with sqlite3.connect(DB_NAME) as db:
        db.execute("""INSERT INTO reports (user_id, project_id, turnover, expenses, profit, roi, margin) VALUES (?, ?, ?, ?, ?, ?, ?)""", 
                   (message.chat.id, d['pid'], turnover, total_expenses, net_profit, roi, margin))
        db.commit()
        
    res = (f"✅ **Отчет принят!**\n\n📂 **Проект:** {d['pname']}\n💰 **Оборот:** *{turnover:,.2f} ₽*\n"
           f"💸 **Общие расходы:** *{total_expenses:,.2f} ₽*\n💵 **Чистая прибыль:** *{net_profit:,.2f} ₽*\n"
           f"📈 **ROI:** *{roi:.1f}%*\n📊 **Маржа:** *{margin:.1f}%*")
    
    bot.send_message(message.chat.id, res, parse_mode="Markdown", reply_markup=main_menu(message.chat.id))
    clear_state(message.chat.id)

# ===========================
# 3. АДМИН ПАНЕЛЬ + РАССЫЛКА
# ===========================
@bot.message_handler(func=lambda m: m.text == "⚙️ Админ Консоль")
def admin_cmd(message):
    if message.chat.id != MAIN_ADMIN_ID: return
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(types.InlineKeyboardButton("👥 Пользователи", callback_data="adm_users"),
               types.InlineKeyboardButton("📜 Логи", callback_data="adm_logs"))
    markup.add(types.InlineKeyboardButton("📢 Рассылка (Update)", callback_data="adm_broadcast"))
    bot.send_message(message.chat.id, "🔒 Панель управления", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "adm_users")
def adm_users(call):
    with sqlite3.connect(DB_NAME) as db:
        users = db.execute("SELECT user_id, username, role, join_date FROM users").fetchall()
    text = f"👥 **Всего пользователей: {len(users)}**\n\n"
    for u in users:
        name = f"@{u[1]}" if u[1] else "Без ника"
        text += f"ID: `{u[0]}` | {name} | {u[2]}\n"
    if len(text) > 4000: text = text[:4000] + "..."
    bot.send_message(call.message.chat.id, text, parse_mode="Markdown")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "adm_logs")
def adm_logs(call):
    bot.send_message(call.message.chat.id, "\n".join(global_logs[-15:]) or "Пусто")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "adm_broadcast")
def adm_broadcast_start(call):
    bot.send_message(call.message.chat.id, "📝 **Введите текст рассылки** (о новинках/обновлениях):")
    set_state(call.message.chat.id, 'admin_broadcast')
    bot.answer_callback_query(call.id)

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'admin_broadcast')
def adm_broadcast_send(message):
    text = message.text
    bot.send_message(message.chat.id, "⏳ Начинаю рассылку...")
    with sqlite3.connect(DB_NAME) as db:
        users = db.execute("SELECT user_id FROM users").fetchall()
    count = 0
    for user in users:
        try:
            bot.send_message(user[0], f"🔔 **НОВОСТИ БОТА**\n\n{text}", parse_mode="Markdown")
            count += 1
            time.sleep(0.1) 
        except: pass
    bot.send_message(message.chat.id, f"✅ Рассылка завершена. Доставлено: {count} пользователям.", reply_markup=main_menu(message.chat.id))
    clear_state(message.chat.id)

# ===========================
# 4. КАЛЬКУЛЯТОРЫ (ИСПРАВЛЕНЫ)
# ===========================
@bot.message_handler(func=lambda m: m.text == "🧮 Калькулятор")
def calc_start(message):
    clear_state(message.chat.id)
    bot.send_message(message.chat.id, "Что отдаем?", reply_markup=tickers_kb("c1"))
    set_state(message.chat.id, 'calc_1')

@bot.callback_query_handler(func=lambda c: c.data.startswith('c1_'))
def calc_2(call):
    update_data(call.message.chat.id, 'c1', call.data.split('_')[1])
    bot.edit_message_text("Что получаем?", call.message.chat.id, call.message.message_id, reply_markup=tickers_kb("c2"))
    set_state(call.message.chat.id, 'calc_2')

@bot.callback_query_handler(func=lambda c: c.data.startswith('c2_'))
def calc_3(call):
    update_data(call.message.chat.id, 'c2', call.data.split('_')[1])
    bot.edit_message_text("Сумма?", call.message.chat.id, call.message.message_id)
    set_state(call.message.chat.id, 'calc_amt')

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'calc_amt')
def calc_4(message):
    val = safe_float(message.text)
    if val is None: return bot.send_message(message.chat.id, "Введите число!")
    update_data(message.chat.id, 'amt', val)
    bot.send_message(message.chat.id, "Комиссия % (например 0.5):")
    set_state(message.chat.id, 'calc_fee')

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'calc_fee')
def calc_5(message):
    fee = safe_float(message.text)
    if fee is None: return bot.send_message(message.chat.id, "Введите число!")
    
    d = user_states[message.chat.id]['data']
    # КОНВЕРТАЦИЯ
    result = convert_currency(d['amt'], d['c1'], d['c2'])
    
    # ВЫЧИТАЕМ КОМИССИЮ (ИЗ РЕЗУЛЬТАТА)
    final = result * (1 - fee/100)
    
    bot.send_message(message.chat.id, f"✅ Итог: **{final:,.2f} {d['c2']}**", parse_mode="Markdown")
    clear_state(message.chat.id)

# ТРОЙНОЙ ОБМЕН
@bot.message_handler(func=lambda m: m.text == "🔀 Тройной Обмен")
def tr_start(message):
    clear_state(message.chat.id)
    bot.send_message(message.chat.id, "1. Старт:", reply_markup=tickers_kb("t1"))
    set_state(message.chat.id, 'tr_1')

@bot.callback_query_handler(func=lambda c: c.data.startswith('t1_'))
def tr_2(call):
    update_data(call.message.chat.id, 't1', call.data.split('_')[1])
    bot.edit_message_text("2. Центр:", call.message.chat.id, call.message.message_id, reply_markup=tickers_kb("t2"))

@bot.callback_query_handler(func=lambda c: c.data.startswith('t2_'))
def tr_3(call):
    update_data(call.message.chat.id, 't2', call.data.split('_')[1])
    bot.edit_message_text("3. Финиш:", call.message.chat.id, call.message.message_id, reply_markup=tickers_kb("t3"))

@bot.callback_query_handler(func=lambda c: c.data.startswith('t3_'))
def tr_4(call):
    update_data(call.message.chat.id, 't3', call.data.split('_')[1])
    bot.edit_message_text("Сумма:", call.message.chat.id, call.message.message_id)
    set_state(call.message.chat.id, 'tr_amt')

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'tr_amt')
def tr_5(message):
    val = safe_float(message.text)
    if val is None: return bot.send_message(message.chat.id, "Число!")
    update_data(message.chat.id, 'amt', val)
    bot.send_message(message.chat.id, "Комиссия на каждом шаге (%):")
    set_state(message.chat.id, 'tr_fee')

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'tr_fee')
def tr_6(message):
    fee = safe_float(message.text)
    if fee is None: return bot.send_message(message.chat.id, "Число!")
    fee_factor = 1 - fee/100
    
    d = user_states[message.chat.id]['data']
    
    # 1. Валюта А -> Валюта Б (с комиссией)
    step1 = convert_currency(d['amt'], d['t1'], d['t2']) * fee_factor
    
    # 2. Валюта Б -> Валюта В (с комиссией)
    step2 = convert_currency(step1, d['t2'], d['t3']) * fee_factor
    
    text = (f"🔄 **Арбитраж:**\n"
            f"1. {d['amt']} {d['t1']} ➡️ {step1:,.2f} {d['t2']}\n"
            f"2. {step1:,.2f} {d['t2']} ➡️ {step2:,.2f} {d['t3']}\n\n"
            f"💰 **Итог:** **{step2:,.2f} {d['t3']}**")
            
    bot.send_message(message.chat.id, text, parse_mode="Markdown")
    clear_state(message.chat.id)

# ГРАФИКИ
@bot.message_handler(func=lambda m: m.text == "📈 Графики")
def charts(message):
    bot.send_message(message.chat.id, "Валюта:", reply_markup=tickers_kb("g"))

@bot.callback_query_handler(func=lambda c: c.data.startswith('g_'))
def chart_p(call):
    t = call.data.split('_')[1]
    # Используем Yahoo код для графика, если он отличается
    yf_ticker = f"{t}=X" if t in ['RUB','KGS','CNY','EUR'] else f"{t}-USD"
    if t == 'USDT': yf_ticker = 'USDT-USD'
    
    # Кэшируем тикер для следующего шага
    update_data(call.message.chat.id, 'chart_t', yf_ticker)
    
    m = types.InlineKeyboardMarkup(row_width=2)
    m.add(types.InlineKeyboardButton("30д", callback_data=f"gp_{t}_30d"),
          types.InlineKeyboardButton("7д", callback_data=f"gp_{t}_7d"),
          types.InlineKeyboardButton("1д", callback_data=f"gp_{t}_1d"),
          types.InlineKeyboardButton("⭐ В Избранное", callback_data=f"fav_{t}"))
    bot.edit_message_text(f"Период для {t}:", call.message.chat.id, call.message.message_id, reply_markup=m)

@bot.callback_query_handler(func=lambda c: c.data.startswith('gp_'))
def chart_draw(call):
    _, t, p = call.data.split('_')
    bot.answer_callback_query(call.id, "Рисую...")
    
    # Получаем правильный тикер для YF
    user_d = user_states.get(call.message.chat.id, {}).get('data', {})
    yf_t = user_d.get('chart_t', f"{t}-USD")
    
    per, inter = ('1mo', '1d') if p == '30d' else (('5d', '60m') if p == '7d' else ('1d', '30m'))
    try:
        d = yf.Ticker(yf_t).history(period=per, interval=inter)
        plt.figure()
        plt.plot(d.index, d['Close'])
        plt.title(f"{t} ({p})")
        plt.grid(True)
        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        bot.send_photo(call.message.chat.id, buf)
        plt.close()
    except Exception as e: 
        print(e)
        bot.send_message(call.message.chat.id, "График недоступен для этой пары.")

@bot.callback_query_handler(func=lambda c: c.data.startswith('fav_'))
def fav_add(call):
    t = call.data.split('_')[1]
    with sqlite3.connect(DB_NAME) as db:
        db.execute("INSERT OR IGNORE INTO watchlist VALUES (?, ?)", (call.message.chat.id, t))
        db.commit()
    bot.answer_callback_query(call.id, "Добавлено!")

@bot.message_handler(func=lambda m: m.text == "⭐ Мой список")
def watchlist(message):
    with sqlite3.connect(DB_NAME) as db:
        wl = db.execute("SELECT ticker FROM watchlist WHERE user_id = ?", (message.chat.id,)).fetchall()
    if not wl: return bot.send_message(message.chat.id, "Пусто.")
    t = "⭐ **Курсы (USD):**\n"
    update_rates()
    for row in wl:
        cur = row[0]
        # Для крипты - цена, для фиата - курс
        if cur in ['BTC','ETH','TON']: rate = rates_cache.get(f"{cur}_PRICE", 0)
        else: rate = rates_cache.get(cur, 0)
        
        t += f"{cur}: {rate:.4f}\n"
    bot.send_message(message.chat.id, t, parse_mode="Markdown")

# AI
@bot.message_handler(func=lambda m: m.text == "💬 AI Советник")
def ai_menu(message):
    m = types.ReplyKeyboardMarkup(resize_keyboard=True)
    m.add("Что купить?", "Что продать?", "🔙 Назад")
    bot.send_message(message.chat.id, "Спрашивай:", reply_markup=m)
    set_state(message.chat.id, 'ai_chat')

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'ai_chat')
def ai_logic(message):
    if message.text == "🔙 Назад":
        clear_state(message.chat.id)
        return bot.send_message(message.chat.id, "Меню:", reply_markup=main_menu(message.chat.id))
    
    if "купить" in message.text.lower() or "продать" in message.text.lower():
        bot.send_message(message.chat.id, "⏳ Анализирую RSI...")
        best, rsi = "USDT", 50
        for name, code in CURRENCIES.items():
            if code in ['USD', 'USDT']: continue # Не анализируем стейблы
            try:
                # Для анализа все равно нужен yfinance (история)
                yf_code = f"{code}-USD" if code not in ['RUB','KGS','CNY'] else f"{code}=X"
                d = yf.Ticker(yf_code).history(period='1mo')
                if len(d) > 14:
                    delta = d['Close'].diff()
                    u, d_ = delta.clip(lower=0), -1*delta.clip(upper=0)
                    rs = u.ewm(com=13, adjust=False).mean() / d_.ewm(com=13, adjust=False).mean()
                    val = 100 - (100/(1+rs)).iloc[-1]
                    if message.text == "Что купить?" and val < 40: best, rsi = name, val; break
                    if message.text == "Что продать?" and val > 60: best, rsi = name, val; break
            except: continue
        bot.send_message(message.chat.id, f"Совет: {best} (RSI: {rsi:.1f})")
    else:
        bot.send_message(message.chat.id, "Я понимаю кнопки.")

# ФОНОВЫЕ ЗАДАЧИ
def run_bg():
    while True:
        schedule.run_pending()
        time.sleep(1)
threading.Thread(target=run_bg, daemon=True).start()

if __name__ == '__main__':
    bot.infinity_polling()
