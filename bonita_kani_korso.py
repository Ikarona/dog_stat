import os
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

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
    ("🧻", "Био-прогулка")
]
EMOJI_BY_ACTION = {action: emoji for emoji, action in VALID_ACTIONS}

# === Интерфейс ===
MAIN_MENU = ReplyKeyboardMarkup(
    [[f"{emoji} {action}" for emoji, action in VALID_ACTIONS[i:i+2]] for i in range(0, len(VALID_ACTIONS), 2)],
    resize_keyboard=True
)

# === Работа с файлами ===
def load_data(filename, default):
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            print(f"[⚠] Предупреждение: повреждён файл {filename}, он будет перезаписан.")
            return default
    return default

def save_data(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def trim_old_records(log, keep_days=120):
    cutoff = datetime.now() - timedelta(days=keep_days)
    return [
        entry for entry in log
        if datetime.strptime(entry['time'], "%Y-%m-%d %H:%M:%S") >= cutoff
    ]

def check_log_rotation():
    max_size = 10 * 1024 * 1024  # 10 MB
    if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > max_size:
        log = load_data(LOG_FILE, [])
        trimmed = trim_old_records(log, keep_days=20)
        save_data(LOG_FILE, trimmed)

# === Хендлеры ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ALLOWED_USER_IDS:
        return await update.message.reply_text("⛔️ У вас нет доступа.")
    await update.message.reply_text(
        "Привет! Я помогу следить за режимом щенка.", reply_markup=MAIN_MENU
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USER_IDS:
        return await update.message.reply_text("⛔️ У вас нет доступа.")

    text = update.message.text.strip()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for emoji, action in VALID_ACTIONS:
        if text.endswith(action):
            check_log_rotation()
            log = load_data(LOG_FILE, [])
            log = trim_old_records(log)
            log.append({
                "action": action,
                "time": now,
                "user": user_id
            })
            save_data(LOG_FILE, log)
            await update.message.reply_text(f"✅ Записал: {emoji} {action} в {now}")
            return

    await update.message.reply_text("Выбери действие из меню.", reply_markup=MAIN_MENU)

# === Запуск ===
def main():
    if not BOT_TOKEN or not ALLOWED_USER_IDS:
        print("❌ Ошибка: переменные TELEGRAM_BOT_TOKEN и ALLOWED_USER_IDS должны быть заданы в .env")
        return

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == "__main__":
    main()