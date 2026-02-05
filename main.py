import telebot
from telebot import types
import yfinance as yf
import matplotlib
matplotlib.use('Agg') # Фикс для сервера
import matplotlib.pyplot as plt
import io
import threading
import time
import schedule
import sqlite3
import pandas as pd
import numpy as np
import random
import os  # <--- Добавил для работы с Railway
from datetime import datetime

# --- КОНФИГУРАЦИЯ ---
# Бот сначала ищет токен в настройках Railway. Если там пусто, берет этот жесткий токен:
BOT_TOKEN = os.getenv('BOT_TOKEN', '8212929038:AAFdctXociA1FcnaxKW7N0wbfc6SdFbJ1v0')
MAIN_ADMIN = 'Slavyanln' 

bot = telebot.TeleBot(BOT_TOKEN)

# --- ВАЛЮТЫ ---
TICKERS = {
    '💵 USDT': 'USDT-USD', '🇺🇸 USD': 'DX-Y.NYB', '₿ BTC': 'BTC-USD',
    '💎 ETH': 'ETH-USD', '💎 TON': 'TON11419-USD', '🇪🇺 EUR': 'EURUSD=X',
    '🇷🇺 RUB': 'RUB=X', '🇰🇬 KGS': 'KGS=X', '🇨🇳 CNY': 'CNY=X',
    '🇦🇪 AED': 'AED=X', '🇹🇯 TJS': 'TJS=X', '🇺🇿 UZS': 'UZS=X'
}
REVERSE_PAIRS = ['RUB=X', 'KGS=X', 'CNY=X', 'AED=X', 'TJS=X', 'UZS=X']

# --- СОСТОЯНИЯ И ПАМЯТЬ ---
DB_NAME = "bot_data.db"
user_states = {} 

# --- БАЗА ДАННЫХ ---
def init_db():
    with sqlite3.connect(DB_NAME) as db:
        # Таблица пользователей
        db.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            role TEXT DEFAULT 'executor'
        )''')
        # Проекты
        db.execute('''CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            type TEXT,
            limit_exp REAL,
            active INTEGER DEFAULT 1
        )''')
        # Отчеты
        db.execute('''CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            project_id INTEGER,
            turnover REAL,
            expenses REAL,
            profit REAL,
            roi REAL,
            date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        # Избранное
        db.execute('''CREATE TABLE IF NOT EXISTS watchlist (
            user_id INTEGER,
            ticker TEXT,
            UNIQUE(user_id, ticker)
        )''')
        db.commit()

init_db()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def get_user_role(uid):
    with sqlite3.connect(DB_NAME) as db:
        res = db.execute("SELECT role FROM users WHERE user_id = ?", (uid,)).fetchone()
        return res[0] if res else 'executor'

def set_state(uid, step, data=None):
    if uid not in user_states: user_states[uid] = {}
    user_states[uid]['step'] = step
    if data: user_states[uid]['data'] = data

def update_data(uid, key, value):
    if uid in user_states:
        if 'data' not in user_states[uid]: user_states[uid]['data'] = {}
        user_states[uid]['data'][key] = value

def clear_state(uid):
    if uid in user_states: del user_states[uid]

def get_price(ticker):
    try:
        d = yf.Ticker(ticker)
        return d.history(period='2d')['Close'].iloc[-1]
    except: return None

def convert(amount, ticker, price, to_usd=True):
    if ticker in REVERSE_PAIRS:
        return amount / price if to_usd else amount * price
    return amount * price if to_usd else amount / price

# --- КЛАВИАТУРЫ ---
def main_menu(uid):
    role = get_user_role(uid)
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add("🧮 Калькулятор", "🔀 Тройной Обмен")
    markup.add("📈 Графики", "⭐ Мой список")
    markup.add("💬 AI Советник", "➕ Отчет (Проекты)")
    if role == 'admin': markup.add("⚙️ Админка Проектов")
    return markup

def tickers_kb(prefix):
    markup = types.InlineKeyboardMarkup(row_width=2)
    btns = []
    for name, t in TICKERS.items():
        btns.append(types.InlineKeyboardButton(name, callback_data=f"{prefix}_{t}"))
    markup.add(*btns)
    return markup

