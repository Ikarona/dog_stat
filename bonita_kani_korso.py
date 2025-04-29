#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
from datetime import datetime, timedelta, time
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# === Load environment ===
load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_IDS = [
    int(uid) for uid in os.getenv("ALLOWED_USER_IDS", "").split(",")
    if uid.strip().isdigit()
]

# === File paths ===
LOG_FILE = "activity_log.json"
SETTINGS_FILE = "settings.json"
COMMANDS_FILE = "commands.json"

# === Actions and emojis ===
ALL_ACTIONS = ["Сон", "Еда", "Игры", "Прогулка", "Био-прогулка"]
VALID_ACTIONS = [
    ("🍽️", "Еда"),
    ("🌿", "Игры"),
    ("🌳", "Прогулка"),
    ("🧻", "Био-прогулка"),
]
EMOJI_BY_ACTION = {
    "Сон": "🛌",
    "Еда": "🍽️",
    "Игры": "🌿",
    "Прогулка": "🌳",
    "Био-прогулка": "🧻",
}

# === Keyboards ===
MAIN_MENU = ReplyKeyboardMarkup([
    ["🛌 Сон", "➕ Добавить вручную"],
    ["🍽️ Еда", "🌿 Игры"],
    ["🌳 Прогулка", "🧻 Био-прогулка"],
    ["📊 Статистика", "📦 Резервная копия"],
], resize_keyboard=True)

STATS_CHOICES = ReplyKeyboardMarkup([
    [KeyboardButton("2 дня")],
    [KeyboardButton("5 дней")],
    [KeyboardButton("10 дней")],
], resize_keyboard=True)


# === Storage helpers ===
def load_data(filename, default):
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            print(f"[⚠] Corrupted {filename}, resetting.")
            return default
    return default

