import os
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# === Загрузка переменных окружения ===
load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_IDS = [int(uid) for uid in os.getenv("ALLOWED_USER_IDS", "").split(",") if uid.strip().isdigit()]

# === Пути к файлам ===
LOG_FILE = "activity_log.json"
SETTINGS_FILE = "settings.json"
COMMANDS_FILE = "commands.json"

# === Валидные действия ===
VALID_ACTIONS = [
    ("🛌", "Сон"),
    ("🍽️", "Еда"),
    ("🌿", "Игры"),
    ("🌳", "Прогулка"),
    ("🧻", "Био-прогулка"),
]

EMOJI_BY_ACTION = {action: emoji for emoji, action in VALID_ACTIONS}

# === Меню ===
MAIN_MENU = ReplyKeyboardMarkup(
    [[f"{emoji} {action}" for emoji, action in VALID_ACTIONS[i:i+2]] for i in range(0, len(VALID_ACTIONS), 2)] +
    [[KeyboardButton("➕ Добавить вручную")], [KeyboardButton("📊 Статистика")], [KeyboardButton("📦 Резервная копия")]],
    resize_keyboard=True
)

STATS_CHOICES = ReplyKeyboardMarkup(
    [[KeyboardButton("2 дня")], [KeyboardButton("5 дней")], [KeyboardButton("10 дней")]],
    resize_keyboard=True
)

# === Хранилище ===
def load_data(filename, default):
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            print(f"[⚠] Повреждён файл {filename}, создаю заново.")
            return default
    return default

