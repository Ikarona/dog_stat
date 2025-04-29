import logging
import json
import os
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USERS = list(map(int, os.getenv("ALLOWED_USER_IDS", "").split(",")))

# Константы путей
LOG_FILE = "activity_log.json"
COMMANDS_FILE = "commands.json"
SETTINGS_FILE = "settings.json"

# Включаем логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Главное меню
MAIN_MENU = ReplyKeyboardMarkup([
    ["\ud83c\udf1a Сон", "\ud83c\udf7d\ufe0f Еда"],
    ["\ud83c\udf3f Игры", "\ud83c\udf33 Прогулка"],
    ["\ud83e\uddfb Био-прогулка"],
    ["\ud83d\udcca Статистика"],
    ["\ud83d\udd27 Настройки", "\ud83d\udcd6 Команды"],
    ["\ud83d\udce6 Резервная копия"]
], resize_keyboard=True)

STAT_MENU = ReplyKeyboardMarkup([
    ["За 2 дня", "За 5 дней", "За 10 дней"],
    ["Назад"]
], resize_keyboard=True)

SETTINGS_MENU = ReplyKeyboardMarkup([
    ["Изменить кормления", "Изменить прогулки"],
    ["Назад"]
], resize_keyboard=True)

COMMAND_MENU = ReplyKeyboardMarkup([
    ["Показать команды", "Добавить команду"],
    ["Назад"]
], resize_keyboard=True)

VALID_ACTIONS = [
    ("\ud83c\udf1a", "Сон"),
    ("\ud83c\udf7d\ufe0f", "Еда"),
    ("\ud83c\udf3f", "Игры"),
    ("\ud83c\udf33", "Прогулка"),
    ("\ud83e\uddfb", "Био-прогулка")
]

# Функции загрузки/сохранения

def load_data(filename, default):
    if not os.path.exists(filename):
        return default
    with open(filename, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# Загрузка всех данных
activity_log = load_data(LOG_FILE, [])
commands = load_data(COMMANDS_FILE, [])
settings = load_data(SETTINGS_FILE, {"meals": 3, "walks": 3})

# Команды
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ALLOWED_USERS:
        await update.message.reply_text("⛔ Вам запрещено пользоваться этим ботом.")
        return
    await update.message.reply_text(
        "Привет! Я помогу следить за режимом щенка.",
        reply_markup=MAIN_MENU
    )

# Обработка кнопок
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ALLOWED_USERS:
        await update.message.reply_text("⛔ Вам запрещено пользоваться этим ботом.")
        return

    text = update.message.text.strip()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for emoji, action in VALID_ACTIONS:
        if text == f"{emoji} {action}":
            activity_log.append({"action": action, "emoji": emoji, "time": now, "user": update.effective_user.id})
            save_data(LOG_FILE, activity_log)
            await update.message.reply_text(f"Записал: {emoji} {action} в {now}")
            return

    if text == "\ud83d\udcca Статистика":
        await update.message.reply_text("Выбери период:", reply_markup=STAT_MENU)

    elif text.startswith("За"):
        days = int(text.split()[1])
        since = datetime.now() - timedelta(days=days)
        filtered = [entry for entry in activity_log if datetime.strptime(entry['time'], "%Y-%m-%d %H:%M:%S") >= since]
        if filtered:
            lines = [f"{e['time']} — {e.get('emoji', '')} {e['action']}" for e in filtered]
            await update.message.reply_text("\n".join(lines), reply_markup=MAIN_MENU)
        else:
            await update.message.reply_text("Нет записей за этот период.", reply_markup=MAIN_MENU)

    elif text == "\ud83d\udcd6 Команды":
        await update.message.reply_text("Что сделать с командами?", reply_markup=COMMAND_MENU)

    elif text == "Показать команды":
        if not commands:
            await update.message.reply_text("Пока нет изученных команд.")
        else:
            await update.message.reply_text("Изученные команды:\n" + "\n".join(commands))

    elif text == "Добавить команду":
        await update.message.reply_text("Напиши название новой команды:", reply_markup=ReplyKeyboardRemove())
        context.user_data['awaiting_command'] = True

    elif context.user_data.get('awaiting_command'):
        commands.append(text)
        save_data(COMMANDS_FILE, commands)
        context.user_data['awaiting_command'] = False
        await update.message.reply_text(f"Добавил команду: {text}", reply_markup=MAIN_MENU)

    elif text == "\ud83d\udd27 Настройки":
        await update.message.reply_text("Что изменить?", reply_markup=SETTINGS_MENU)

    elif text == "Изменить кормления":
        await update.message.reply_text("Введи новое число кормлений в день:", reply_markup=ReplyKeyboardRemove())
        context.user_data['awaiting_meals'] = True

    elif context.user_data.get('awaiting_meals'):
        if text.isdigit():
            settings['meals'] = int(text)
            save_data(SETTINGS_FILE, settings)
            context.user_data['awaiting_meals'] = False
            await update.message.reply_text(f"Обновлено: {text} кормлений в день.", reply_markup=MAIN_MENU)
        else:
            await update.message.reply_text("Пожалуйста, введи число.")

    elif text == "Изменить прогулки":
        await update.message.reply_text("Введи новое число прогулок в день:", reply_markup=ReplyKeyboardRemove())
        context.user_data['awaiting_walks'] = True

    elif context.user_data.get('awaiting_walks'):
        if text.isdigit():
            settings['walks'] = int(text)
            save_data(SETTINGS_FILE, settings)
            context.user_data['awaiting_walks'] = False
            await update.message.reply_text(f"Обновлено: {text} прогулок в день.", reply_markup=MAIN_MENU)
        else:
            await update.message.reply_text("Пожалуйста, введи число.")

    elif text == "Назад":
        await update.message.reply_text("Главное меню:", reply_markup=MAIN_MENU)

    else:
        await update.message.reply_text("Выбери действие из меню.", reply_markup=MAIN_MENU)

# Запуск

def main():
    if not TOKEN:
        print("[Ошибка] TELEGRAM_BOT_TOKEN не задан в .env")
        return

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == "__main__":
    main()