# --- СТАРТ ---
@bot.message_handler(commands=['start'])
def start(message):
    uid = message.chat.id
    uname = message.from_user.username
    role = 'admin' if uname == MAIN_ADMIN else 'executor'
    
    with sqlite3.connect(DB_NAME) as db:
        db.execute("INSERT OR IGNORE INTO users (user_id, username, role) VALUES (?, ?, ?)", (uid, uname, role))
        if uname == MAIN_ADMIN:
            db.execute("UPDATE users SET role = 'admin' WHERE user_id = ?", (uid,))
        db.commit()
    
    bot.send_message(uid, f"Привет! Я готов. Твоя роль: {role}", reply_markup=main_menu(uid))

# --- АДМИН КОНСОЛЬ (/admin) ---
@bot.message_handler(commands=['admin'])
def admin_cmd(message):
    if message.from_user.username != MAIN_ADMIN: return
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("👥 Пользователи", callback_data="adm_users"))
    bot.send_message(message.chat.id, "🔒 Админ Панель", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "adm_users")
def adm_users(call):
    with sqlite3.connect(DB_NAME) as db:
        users = db.execute("SELECT username, role FROM users").fetchall()
    text = "👥 Список:\n" + "\n".join([f"@{u[0]} - {u[1]}" for u in users])
    bot.send_message(call.message.chat.id, text[:4000])

# ===========================
# 1. СОЗДАНИЕ ПРОЕКТОВ (АДМИН)
# ===========================
@bot.message_handler(func=lambda m: m.text == "⚙️ Админка Проектов")
def proj_start(message):
    if get_user_role(message.chat.id) != 'admin': return
    bot.send_message(message.chat.id, "Введите название проекта:", reply_markup=types.ReplyKeyboardRemove())
    set_state(message.chat.id, 'proj_name')

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'proj_name')
def proj_name(message):
    update_data(message.chat.id, 'name', message.text)
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("карта", "сим", "проект", "другое")
    bot.send_message(message.chat.id, "Тип проекта:", reply_markup=markup)
    set_state(message.chat.id, 'proj_type')

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'proj_type')
def proj_type(message):
    update_data(message.chat.id, 'type', message.text)
    bot.send_message(message.chat.id, "Лимит расходов (число):", reply_markup=types.ReplyKeyboardRemove())
    set_state(message.chat.id, 'proj_limit')

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'proj_limit')
def proj_finish(message):
    try:
        limit = float(message.text)
        data = user_states[message.chat.id]['data']
        with sqlite3.connect(DB_NAME) as db:
            db.execute("INSERT INTO projects (name, type, limit_exp) VALUES (?, ?, ?)", 
                       (data['name'], data['type'], limit))
            db.commit()
        bot.send_message(message.chat.id, f"✅ Проект '{data['name']}' создан!", reply_markup=main_menu(message.chat.id))
        clear_state(message.chat.id)
    except: bot.send_message(message.chat.id, "Нужно число!")

# ===========================
# 2. ОТЧЕТЫ (ИСПОЛНИТЕЛИ)
# ===========================
@bot.message_handler(func=lambda m: m.text == "➕ Отчет (Проекты)")
def rep_start(message):
    with sqlite3.connect(DB_NAME) as db:
        projs = db.execute("SELECT id, name FROM projects WHERE active=1").fetchall()
    
    if not projs: return bot.send_message(message.chat.id, "Нет проектов.")
    
    markup = types.InlineKeyboardMarkup()
    for p in projs: markup.add(types.InlineKeyboardButton(p[1], callback_data=f"rep_p_{p[0]}"))
    bot.send_message(message.chat.id, "Выберите проект:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('rep_p_'))
def rep_sel(call):
    pid = int(call.data.split('_')[2])
    set_state(call.message.chat.id, 'rep_turn', {'pid': pid})
    bot.edit_message_text("💰 Введите ОБОРОТ (число):", call.message.chat.id, call.message.message_id)

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'rep_turn')
def rep_turn(message):
    try:
        val = float(message.text)
        update_data(message.chat.id, 'turn', val)
        bot.send_message(message.chat.id, "💸 Введите РАСХОДЫ (число):")
        set_state(message.chat.id, 'rep_exp')
    except: bot.send_message(message.chat.id, "Число!")

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'rep_exp')
def rep_fin(message):
    try:
        exp = float(message.text)
        d = user_states[message.chat.id]['data']
        profit = d['turn'] - exp
        roi = (profit / exp * 100) if exp > 0 else 0
        
        with sqlite3.connect(DB_NAME) as db:
            db.execute("INSERT INTO reports (user_id, project_id, turnover, expenses, profit, roi) VALUES (?,?,?,?,?,?)",
                       (message.chat.id, d['pid'], d['turn'], exp, profit, roi))
            db.commit()
            
        bot.send_message(message.chat.id, f"✅ Отчет принят!\nПрибыль: {profit:,.2f}", reply_markup=main_menu(message.chat.id))
        clear_state(message.chat.id)
    except: bot.send_message(message.chat.id, "Число!")