def save_data(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def trim_old_records(log, keep_days=120):
    cutoff = datetime.now() - timedelta(days=keep_days)
    return [entry for entry in log if datetime.strptime(entry['time'], "%Y-%m-%d %H:%M:%S") >= cutoff]

def check_log_rotation():
    max_size = 10 * 1024 * 1024  # 10 MB
    if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > max_size:
        log = load_data(LOG_FILE, [])
        trimmed = trim_old_records(log, keep_days=20)
        save_data(LOG_FILE, trimmed)

# === Состояния для отслеживания добавления вручную и прогулок ===
user_states = {}  # user_id: {"mode": "postfact", "step": int, "data": {...}}
active_walks = {}  # user_id: {"start": datetime_str}

# === Усреднённое время по действиям ===
def get_average_time(log, action, days=5):
    cutoff = datetime.now() - timedelta(days=days)
    times = [
        datetime.strptime(entry["time"], "%Y-%m-%d %H:%M:%S")
        for entry in log
        if entry["action"] == action and datetime.strptime(entry["time"], "%Y-%m-%d %H:%M:%S") >= cutoff
    ]
    if not times:
        return None
    total_minutes = sum(t.hour * 60 + t.minute for t in times)
    avg = total_minutes // len(times)
    return avg // 60, avg % 60

# === Хендлеры ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ALLOWED_USER_IDS:
        return await update.message.reply_text("⛔️ У вас нет доступа.")
    await update.message.reply_text("Привет! Я помогу следить за режимом щенка 🐶", reply_markup=MAIN_MENU)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USER_IDS:
        return await update.message.reply_text("⛔️ У вас нет доступа.")
    text = update.message.text.strip()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log = load_data(LOG_FILE, [])

    # === Шаги постфактум ===
    if user_id in user_states:
        state = user_states[user_id]
        if state["mode"] == "postfact":
            if state["step"] == 0:
                state["data"]["action"] = text
                await update.message.reply_text("Теперь введи дату и время в формате ДД.ММ.ГГГГ ЧЧ:ММ")
                state["step"] = 1
            elif state["step"] == 1:
                try:
                    dt = datetime.strptime(text, "%d.%m.%Y %H:%M")
                    state["data"]["time"] = dt.strftime("%Y-%m-%d %H:%M:%S")
                    log.append({
                        "action": state["data"]["action"],
                        "time": state["data"]["time"],
                        "user": user_id
                    })
                    save_data(LOG_FILE, trim_old_records(log))
                    await update.message.reply_text(f"✅ Записал: {state['data']['action']} на {state['data']['time']}")
                except ValueError:
                    await update.message.reply_text("Неверный формат. Попробуй ещё раз: ДД.ММ.ГГГГ ЧЧ:ММ")
                    return
                user_states.pop(user_id)
            return

    # === Постфактум меню ===
    if text == "➕ Добавить вручную":
        actions_list = "\n".join([f"• {a}" for _, a in VALID_ACTIONS])
        user_states[user_id] = {"mode": "postfact", "step": 0, "data": {}}
        await update.message.reply_text(f"Что добавить?\n{actions_list}")
        return

    # === Прогулка: начало и конец ===
    if text.endswith("Прогулка"):
        if user_id in active_walks:
            start_time = active_walks.pop(user_id)["start"]
            end_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log.append({"action": "Прогулка", "time": start_time, "user": user_id, "note": "начало"})
            log.append({"action": "Прогулка", "time": end_time, "user": user_id, "note": "конец"})
            save_data(LOG_FILE, trim_old_records(log))
            await update.message.reply_text(f"🚶 Прогулка завершена. Длительность: рассчитывается по логам.")
        else:
            active_walks[user_id] = {"start": now}
            await update.message.reply_text("🚶 Прогулка началась.")
        return

# === Статистика ===
def get_stats(log, days=2):
    cutoff = datetime.now() - timedelta(days=days)
    filtered = [
        entry for entry in log
        if datetime.strptime(entry['time'], "%Y-%m-%d %H:%M:%S") >= cutoff
    ]
    stats = {}
    for entry in filtered:
        key = entry['action']
        stats.setdefault(key, []).append(entry['time'])

    result = f"📊 Статистика за {days} дней:\n"
    for action, times in stats.items():
        avg = average_time(times)
        result += f"{EMOJI_BY_ACTION.get(action, '')} {action}: {len(times)} раз(а), в среднем в {avg}\n"
    return result

def average_time(times):
    if not times:
        return "—"
    total_minutes = 0
    for t in times:
        dt = datetime.strptime(t, "%Y-%m-%d %H:%M:%S")
        total_minutes += dt.hour * 60 + dt.minute
    avg_minutes = total_minutes // len(times)
    return f"{avg_minutes // 60:02d}:{avg_minutes % 60:02d}"

# === Бэкап ===
async def send_backup(context: ContextTypes.DEFAULT_TYPE):
    for uid in ALLOWED_USER_IDS:
        for file in [LOG_FILE, SETTINGS_FILE, COMMANDS_FILE]:
            if os.path.exists(file):
                await context.bot.send_document(chat_id=uid, document=open(file, "rb"),
                                                caption="📦 Ежедневная резервная копия")

# === Напоминания ===
async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    log = load_data(LOG_FILE, [])
    today = datetime.now().strftime("%Y-%m-%d")
    actions_to_check = ["Еда", "Прогулка"]
    for action in actions_to_check:
        avg = get_average_time(log, action)
        if not avg:
            continue
        avg_hour, avg_minute = avg
        target_time = datetime.strptime(f"{today} {avg_hour:02d}:{avg_minute:02d}", "%Y-%m-%d %H:%M")
        now = datetime.now()
        if 6 <= (now - target_time).total_seconds() / 60 <= 10:
            already_done = any(
                entry['action'] == action and entry['time'].startswith(today)
                for entry in log
            )
            if not already_done:
                for uid in ALLOWED_USER_IDS:
                    await context.bot.send_message(
                        chat_id=uid,
                        text=f"{EMOJI_BY_ACTION.get(action, '')} Пора: {action.lower()}!\n"
                             f"Обычно в {avg_hour:02d}:{avg_minute:02d}, сейчас уже {now.strftime('%H:%M')}"
                    )

# === Обработка сообщений ===
async def handle_stat_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    log = load_data(LOG_FILE, [])
    if text in ["2 дня", "5 дней", "10 дней"]:
        days = int(text.split()[0])
        await update.message.reply_text(get_stats(log, days=days), reply_markup=MAIN_MENU)

# === Запуск ===
def main():
    if not BOT_TOKEN or not ALLOWED_USER_IDS:
        print("❌ Ошибка: переменные TELEGRAM_BOT_TOKEN и ALLOWED_USER_IDS должны быть заданы в .env")
        return

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_stat_request))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Планировщик задач
    try:
        scheduler = AsyncIOScheduler()
    except Exception:
        print("⚠️ Не удалось применить таймзону, используем системное время")
    scheduler = AsyncIOScheduler()
    scheduler.add_job(send_backup, trigger="cron", hour=23, minute=59, args=[app.bot])
    scheduler.add_job(check_reminders, trigger="interval", minutes=5, args=[app.bot])
    scheduler.start()

    print("✅ Bonita_Kani_Korso запущен")
    app.run_polling()

if __name__ == "__main__":
    main()
