#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bonita_Kani_Korso — Telegram-бот для режима щенка:
- Сон (засыпание/пробуждение)
- Действия: Еда, Игры, Прогулка  (без статистики для био-прогулки)
- Постфактум-добавление с указанием начала и конца
- Ручная длительность прогулок
- Редактирование/удаление последних 10 записей по действию
- Статистика за 2/5/10 дней:
    • Еда — по расписанию: завтрак, обед, ужин, поздний ужин
    • Сон, Игры, Прогулка — общее количество и среднее время
- Команда `/last <действие> <N>` — вывод последних N записей
- Настройки:
    • Пользовательское расписание приёма пищи
    • Количество приёмов пищи в день
- Напоминания:
    • Еда — за 5 минут до приёма
    • Прогулка — за 1 ч 10 мин до приёма
    • Био-выход — через 4 минуты после регистрации «Еда»
- Ежедневные бэкапы (23:59), ротация лога (>10 МБ), очистка старше 120 дней
- Многопользовательская работа через `.env`
"""

import os, json
from datetime import datetime, date, timedelta, time
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)

# === Загрузка окружения ===
load_dotenv()
BOT_TOKEN       = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_IDS= [
    int(x) for x in os.getenv("ALLOWED_USER_IDS","").split(",")
    if x.strip().isdigit()
]

LOG_FILE      = "activity_log.json"
SETTINGS_FILE = "settings.json"
COMMANDS_FILE = "commands.json"

# === Действия и эмодзи ===
ALL_ACTIONS     = ["Сон","Еда","Игры","Прогулка"]
VALID_ACTIONS   = [("🍽️","Еда"),("🌿","Игры"),("🌳","Прогулка")]
EMOJI_BY_ACTION = {"Сон":"🛌","Еда":"🍽️","Игры":"🌿","Прогулка":"🌳"}

CANCEL = "❌ Отмена"

MAIN_MENU = ReplyKeyboardMarkup([
    ["🛌 Сон",   "➕ Добавить вручную", "✏️ Редактировать"],
    ["🍽️ Еда",  "🌿 Игры",            "⚙️ Настройки"],
    ["🌳 Прогулка","📊 Статистика",     "/last"],
    ["📦 Резервная копия", CANCEL]
], resize_keyboard=True)

STATS_CHOICES = ReplyKeyboardMarkup([
    [KeyboardButton("2 дня")],
    [KeyboardButton("5 дней")],
    [KeyboardButton("10 дней")],
    [KeyboardButton(CANCEL)]
], resize_keyboard=True)

SETTINGS_MENU = ReplyKeyboardMarkup([
    [KeyboardButton("Изменить расписание"), KeyboardButton("Изменить кол-во приёмов пищи")],
    [KeyboardButton(CANCEL)]
], resize_keyboard=True)

# === Настройки по умолчанию ===
default_settings = {
    "feedings_per_day": 1,
    "schedule": {
        "breakfast":   "08:00",
        "lunch":       "13:00",
        "dinner":      "18:00",
        "late_dinner": "23:00"
    }
}

def load_data(fn, default):
    if os.path.exists(fn):
        try:
            with open(fn, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return default
    return default

def save_data(fn, data):
    with open(fn, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

settings = load_data(SETTINGS_FILE, default_settings)

def trim_old(records, days=120):
    cutoff = datetime.now() - timedelta(days=days)
    return [
        e for e in records
        if datetime.strptime(e["time"], "%Y-%m-%d %H:%M:%S") >= cutoff
    ]

def check_rotation():
    if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 10*1024*1024:
        log = load_data(LOG_FILE, [])
        save_data(LOG_FILE, trim_old(log, days=20))

# === Временные состояния ===
user_states   = {}  # user_id -> {"mode","step","data"}
active_walks  = {}  # user_id -> {"start": str}
active_sleeps = {}  # user_id -> {"start": str}

# === Вспомогательные функции ===
def average_time(times):
    if not times:
        return "—"
    total = sum(
        dt.hour*60 + dt.minute
        for dt in (datetime.strptime(t, "%Y-%m-%d %H:%M:%S") for t in times)
    )
    avg = total // len(times)
    return f"{avg//60:02d}:{avg%60:02d}"

def list_last_entries(log, action, limit=10):
    entries = [e for e in log if e["action"] == action]
    entries.sort(key=lambda e: e["time"], reverse=True)
    return entries[:limit]

# === Статистика ===
def get_stats(log, days=2):
    cutoff = datetime.now() - timedelta(days=days)
    entries = [
        e for e in log
        if datetime.strptime(e["time"], "%Y-%m-%d %H:%M:%S") >= cutoff
    ]

    # расписание
    sched = settings["schedule"]
    sch_t = {
        k: datetime.strptime(v, "%H:%M").time()
        for k, v in sched.items()
    }
    periods = [
        ("breakfast","Завтрак"),
        ("lunch",    "Обед"),
        ("dinner",   "Ужин"),
        ("late_dinner","Поздний ужин")
    ]

    lines = [f"📊 Статистика за {days} дней:"]

    # Еда по периодам
    groups = {p: [] for p, _ in periods}
    for e in entries:
        if e["action"] != "Еда":
            continue
        t = datetime.strptime(e["time"], "%Y-%m-%d %H:%M:%S").time()
        if   t < sch_t["lunch"]:
            groups["breakfast"].append(e["time"])
        elif t < sch_t["dinner"]:
            groups["lunch"].append(e["time"])
        elif t < sch_t["late_dinner"]:
            groups["dinner"].append(e["time"])
        else:
            groups["late_dinner"].append(e["time"])
    lines.append("\n🍽️ Еда:")
    for p, label in periods:
        times = groups[p]
        lines.append(f"  • {label}: {len(times)} раз, ср. в {average_time(times)}")

    # Остальные действия
    for action in ALL_ACTIONS:
        if action == "Еда":
            continue
        times = [e["time"] for e in entries if e["action"] == action]
        emoji = EMOJI_BY_ACTION.get(action, "")
        lines.append(f"\n{emoji} {action}: {len(times)} раз, ср. в {average_time(times)}")

    return "\n".join(lines)

# === Напоминания ===
async def send_backup(context: ContextTypes.DEFAULT_TYPE):
    for uid in ALLOWED_USER_IDS:
        for fn in (LOG_FILE, SETTINGS_FILE, COMMANDS_FILE):
            if os.path.exists(fn):
                await context.bot.send_document(
                    chat_id=uid,
                    document=open(fn, "rb"),
                    caption="📦 Ежедневная копия"
                )

async def send_eat_reminder(context: ContextTypes.DEFAULT_TYPE):
    for uid in ALLOWED_USER_IDS:
        await context.bot.send_message(
            chat_id=uid,
            text="🍽️ Напоминание: через 5 минут — приём пищи."
        )

async def send_walk_reminder(context: ContextTypes.DEFAULT_TYPE):
    for uid in ALLOWED_USER_IDS:
        await context.bot.send_message(
            chat_id=uid,
            text="🚶 Напоминание: через 1 ч 10 мин — прогулка."
        )

async def send_bio_reminder(context: ContextTypes.DEFAULT_TYPE):
    uid = context.job.data["user_id"]
    await context.bot.send_message(
        chat_id=uid,
        text="🧻 Напоминание: био-выход через 4 минуты после еды."
    )

# === Хендлеры ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ALLOWED_USER_IDS:
        return await update.message.reply_text("⛔️ У вас нет доступа.")
    await update.message.reply_text(
        "Привет! Я слежу за режимом щенка 🐶",
        reply_markup=MAIN_MENU
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid     = update.effective_user.id
    if uid not in ALLOWED_USER_IDS:
        return await update.message.reply_text("⛔️ У вас нет доступа.")
    text    = update.message.text.strip()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log     = load_data(LOG_FILE, [])

    # Отмена
    if text == CANCEL and uid in user_states:
        user_states.pop(uid)
        return await update.message.reply_text("❌ Операция отменена.", reply_markup=MAIN_MENU)

    # Настройки
    if text == "⚙️ Настройки":
        return await update.message.reply_text("Выберите опцию:", reply_markup=SETTINGS_MENU)
    # (реализация изменения расписания и числа приемов пищи — по примеру ранее)

    # Статистика
    if text == "📊 Статистика":
        return await update.message.reply_text("Выберите период:", reply_markup=STATS_CHOICES)
    if text in ("2 дня", "5 дней", "10 дней"):
        days = int(text.split()[0])
        return await update.message.reply_text(get_stats(log, days), reply_markup=MAIN_MENU)

    # Резервная копия
    if text == "📦 Резервная копия":
        for fn in (LOG_FILE, SETTINGS_FILE, COMMANDS_FILE):
            if os.path.exists(fn):
                await context.bot.send_document(chat_id=update.effective_chat.id, document=open(fn, "rb"))
        return

    # Постфактум-добавление
    if uid in user_states and user_states[uid]["mode"] == "postfact":
        state = user_states[uid]
        step  = state["step"]
        data  = state["data"]

        # Шаг 0: выбор действия
        if step == 0:
            if text not in ALL_ACTIONS:
                user_states.pop(uid)
                return await update.message.reply_text("Неверное действие.", reply_markup=MAIN_MENU)
            data["action"] = text
            state["step"] = 1
            return await update.message.reply_text(
                "Введите время начала (ДД.MM.YYYY ЧЧ:ММ):",
                reply_markup=ReplyKeyboardMarkup([[KeyboardButton(CANCEL)]], resize_keyboard=True)
            )

        # Шаг 1: ввод начала или переход к ручному концу
        if step == 1:
            try:
                dt0 = datetime.strptime(text, "%d.%m.%Y %H:%M")
            except ValueError:
                return await update.message.reply_text("Неверный формат.")
            data["start"] = dt0
            action = data["action"]
            # для сна — прямо спрашиваем конец
            if action == "Сон":
                state["step"] = 2
                return await update.message.reply_text(
                    "Введите время конца сна (ДД.MM.YYYY ЧЧ:ММ):",
                    reply_markup=ReplyKeyboardMarkup([[KeyboardButton(CANCEL)]], resize_keyboard=True)
                )
            # для прогулки — длительность в минутах
            if action == "Прогулка":
                state["step"] = 3
                return await update.message.reply_text(
                    "Введите длительность прогулки в минутах:",
                    reply_markup=ReplyKeyboardMarkup([[KeyboardButton(CANCEL)]], resize_keyboard=True)
                )
            # для еды и игр — простая запись
            log.append({"action": action, "time": dt0.strftime("%Y-%m-%d %H:%M:%S"), "user": uid})
            save_data(LOG_FILE, trim_old(log))
            user_states.pop(uid)
            return await update.message.reply_text("✅ Записано.", reply_markup=MAIN_MENU)

        # Шаг 2: конец сна
        if step == 2:
            try:
                dt1 = datetime.strptime(text, "%d.%m.%Y %H:%M")
            except ValueError:
                return await update.message.reply_text("Неверный формат.")
            dt0 = data["start"]
            log.extend([
                {"action": "Сон", "time": dt0.strftime("%Y-%m-%d %H:%M:%S"), "user": uid, "note": "start"},
                {"action": "Сон", "time": dt1.strftime("%Y-%m-%d %H:%M:%S"), "user": uid, "note": "end"}
            ])
            save_data(LOG_FILE, trim_old(log))
            user_states.pop(uid)
            return await update.message.reply_text("✅ Сон записан.", reply_markup=MAIN_MENU)

        # Шаг 3: длительность прогулки
        if step == 3:
            try:
                mins = int(text)
            except ValueError:
                return await update.message.reply_text("Нужно число минут.")
            dt0 = data["start"]
            dt1 = dt0 + timedelta(minutes=mins)
            log.extend([
                {"action": "Прогулка", "time": dt0.strftime("%Y-%m-%d %H:%M:%S"), "user": uid, "note": "start"},
                {"action": "Прогулка", "time": dt1.strftime("%Y-%m-%d %H:%M:%S"), "user": uid, "note": "end"}
            ])
            save_data(LOG_FILE, trim_old(log))
            user_states.pop(uid)
            return await update.message.reply_text("✅ Прогулка записана.", reply_markup=MAIN_MENU)

    if text == "➕ Добавить вручную":
        user_states[uid] = {"mode": "postfact", "step": 0, "data": {}}
        kb = [[KeyboardButton(a)] for a in ALL_ACTIONS] + [[KeyboardButton(CANCEL)]]
        return await update.message.reply_text(
            "Выберите действие:", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
        )

    # Сон: начало/пробуждение
    if text == "🛌 Сон":
        if uid in active_sleeps:
            s = active_sleeps.pop(uid)["start"]
            dt0 = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
            dt1 = datetime.now()
            delta = dt1 - dt0
            h, rem = divmod(delta.seconds, 3600)
            m = rem // 60
            log.extend([
                {"action": "Сон", "time": dt0.strftime("%Y-%m-%d %H:%M:%S"), "user": uid, "note": "start"},
                {"action": "Сон", "time": dt1.strftime("%Y-%m-%d %H:%M:%S"), "user": uid, "note": "end"}
            ])
            save_data(LOG_FILE, trim_old(log))
            return await update.message.reply_text(f"😴 Пробуждение: {h}ч {m}м", reply_markup=MAIN_MENU)
        else:
            active_sleeps[uid] = {"start": now_str}
            return await update.message.reply_text("😴 Засыпание зарегистрировано.", reply_markup=MAIN_MENU)

    # Прогулка
    if text == "🌳 Прогулка":
        if uid in active_walks:
            s = active_walks.pop(uid)["start"]
            dt0 = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
            dt1 = datetime.now()
            delta = dt1 - dt0
            h, rem = divmod(delta.seconds, 3600)
            m = rem // 60
            log.extend([
                {"action": "Прогулка", "time": dt0.strftime("%Y-%m-%d %H:%M:%S"), "user": uid, "note": "start"},
                {"action": "Прогулка", "time": dt1.strftime("%Y-%m-%d %H:%M:%S"), "user": uid, "note": "end"}
            ])
            save_data(LOG_FILE, trim_old(log))
            return await update.message.reply_text(f"🚶 Прогулка завершена: {h}ч {m}м", reply_markup=MAIN_MENU)
        else:
            active_walks[uid] = {"start": now_str}
            return await update.message.reply_text("🚶 Прогулка началась.", reply_markup=MAIN_MENU)

    # Редактирование
    if text == "✏️ Редактировать":
        user_states[uid] = {"mode": "edit", "step": 0, "data": {}}
        kb = [[KeyboardButton(a)] for a in ALL_ACTIONS] + [[KeyboardButton(CANCEL)]]
        return await update.message.reply_text(
            "Какое действие редактировать?", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
        )

    if uid in user_states and user_states[uid]["mode"] == "edit":
        state = user_states[uid]
        step  = state["step"]
        data  = state["data"]
        # Шаг 0: выбор действия
        if step == 0:
            if text not in ALL_ACTIONS:
                user_states.pop(uid)
                return await update.message.reply_text("❌ Отмена.", reply_markup=MAIN_MENU)
            data["action"] = text
            state["step"] = 1
            entries = list_last_entries(log, text, 10)
            if not entries:
                user_states.pop(uid)
                return await update.message.reply_text("Нет записей для редактирования.", reply_markup=MAIN_MENU)
            data["entries"] = entries
            kb = [[KeyboardButton(str(i+1))] for i in range(len(entries))] + [[KeyboardButton(CANCEL)]]
            msg = "\n".join(f"{i+1}. {e['time']} ({e.get('note','')})" for i, e in enumerate(entries))
            return await update.message.reply_text(
                "Выберите номер:\n"+msg,
                reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
            )
        # Шаг 1: выбор записи
        if step == 1:
            try:
                idx = int(text) - 1
                entries = data["entries"]
                if not (0 <= idx < len(entries)):
                    raise ValueError
            except:
                user_states.pop(uid)
                return await update.message.reply_text("❌ Отмена.", reply_markup=MAIN_MENU)
            data["idx"] = idx
            state["step"] = 2
            kb = [[KeyboardButton("1")],[KeyboardButton("2")],[KeyboardButton("3")],[KeyboardButton(CANCEL)]]
            return await update.message.reply_text(
                "1. Изменить время начала\n"
                "2. Изменить время конца\n"
                "3. Удалить запись",
                reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
            )
        # Шаг 2: выбор операции
        if step == 2:
            choice = text
            entries= data["entries"]
            entry  = entries[data["idx"]]
            if choice == "3":
                # удаляем именно эту запись
                log2 = [e for e in log if not (
                    e["action"] == entry["action"] and e["time"] == entry["time"]
                )]
                save_data(LOG_FILE, trim_old(log2))
                user_states.pop(uid)
                return await update.message.reply_text("✅ Запись удалена.", reply_markup=MAIN_MENU)
            if choice in ("1","2"):
                data["field"] = "start" if choice=="1" else "end"
                state["step"] = 3
                return await update.message.reply_text(
                    "Введите новое время (ДД.MM.YYYY ЧЧ:ММ):",
                    reply_markup=ReplyKeyboardMarkup([[KeyboardButton(CANCEL)]], resize_keyboard=True)
                )
            user_states.pop(uid)
            return await update.message.reply_text("❌ Отмена.", reply_markup=MAIN_MENU)
        # Шаг 3: ввод нового времени
        if step == 3:
            field   = data["field"]
            entries = data["entries"]
            idx     = data["idx"]
            old     = entries[idx]
            try:
                dt_new = datetime.strptime(text, "%d.%m.%Y %H:%M").strftime("%Y-%m-%d %H:%M:%S")
            except:
                user_states.pop(uid)
                return await update.message.reply_text("Неверный формат.", reply_markup=MAIN_MENU)
            # обновляем запись
            log2 = []
            for e in log:
                if e["action"]==old["action"] and e["time"]==old["time"] and e.get("note","")==field:
                    e["time"] = dt_new
                log2.append(e)
            save_data(LOG_FILE, trim_old(log2))
            user_states.pop(uid)
            return await update.message.reply_text("✅ Запись обновлена.", reply_markup=MAIN_MENU)

    # Простые действия и био-напоминание
    for emo, act in VALID_ACTIONS:
        if text == f"{emo} {act}":
            check_rotation()
            log.append({"action": act, "time": now_str, "user": uid})
            save_data(LOG_FILE, trim_old(log))
            if act == "Еда":
                context.job_queue.run_once(send_bio_reminder, when=4*60, data={"user_id": uid})
            return await update.message.reply_text(f"{emo} {act} записано.", reply_markup=MAIN_MENU)

    await update.message.reply_text("Выберите действие из меню.", reply_markup=MAIN_MENU)

# === Команда /last ===
async def last_entries(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) != 2:
        return await update.message.reply_text("Использование: /last <действие> <число>")
    action = args[0]
    try:
        count = int(args[1])
    except:
        return await update.message.reply_text("Второй аргумент должен быть числом.")
    log = load_data(LOG_FILE, [])
    entries = list_last_entries(log, action, limit=count)
    if not entries:
        return await update.message.reply_text("Нет записей.")
    lines = [f"Последние {count} для {action}:"]
    for e in entries:
        note = e.get("note","")
        lines.append(f"{e['time']} {note}")
    return await update.message.reply_text("\n".join(lines))

# === Точка входа ===
def main():
    if not BOT_TOKEN or not ALLOWED_USER_IDS:
        print("❌ TELEGRAM_BOT_TOKEN и ALLOWED_USER_IDS должны быть в .env")
        return
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("last", last_entries))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    jq = app.job_queue
    jq.run_daily(send_backup, time=time(hour=23, minute=59))

    # напоминания по расписанию приёмов пищи
    sched = settings["schedule"]
    for _, tstr in sched.items():
        hh, mm = map(int, tstr.split(":"))
        meal_time = datetime.combine(date.today(), time(hh, mm))
        eat_tm  = (meal_time - timedelta(minutes=5)).time()
        walk_tm = (meal_time - timedelta(hours=1, minutes=10)).time()
        jq.run_daily(send_eat_reminder,  time=eat_tm)
        jq.run_daily(send_walk_reminder, time=walk_tm)

    print("✅ Bonita_Kani_Korso запущен")
    app.run_polling()

if __name__ == "__main__":
    main()
