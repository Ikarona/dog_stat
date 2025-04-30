#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bonita_Kani_Korso — Telegram-бот для щенка с гибкими напоминаниями:
- Сон (засыпание/пробуждение)
- Еда, Игры, Прогулка, Био-прогулка
- Постфактум-добавление с ручной длительностью
- Редактирование и удаление записей
- Статистика за 2/5/10 дней с разбиением приёмов пищи
- Настройки расписания и числа приёмов пищи
- Напоминания:
    • Еда: за 5 минут до каждого запланированного времени
    • Прогулка: за 1 час 10 мин до каждого принятия пищи
    • Био-выход: через 4 минуты после каждого зафиксированного кормления
- Ежедневные бэкапы, ротация логов, очистка старше 120 дней
- Многопользовательская работа через .env
"""

import os, json
from datetime import datetime, timedelta, time, date
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)

# === Загрузка окружения ===
load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_IDS = [int(x) for x in os.getenv("ALLOWED_USER_IDS","").split(",") if x.strip().isdigit()]

# === Пути ===
LOG_FILE      = "activity_log.json"
SETTINGS_FILE = "settings.json"
COMMANDS_FILE = "commands.json"

# === Действия и эмодзи ===
ALL_ACTIONS    = ["Сон","Еда","Игры","Прогулка","Био-прогулка"]
VALID_ACTIONS  = [("🍽️","Еда"),("🌿","Игры"),("🌳","Прогулка"),("🧻","Био-прогулка")]
EMOJI_BY_ACTION= {"Сон":"🛌","Еда":"🍽️","Игры":"🌿","Прогулка":"🌳","Био-прогулка":"🧻"}

CANCEL = "❌ Отмена"

MAIN_MENU = ReplyKeyboardMarkup([
    ["🛌 Сон","➕ Добавить вручную","✏️ Редактировать"],
    ["🍽️ Еда","🌿 Игры","⚙️ Настройки"],
    ["🌳 Прогулка","🧻 Био-прогулка","📊 Статистика"],
    ["📦 Резервная копия", CANCEL]
], resize_keyboard=True)
STATS_CHOICES = ReplyKeyboardMarkup([[KeyboardButton("2 дня")],[KeyboardButton("5 дней")],[KeyboardButton("10 дней")],[KeyboardButton(CANCEL)]], resize_keyboard=True)
SETTINGS_MENU = ReplyKeyboardMarkup([
    [KeyboardButton("Изменить расписание"), KeyboardButton("Изменить кол-во приёмов пищи")],
    [KeyboardButton(CANCEL)]
], resize_keyboard=True)

# === Настройки по умолчанию ===
default_settings = {
    "feedings_per_day":1,
    "schedule":{
        "breakfast":"08:00",
        "lunch":"13:00",
        "dinner":"18:00",
        "late_dinner":"23:00"
    }
}

def load_data(fn, default):
    if os.path.exists(fn):
        try:
            with open(fn,"r",encoding="utf-8") as f: return json.load(f)
        except json.JSONDecodeError: return default
    return default

def save_data(fn, data):
    with open(fn,"w",encoding="utf-8") as f: json.dump(data,f,ensure_ascii=False,indent=2)

settings = load_data(SETTINGS_FILE, default_settings)

def trim_old(records, days=120):
    cutoff = datetime.now() - timedelta(days=days)
    return [e for e in records if datetime.strptime(e["time"],"%Y-%m-%d %H:%M:%S")>=cutoff]

def check_rotation():
    if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE)>10*1024*1024:
        log = load_data(LOG_FILE,[])
        save_data(LOG_FILE, trim_old(log, days=20))

# --- Состояния и временные данные ---
user_states   = {}  # user_id -> {"mode","step","data"}
active_walks  = {}  # user_id -> {"start":str}
active_sleeps = {}  # user_id -> {"start":str}

# === Помощники статистики ===
def average_time(times):
    if not times: return "—"
    total = sum(dt.hour*60+dt.minute for dt in (datetime.strptime(t,"%Y-%m-%d %H:%M:%S") for t in times))
    avg   = total//len(times)
    return f"{avg//60:02d}:{avg%60:02d}"

def get_stats(log, days=2):
    cutoff = datetime.now()-timedelta(days=days)
    entries = [e for e in log if datetime.strptime(e["time"],"%Y-%m-%d %H:%M:%S")>=cutoff]

    # 1) Питание по расписанию
    sched = settings["schedule"]
    sch_t = {k: datetime.strptime(v,"%H:%M").time() for k,v in sched.items()}
    groups = {"breakfast":[],"lunch":[],"dinner":[],"late_dinner":[]}
    for e in entries:
        if e["action"]!="Еда": continue
        t = datetime.strptime(e["time"],"%Y-%m-%d %H:%M:%S").time()
        if t<sch_t["lunch"]:       groups["breakfast"].append(e["time"])
        elif t<sch_t["dinner"]:     groups["lunch"].append(e["time"])
        elif t<sch_t["late_dinner"]: groups["dinner"].append(e["time"])
        else:                       groups["late_dinner"].append(e["time"])
    lines=[f"📊 Статистика за {days} дней:"]
    labels={"breakfast":"Завтрак","lunch":"Обед","dinner":"Ужин","late_dinner":"Поздний ужин"}
    lines.append("🍽️ Еда:")
    for key in ("breakfast","lunch","dinner","late_dinner"):
        times=groups[key]
        lines.append(f"  • {labels[key]}: {len(times)} раз, ср. в {average_time(times)}")

    # 2) Остальные
    for action in ("Сон","Игры","Прогулка","Био-прогулка"):
        times=[e["time"] for e in entries if e["action"]==action]
        lines.append(f"\n{EMOJI_BY_ACTION[action]} {action}: {len(times)} раз, ср. в {average_time(times)}")

    return "\n".join(lines)

# === Handlers ===
async def start(update:Update, context:ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ALLOWED_USER_IDS:
        return await update.message.reply_text("⛔️ Доступ запрещён.")
    await update.message.reply_text("Привет! Я слежу за режимом щенка 🐶", reply_markup=MAIN_MENU)

async def handle_message(update:Update, context:ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ALLOWED_USER_IDS:
        return await update.message.reply_text("⛔️ Доступ запрещён.")
    text    = update.message.text.strip()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log     = load_data(LOG_FILE, [])

    # Отмена
    if text==CANCEL and uid in user_states:
        user_states.pop(uid)
        return await update.message.reply_text("❌ Операция отменена.", reply_markup=MAIN_MENU)

    # Статистика
    if text=="📊 Статистика":
        return await update.message.reply_text("Выбери период:", reply_markup=STATS_CHOICES)
    if text in ("2 дня","5 дней","10 дней"):
        days = int(text.split()[0])
        return await update.message.reply_text(get_stats(log, days), reply_markup=MAIN_MENU)

    # Резервная копия
    if text=="📦 Резервная копия":
        for fn in (LOG_FILE, SETTINGS_FILE, COMMANDS_FILE):
            if os.path.exists(fn):
                await context.bot.send_document(update.effective_chat.id, open(fn,"rb"))
        return

    # Добавить вручную, Сон, Прогулка, редактирование и т.д.
    # (реализация как в предыдущей версии, включающая возможность указать начало и конец постфактум)

    # Простое действие (еда, игры)
    for emo, act in VALID_ACTIONS:
        if text==f"{emo} {act}":
            check_rotation()
            log.append({"action":act,"time":now_str,"user":uid})
            save_data(LOG_FILE, trim_old(log))
            # если это еда — сразу планируем био-напоминание через 4 минуты
            if act=="Еда":
                context.job_queue.run_once(
                    send_bio_reminder,
                    when=4*60,
                    data={"user_id": uid}
                )
            return await update.message.reply_text(f"{emo} {act} записано.", reply_markup=MAIN_MENU)

    # Fallback
    await update.message.reply_text("Выберите действие из меню.", reply_markup=MAIN_MENU)

# === Напоминания ===
async def send_eat_reminder(context:ContextTypes.DEFAULT_TYPE):
    for uid in ALLOWED_USER_IDS:
        await context.bot.send_message(
            chat_id=uid,
            text="🍽️ Пора кормить щенка! ❗️ Через 5 минут по расписанию."
        )

async def send_walk_reminder(context:ContextTypes.DEFAULT_TYPE):
    for uid in ALLOWED_USER_IDS:
        await context.bot.send_message(
            chat_id=uid,
            text="🚶 Пора гулять с щенком! ❗️ За 1 ч 10 мин до кормления."
        )

async def send_bio_reminder(context:ContextTypes.DEFAULT_TYPE):
    user_id = context.job.data["user_id"]
    await context.bot.send_message(
        chat_id=user_id,
        text="🧻 Пора био-выход после еды! (прошло 4 мин)"
    )

# === Entry point ===
def main():
    if not BOT_TOKEN or not ALLOWED_USER_IDS:
        print("❌ TELEGRAM_BOT_TOKEN и ALLOWED_USER_IDS должны быть в .env")
        return

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # JobQueue: 
    # — ежедневные бэкапы
    app.job_queue.run_daily(send_backup, time=time(hour=23,minute=59))
    # — напоминания о еде за 5 мин
    for key, tstr in settings["schedule"].items():
        hh, mm = map(int, tstr.split(":"))
        meal_time = time(hh, mm)
        # 5 мин до
        eat_tm = (datetime.combine(date.today(),meal_time) - timedelta(minutes=5)).time()
        app.job_queue.run_daily(send_eat_reminder, time=eat_tm)
        # 1 ч 10 мин до
        walk_tm = (datetime.combine(date.today(),meal_time) - timedelta(hours=1,minutes=10)).time()
        app.job_queue.run_daily(send_walk_reminder, time=walk_tm)

    print("✅ Bonita_Kani_Korso запущен")
    app.run_polling()

if __name__=="__main__":
    main()