# ===========================
# 3. КАЛЬКУЛЯТОРЫ
# ===========================
@bot.message_handler(func=lambda m: m.text == "🧮 Калькулятор")
def calc_start(message):
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
    try:
        update_data(message.chat.id, 'amt', float(message.text))
        bot.send_message(message.chat.id, "Комиссия %:")
        set_state(message.chat.id, 'calc_fee')
    except: pass

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'calc_fee')
def calc_5(message):
    try:
        fee = float(message.text)
        d = user_states[message.chat.id]['data']
        p1, p2 = get_price(d['c1']), get_price(d['c2'])
        if p1 and p2:
            u = convert(d['amt'], d['c1'], p1, True)
            f = convert(u*(1-fee/100), d['c2'], p2, False)
            bot.send_message(message.chat.id, f"Итог: {f:,.2f}")
        clear_state(message.chat.id)
    except: pass

# ТРОЙНОЙ ОБМЕН
@bot.message_handler(func=lambda m: m.text == "🔀 Тройной Обмен")
def tr_start(message):
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
    try:
        update_data(message.chat.id, 'amt', float(message.text))
        bot.send_message(message.chat.id, "Комиссия %:")
        set_state(message.chat.id, 'tr_fee')
    except: pass

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'tr_fee')
def tr_6(message):
    try:
        fee = float(message.text)/100
        d = user_states[message.chat.id]['data']
        p1, p2, p3 = get_price(d['t1']), get_price(d['t2']), get_price(d['t3'])
        if p1 and p2 and p3:
            u1 = convert(d['amt'], d['t1'], p1, True)
            u2 = convert(convert(u1*(1-fee), d['t2'], p2, False), d['t2'], p2, True)
            fin = convert(u2*(1-fee), d['t3'], p3, False)
            bot.send_message(message.chat.id, f"Итог: {fin:,.2f}")
        clear_state(message.chat.id)
    except: pass

# ===========================
# 4. ГРАФИКИ
# ===========================
@bot.message_handler(func=lambda m: m.text == "📈 Графики")
def charts(message):
    bot.send_message(message.chat.id, "Валюта:", reply_markup=tickers_kb("g"))

@bot.callback_query_handler(func=lambda c: c.data.startswith('g_'))
def chart_p(call):
    t = call.data.split('_')[1]
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
    per, inter = ('1mo', '1d') if p == '30d' else (('5d', '60m') if p == '7d' else ('1d', '30m'))
    try:
        d = yf.Ticker(t).history(period=per, interval=inter)
        plt.figure()
        plt.plot(d.index, d['Close'])
        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        bot.send_photo(call.message.chat.id, buf)
        plt.close()
    except: pass

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
    t = "⭐ Курсы:\n"
    for row in wl:
        p = get_price(row[0])
        t += f"{row[0]}: {p:.4f}\n" if p else f"{row[0]}: Err\n"
    bot.send_message(message.chat.id, t)

# ===========================
# 5. AI
# ===========================
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
        for n, t in TICKERS.items():
            try:
                d = yf.Ticker(t).history(period='1mo')
                if len(d) > 14:
                    delta = d['Close'].diff()
                    u, d = delta.clip(lower=0), -1*delta.clip(upper=0)
                    rs = u.ewm(com=13, adjust=False).mean() / d.ewm(com=13, adjust=False).mean()
                    val = 100 - (100/(1+rs)).iloc[-1]
                    if message.text == "Что купить?" and val < 40: best, rsi = n, val; break
                    if message.text == "Что продать?" and val > 60: best, rsi = n, val; break
            except: continue
        
        bot.send_message(message.chat.id, f"Совет: {best} (RSI: {rsi:.1f})")
    else:
        bot.send_message(message.chat.id, "Я понимаю кнопки.")

# ФОНОВЫЕ ЗАДАЧИ
def job():
    with sqlite3.connect(DB_NAME) as db:
        users = db.execute("SELECT user_id FROM users").fetchall()
        # тут можно добавить проверку цен
    pass
schedule.every(10).minutes.do(job)

def run_bg():
    while True:
        schedule.run_pending()
        time.sleep(1)
threading.Thread(target=run_bg, daemon=True).start()

if __name__ == '__main__':
    bot.infinity_polling()