def save_data(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def trim_old_records(records, keep_days=120):
    cutoff = datetime.now() - timedelta(days=keep_days)
    return [
        e for e in records
        if datetime.strptime(e["time"], "%Y-%m-%d %H:%M:%S") >= cutoff
    ]

def check_log_rotation():
    max_size = 10 * 1024 * 1024  # 10 MB
    if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > max_size:
        log = load_data(LOG_FILE, [])
        trimmed = trim_old_records(log, keep_days=20)
        save_data(LOG_FILE, trimmed)


# === In-memory state ===
user_states = {}    # user_id -> {"mode":"postfact","step":int,"data":{}}
active_walks = {}   # user_id -> {"start": "YYYY-MM-DD HH:MM:SS"}
active_sleeps = {}  # user_id -> {"start": "YYYY-MM-DD HH:MM:SS"}


# === Statistics helpers ===
def average_time(times):
    if not times:
        return "—"
    total = sum(
        dt.hour * 60 + dt.minute
        for dt in (datetime.strptime(t, "%Y-%m-%d %H:%M:%S") for t in times)
    )
    avg = total // len(times)
    return f"{avg // 60:02d}:{avg % 60:02d}"

def get_stats(log, days=2):
    cutoff = datetime.now() - timedelta(days=days)
    filtered = [
        e for e in log
        if datetime.strptime(e["time"], "%Y-%m-%d %H:%M:%S") >= cutoff
    ]
    stats = {}
    for e in filtered:
        stats.setdefault(e["action"], []).append(e["time"])
    text = f"📊 Статистика за {days} дней:\n"
    for action, times in stats.items():
        text += (
            f"{EMOJI_BY_ACTION.get(action,'')} {action}: "
            f"{len(times)} раз(а), в среднем в {average_time(times)}\n"
        )
    return text

def get_average_time(log, action, days=5):
    cutoff = datetime.now() - timedelta(days=days)
    times = [
        datetime.strptime(e["time"], "%Y-%m-%d %H:%M:%S")
        for e in log
        if e["action"] == action
        and datetime.strptime(e["time"], "%Y-%m-%d %H:%M:%S") >= cutoff
    ]
    if not times:
        return None
    minutes = sum(t.hour * 60 + t.minute for t in times) // len(times)
    return (minutes // 60, minutes % 60)


# === Handlers ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ALLOWED_USER_IDS:
        return await update.message.reply_text("⛔️ У вас нет доступа.")
    await update.message.reply_text(
        "Привет! Я слежу за режимом щенка 🐶", reply_markup=MAIN_MENU
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USER_IDS:
        return await update.message.reply_text("⛔️ У вас нет доступа.")

    text = update.message.text.strip()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log = load_data(LOG_FILE, [])

    # --- Статистика ---
    if text == "📊 Статистика":
        return await update.message.reply_text("Выбери период:", reply_markup=STATS_CHOICES)
    if text in ("2 дня", "5 дней", "10 дней"):
        days = int(text.split()[0])
        return await update.message.reply_text(get_stats(log, days), reply_markup=MAIN_MENU)

    # --- Резервная копия ---
    if text == "📦 Резервная копия":
        for fn in (LOG_FILE, SETTINGS_FILE, COMMANDS_FILE):
            if os.path.exists(fn):
                await context.bot.send_document(chat_id=update.effective_chat.id,
                                                document=open(fn, "rb"))
        return

    # --- Постфактум-добавление ---
    if user_id in user_states:
        state = user_states[user_id]
        action = state["data"].get("action")
        # Шаг 1: получили действие
        if state["step"] == 0:
            if text not in ALL_ACTIONS:
                user_states.pop(user_id)
                return await update.message.reply_text("Неверный выбор.", reply_markup=MAIN_MENU)
            state["data"]["action"] = text
            state["step"] = 1
            await update.message.reply_text("Введи дату и время: ДД.MM.YYYY ЧЧ:ММ")
            return
        # Шаг 2: получили дату и время
        if state["step"] == 1:
            try:
                dt = datetime.strptime(text, "%d.%m.%Y %H:%M")
            except ValueError:
                return await update.message.reply_text("Неверный формат. Попробуй снова.")
            state["data"]["time"] = dt
            # если прогулка — просим длительность
            if action in ("Прогулка", "Био-прогулка"):
                state["step"] = 2
                return await update.message.reply_text(
                    "Теперь введи длительность в минутах или ЧЧ:ММ"
                )
            # иначе сохраняем сразу
            log.append({
                "action": action,
                "time": dt.strftime("%Y-%m-%d %H:%M:%S"),
                "user": user_id
            })
            save_data(LOG_FILE, trim_old_records(log))
            user_states.pop(user_id)
            return await update.message.reply_text(
                f"✅ Записано: {action} в {dt.strftime('%Y-%m-%d %H:%M:%S')}",
                reply_markup=MAIN_MENU
            )
        # Шаг 3: получили длительность
        if state["step"] == 2:
            duration = text
            # парсим длительность
            if ":" in duration:
                h, m = duration.split(":", 1)
                mins = int(h) * 60 + int(m)
            else:
                mins = int(duration)
            start_dt = state["data"]["time"]
            end_dt = start_dt + timedelta(minutes=mins)
            # записываем две метки
            log.extend([
                {"action": action, "time": start_dt.strftime("%Y-%m-%d %H:%M:%S"),
                 "user": user_id, "note": "start"},
                {"action": action, "time": end_dt.strftime("%Y-%m-%d %H:%M:%S"),
                 "user": user_id, "note": "end"},
            ])
            save_data(LOG_FILE, trim_old_records(log))
            user_states.pop(user_id)
            return await update.message.reply_text(
                f"✅ {action} с {start_dt.strftime('%H:%M')} до {end_dt.strftime('%H:%M')}",
                reply_markup=MAIN_MENU
            )

    # Запуск постфактум
    if text == "➕ Добавить вручную":
        user_states[user_id] = {"mode": "postfact", "step": 0, "data": {}}
        actions_list = "\n".join(f"• {a}" for a in ALL_ACTIONS)
        return await update.message.reply_text(f"Что добавить?\n{actions_list}")

    # --- Сон: начало/пробуждение ---
    if text == "🛌 Сон":
        if user_id in active_sleeps:
            start_str = active_sleeps.pop(user_id)["start"]
            dt0 = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S")
            dt1 = datetime.now()
            duration = dt1 - dt0
            hours, rem = divmod(duration.seconds, 3600)
            mins = rem // 60
            log.extend([
                {"action": "Сон", "time": start_str, "user": user_id, "note": "start"},
                {"action": "Сон", "time": dt1.strftime("%Y-%m-%d %H:%M:%S"),
                 "user": user_id, "note": "end"},
            ])
            save_data(LOG_FILE, trim_old_records(log))
            return await update.message.reply_text(
                f"😴 Пробуждение: сон длился {hours}ч {mins}м", reply_markup=MAIN_MENU
            )
        else:
            active_sleeps[user_id] = {"start": now_str}
            return await update.message.reply_text("😴 Засыпание зарегистрировано.", reply_markup=MAIN_MENU)

    # --- Прогулка: начало/конец ---
    if text in ("🌳 Прогулка", "🧻 Био-прогулка"):
        action = text.split()[1]
        if user_id in active_walks:
            start_str = active_walks.pop(user_id)["start"]
            dt0 = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S")
            dt1 = datetime.now()
            duration = dt1 - dt0
            hours, rem = divmod(duration.seconds, 3600)
            mins = rem // 60
            log.extend([
                {"action": action, "time": start_str, "user": user_id, "note": "start"},
                {"action": action, "time": dt1.strftime("%Y-%m-%d %H:%M:%S"),
                 "user": user_id, "note": "end"},
            ])
            save_data(LOG_FILE, trim_old_records(log))
            return await update.message.reply_text(
                f"🚶 {action} завершена: {hours}ч {mins}м", reply_markup=MAIN_MENU
            )
        else:
            active_walks[user_id] = {"start": now_str}
            return await update.message.reply_text(
                f"🚶 {action} началась.", reply_markup=MAIN_MENU
            )

    # --- Простые действия ---
    for emoji, action in VALID_ACTIONS:
        if text == f"{emoji} {action}":
            check_log_rotation()
            log.append({"action": action, "time": now_str, "user": user_id})
            save_data(LOG_FILE, trim_old_records(log))
            return await update.message.reply_text(
                f"✅ {emoji} {action} в {now_str}", reply_markup=MAIN_MENU
            )

    # --- Fallback ---
    await update.message.reply_text("Выбери действие из меню.", reply_markup=MAIN_MENU)


# === Periodic tasks via JobQueue ===
async def send_backup(context: ContextTypes.DEFAULT_TYPE):
    for uid in ALLOWED_USER_IDS:
        for fn in (LOG_FILE, SETTINGS_FILE, COMMANDS_FILE):
            if os.path.exists(fn):
                await context.bot.send_document(chat_id=uid,
                                                document=open(fn, "rb"),
                                                caption="📦 Ежедневная резервная копия")

async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    log = load_data(LOG_FILE, [])
    today = datetime.now().strftime("%Y-%m-%d")
    for action in ("Еда", "Прогулка"):
        avg = get_average_time(log, action)
        if not avg:
            continue
        h, m = avg
        target = datetime.strptime(f"{today} {h:02d}:{m:02d}", "%Y-%m-%d %H:%M")
        now = datetime.now()
        delta = (now - target).total_seconds() / 60
        if 6 <= delta <= 10 and not any(
            e["action"] == action and e["time"].startswith(today) for e in log
        ):
            text = (
                f"{EMOJI_BY_ACTION[action]} Пора {action.lower()}!\n"
                f"Обычно в {h:02d}:{m:02d}, сейчас {now.strftime('%H:%M')}"
            )
            for uid in ALLOWED_USER_IDS:
                await context.bot.send_message(chat_id=uid, text=text)


# === Entry point ===
def main():
    if not BOT_TOKEN or not ALLOWED_USER_IDS:
        print("❌ TELEGRAM_BOT_TOKEN и ALLOWED_USER_IDS должны быть заданы в .env")
        return

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Schedule jobs
    jq = app.job_queue
    jq.run_daily(send_backup, time=time(hour=23, minute=59))
    jq.run_repeating(check_reminders, interval=300, first=0)

    print("✅ Bonita_Kani_Korso запущен")
    app.run_polling()


if __name__ == "__main__":
    main()
