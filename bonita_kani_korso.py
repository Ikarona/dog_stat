#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bonita_Kani_Korso — Telegram-бот для режима щенка:
- Сон (засыпание/пробуждение)
- Действия: Еда, Игры, Прогулка  (без статистики для биопрогулки)
- Постфактум-добавление событий с указанием начала и конца
- Ручная длительность прогулок
- Редактирование/удаление последних 10 записей по действию
- Статистика за 2/5/10 дней:
    • Еда — по расписанию: завтрак, обед, ужин, поздний ужин
    • Сон, Игры, Прогулка — разбивка по тем же приёмам пищи
- Интерактивный вывод последних 2, 5, 10 или 15 записей для выбранного действия
- Настройки:
    • Пользовательское расписание приёма пищи
    • Количество приёмов пищи в день
- Напоминания:
    • Еда — за 5 минут до приёма
    • Прогулка — за 1 ч 10 мин до приёма
    • Био-выход — через 4 минуты после регистрации еды
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

# === Конфигурация ===
load_dotenv()
BOT_TOKEN        = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_IDS = [
    int(x) for x in os.getenv("ALLOWED_USER_IDS","").split(",")
    if x.strip().isdigit()
]

# === Пути к файлам ===
LOG_FILE      = "activity_log.json"
SETTINGS_FILE = "settings.json"
COMMANDS_FILE = "commands.json"

# === Действия и эмодзи ===
ALL_ACTIONS     = ["Сон","Еда","Игры","Прогулка"]
VALID_ACTIONS   = [("🍽️","Еда"),("🌿","Игры"),("🌳","Прогулка")]
EMOJI_BY_ACTION = {"Сон":"🛌","Еда":"🍽️","Игры":"🌿","Прогулка":"🌳"}

CANCEL = "❌ Отмена"

MAIN_MENU = ReplyKeyboardMarkup([
    ["🛌 Сон",   "➕ Добавить вручную",  "✏️ Редактировать"],
    ["🍽️ Еда",  "🌿 Игры",             "⚙️ Настройки"],
    ["🌳 Прогулка","📊 Статистика",      "🕓 Последние"],
    ["📦 Резервная копия", CANCEL]
], resize_keyboard=True)

STATS_CHOICES = ReplyKeyboardMarkup([
    [KeyboardButton("2 дня")],
    [KeyboardButton("5 дней")],
    [KeyboardButton("10 дней")],
    [KeyboardButton(CANCEL)]
], resize_keyboard=True)

LAST_CHOICES = ReplyKeyboardMarkup([
    [KeyboardButton("2")],
    [KeyboardButton("5")],
    [KeyboardButton("10")],
    [KeyboardButton("15")],
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
    # подготовка расписания
    sched = settings["schedule"]
    sch_t = {k: datetime.strptime(v, "%H:%M").time() for k,v in sched.items()}
    periods = [
        ("breakfast","Завтрак"),
        ("lunch",    "Обед"),
        ("dinner",   "Ужин"),
        ("late_dinner","Поздний ужин")
    ]
    lines = [f"📊 Статистика за {days} дней:"]

    # Еда по периодам
    food = {p: [] for p,_ in periods}
    for e in entries:
        if e["action"]!="Еда": continue
        t = datetime.strptime(e["time"], "%Y-%m-%d %H:%M:%S").time()
        if   t < sch_t["lunch"]:       food["breakfast"].append(e["time"])
        elif t < sch_t["dinner"]:      food["lunch"].append(e["time"])
        elif t < sch_t["late_dinner"]: food["dinner"].append(e["time"])
        else:                          food["late_dinner"].append(e["time"])
    lines.append("\n🍽️ Еда:")
    for p,label in periods:
        lines.append(f"  • {label}: {len(food[p])} раз, ср. в {average_time(food[p])}")

    # Сон, Игры, Прогулка по тем же периодам
    for act, emoji in [("Сон","🛌"),("Игры","🌿"),("Прогулка","🌳")]:
        groups = {p: [] for p,_ in periods}
        for e in entries:
            if e["action"]!=act: continue
            t = datetime.strptime(e["time"], "%Y-%m-%d %H:%M:%S").time()
            if   t < sch_t["lunch"]:       groups["breakfast"].append(e["time"])
            elif t < sch_t["dinner"]:      groups["lunch"].append(e["time"])
            elif t < sch_t["late_dinner"]: groups["dinner"].append(e["time"])
            else:                          groups["late_dinner"].append(e["time"])
        lines.append(f"\n{emoji} {act}-приём:")
        for p,label in periods:
            lines.append(f"  • {label}: {len(groups[p])} раз, ср. в {average_time(groups[p])}")

    return "\n".join(lines)

# === Напоминания ===
async def send_backup(context: ContextTypes.DEFAULT_TYPE):
    for uid in ALLOWED_USER_IDS:
        for fn in (LOG_FILE, SETTINGS_FILE, COMMANDS_FILE):
            if os.path.exists(fn):
                await context.bot.send_document(
                    chat_id=uid, document=open(fn,"rb"),
                    caption="📦 Ежедневная копия"
                )

async def send_eat_reminder(context: ContextTypes.DEFAULT_TYPE):
    for uid in ALLOWED_USER_IDS:
        await context.bot.send_message(
            chat_id=uid,
            text="🍽️ Напоминание: через 5 мин — приём пищи."
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
        text="🧻 Напоминание: био-выход через 4 мин после еды."
    )

# === Обработчики ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ALLOWED_USER_IDS:
        return await update.message.reply_text("⛔️ Доступ запрещён.")
    await update.message.reply_text(
        "Привет! Я слежу за режимом щенка 🐶",
        reply_markup=MAIN_MENU
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid     = update.effective_user.id
    if uid not in ALLOWED_USER_IDS:
        return await update.message.reply_text("⛔️ Доступ запрещён.")
    text    = update.message.text.strip()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log     = load_data(LOG_FILE, [])

    # ❌ Отмена
    if text == CANCEL and uid in user_states:
        user_states.pop(uid)
        return await update.message.reply_text("❌ Операция отменена.", reply_markup=MAIN_MENU)

    # 🕓 Последние записи
    if text == "🕓 Последние":
        user_states[uid] = {"mode":"last", "step":0, "data":{}}
        kb = [[KeyboardButton(a)] for a in ALL_ACTIONS] + [[KeyboardButton(CANCEL)]]
        return await update.message.reply_text(
            "Выберите действие:", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
        )
    if uid in user_states and user_states[uid]["mode"] == "last":
        st = user_states[uid]; step = st["step"]; data = st["data"]
        if step == 0:
            if text not in ALL_ACTIONS:
                user_states.pop(uid)
                return await update.message.reply_text("❌ Отмена.", reply_markup=MAIN_MENU)
            data["action"] = text
            st["step"] = 1
            return await update.message.reply_text("Сколько записей показать?", reply_markup=LAST_CHOICES)
        if step == 1:
            if text not in ("2","5","10","15"):
                user_states.pop(uid)
                return await update.message.reply_text("❌ Отмена.", reply_markup=MAIN_MENU)
            n = int(text)
            entries = list_last_entries(log, data["action"], limit=n)
            user_states.pop(uid)
            if not entries:
                return await update.message.reply_text("Нет записей.", reply_markup=MAIN_MENU)
            lines = [f"Последние {n} для {data['action']}:"]
            for e in entries:
                lines.append(f"{e['time']} {e.get('note','')}")
            return await update.message.reply_text("\n".join(lines), reply_markup=MAIN_MENU)

    # 📊 Статистика
    if text == "📊 Статистика":
        return await update.message.reply_text("Выберите период:", reply_markup=STATS_CHOICES)
    if text in ("2 дня","5 дней","10 дней"):
        days = int(text.split()[0])
        return await update.message.reply_text(get_stats(log, days), reply_markup=MAIN_MENU)

    # ⚙️ Настройки
    if text == "⚙️ Настройки":
        return await update.message.reply_text("Выберите опцию:", reply_markup=SETTINGS_MENU)

    # -- Изменить расписание --
    if uid in user_states and user_states[uid]["mode"] == "schedule":
        st = user_states[uid]; step = st["step"]; data = st["data"]
        times = ["breakfast","lunch","dinner","late_dinner"]
        labels = {
            "breakfast":"завтрак","lunch":"обед",
            "dinner":"ужин","late_dinner":"поздний ужин"
        }
        # ввод времени
        try:
            datetime.strptime(text, "%H:%M")
        except:
            return await update.message.reply_text("Неверный формат, нужно ЧЧ:ММ")
        data[times[step]] = text
        step += 1
        if step < 4:
            st["step"] = step
            return await update.message.reply_text(f"Теперь введите время {labels[times[step]]} (ЧЧ:ММ):")
        # завершаем
        settings["schedule"] = data
        save_data(SETTINGS_FILE, settings)
        user_states.pop(uid)
        return await update.message.reply_text("✅ Расписание обновлено!", reply_markup=MAIN_MENU)

    if text == "Изменить расписание":
        sched = settings["schedule"]
        msg = "\n".join(f"{k}: {v}" for k,v in sched.items())
        user_states[uid] = {"mode":"schedule","step":0,"data":{}}
        return await update.message.reply_text(
            f"Текущее расписание:\n{msg}\n\nВведи время завтрака (ЧЧ:ММ):"
        )

    # -- Изменить кол-во приёмов пищи --
    if uid in user_states and user_states[uid]["mode"] == "set_feedings":
        try:
            n = int(text)
        except:
            return await update.message.reply_text("Нужно число.")
        settings["feedings_per_day"] = n
        save_data(SETTINGS_FILE, settings)
        user_states.pop(uid)
        return await update.message.reply_text(f"✅ Приёмов пищи в день: {n}", reply_markup=MAIN_MENU)

    if text == "Изменить кол-во приёмов пищи":
        user_states[uid] = {"mode":"set_feedings","step":0}
        return await update.message.reply_text("Сколько приёмов пищи в день?", reply_markup=MAIN_MENU)

    # ➕ Добавить вручную
    if uid in user_states and user_states[uid]["mode"] == "postfact":
        st = user_states[uid]; step = st["step"]; data = st["data"]
        # Шаг 0: выбор действия
        if step == 0:
            if text not in ALL_ACTIONS:
                user_states.pop(uid)
                return await update.message.reply_text("Неверное действие.", reply_markup=MAIN_MENU)
            data["action"] = text
            st["step"] = 1
            return await update.message.reply_text(
                "Введите время начала (ДД.MM.YYYY ЧЧ:ММ):",
                reply_markup=ReplyKeyboardMarkup([[KeyboardButton(CANCEL)]], resize_keyboard=True)
            )
        # Шаг 1: ввод начала
        if step == 1:
            try:
                dt0 = datetime.strptime(text, "%d.%m.%Y %H:%M")
            except:
                return await update.message.reply_text("Неверный формат.")
            data["start"] = dt0
            action = data["action"]
            if action == "Сон":
                st["step"] = 2
                return await update.message.reply_text(
                    "Введите время конца сна (ДД.MM.YYYY ЧЧ:ММ):",
                    reply_markup=ReplyKeyboardMarkup([[KeyboardButton(CANCEL)]], resize_keyboard=True)
                )
            if action == "Прогулка":
                st["step"] = 3
                return await update.message.reply_text(
                    "Введите длительность прогулки в минутах:",
                    reply_markup=ReplyKeyboardMarkup([[KeyboardButton(CANCEL)]], resize_keyboard=True)
                )
            # для еды и игр — просто записываем
            log.append({"action": action, "time": dt0.strftime("%Y-%m-%d %H:%M:%S"), "user": uid})
            save_data(LOG_FILE, trim_old(log))
            user_states.pop(uid)
            return await update.message.reply_text("✅ Записано.", reply_markup=MAIN_MENU)
        # Шаг 2: конец сна
        if step == 2:
            try:
                dt1 = datetime.strptime(text, "%d.%m.%Y %H:%M")
            except:
                return await update.message.reply_text("Неверный формат.")
            dt0 = data["start"]
            log.extend([
                {"action":"Сон","time":dt0.strftime("%Y-%m-%d %H:%M:%S"),"user":uid,"note":"start"},
                {"action":"Сон","time":dt1.strftime("%Y-%m-%d %H:%M:%S"),"user":uid,"note":"end"},
            ])
            save_data(LOG_FILE, trim_old(log))
            user_states.pop(uid)
            return await update.message.reply_text("✅ Сон записан.", reply_markup=MAIN_MENU)
        # Шаг 3: длительность прогулки
        if step == 3:
            try:
                mins = int(text)
            except:
                return await update.message.reply_text("Нужно число.")
            dt0 = data["start"]; dt1 = dt0 + timedelta(minutes=mins)
            log.extend([
                {"action":"Прогулка","time":dt0.strftime("%Y-%m-%d %H:%M:%S"),"user":uid,"note":"start"},
                {"action":"Прогулка","time":dt1.strftime("%Y-%m-%d %H:%M:%S"),"user":uid,"note":"end"},
            ])
            save_data(LOG_FILE, trim_old(log))
            user_states.pop(uid)
            return await update.message.reply_text("✅ Прогулка записана.", reply_markup=MAIN_MENU)

    if text == "➕ Добавить вручную":
        user_states[uid] = {"mode":"postfact","step":0,"data":{}}
        kb = [[KeyboardButton(a)] for a in ALL_ACTIONS] + [[KeyboardButton(CANCEL)]]
        return await update.message.reply_text(
            "Выберите действие:", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
        )

    # 🛌 Сон
    if text == "🛌 Сон":
        if uid in active_sleeps:
            s = active_sleeps.pop(uid)["start"]
            dt0 = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
            dt1 = datetime.now()
            diff = dt1 - dt0
            h,m = divmod(diff.seconds,3600); m//=60
            log.extend([
                {"action":"Сон","time":dt0.strftime("%Y-%m-%d %H:%M:%S"),"user":uid,"note":"start"},
                {"action":"Сон","time":dt1.strftime("%Y-%m-%d %H:%M:%S"),"user":uid,"note":"end"},
            ])
            save_data(LOG_FILE, trim_old(log))
            return await update.message.reply_text(f"😴 Пробуждение: {h}ч {m}м", reply_markup=MAIN_MENU)
        else:
            active_sleeps[uid] = {"start": now_str}
            return await update.message.reply_text("😴 Засыпание.", reply_markup=MAIN_MENU)

    # 🌳 Прогулка
    if text == "🌳 Прогулка":
        if uid in active_walks:
            s = active_walks.pop(uid)["start"]
            dt0 = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
            dt1 = datetime.now()
            diff = dt1 - dt0
            h,m = divmod(diff.seconds,3600); m//=60
            log.extend([
                {"action":"Прогулка","time":dt0.strftime("%Y-%m-%d %H:%M:%S"),"user":uid,"note":"start"},
                {"action":"Прогулка","time":dt1.strftime("%Y-%m-%d %H:%M:%S"),"user":uid,"note":"end"},
            ])
            save_data(LOG_FILE, trim_old(log))
            return await update.message.reply_text(f"🚶 Прогулка: {h}ч {m}м", reply_markup=MAIN_MENU)
        else:
            active_walks[uid] = {"start": now_str}
            return await update.message.reply_text("🚶 Началась прогулка.", reply_markup=MAIN_MENU)

    # ✏️ Редактировать
    if text == "✏️ Редактировать":
        user_states[uid] = {"mode":"edit","step":0,"data":{}}
        kb = [[KeyboardButton(a)] for a in ALL_ACTIONS] + [[KeyboardButton(CANCEL)]]
        return await update.message.reply_text(
            "Какое действие редактировать?", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
        )

    if uid in user_states and user_states[uid]["mode"]=="edit":
        st = user_states[uid]; step = st["step"]; data = st["data"]
        # выбор действия
        if step==0:
            if text not in ALL_ACTIONS:
                user_states.pop(uid)
                return await update.message.reply_text("❌ Отмена.", reply_markup=MAIN_MENU)
            data["action"]=text; st["step"]=1
            entries=list_last_entries(log,text,10)
            if not entries:
                user_states.pop(uid)
                return await update.message.reply_text("Нет записей.", reply_markup=MAIN_MENU)
            data["entries"]=entries
            msg="\n".join(f"{i+1}. {e['time']} ({e.get('note','')})" for i,e in enumerate(entries))
            kb = [[KeyboardButton(str(i+1))] for i in range(len(entries))] + [[KeyboardButton(CANCEL)]]
            return await update.message.reply_text("Выберите номер:\n"+msg, reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
        # выбор номер/операция
        if step==1:
            try:
                idx=int(text)-1
                entries=data["entries"]
                if not (0<=idx<len(entries)): raise
            except:
                user_states.pop(uid)
                return await update.message.reply_text("❌ Отмена.", reply_markup=MAIN_MENU)
            data["idx"]=idx; st["step"]=2
            kb=[[KeyboardButton("1")],[KeyboardButton("2")],[KeyboardButton("3")],[KeyboardButton(CANCEL)]]
            return await update.message.reply_text(
                "1. Изм. время начала\n2. Изм. время конца\n3. Удалить",
                reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
            )
        # выполнение операции
        if step==2:
            choice=text; entries=data["entries"]; idx=data["idx"]
            entry=entries[idx]
            if choice=="3":
                log2=[e for e in log if not (e["action"]==entry["action"] and e["time"]==entry["time"])]
                save_data(LOG_FILE, trim_old(log2))
                user_states.pop(uid)
                return await update.message.reply_text("✅ Удалено.", reply_markup=MAIN_MENU)
            if choice in ("1","2"):
                data["field"]="start" if choice=="1" else "end"
                st["step"]=3
                return await update.message.reply_text(
                    "Введите новое время (ДД.MM.YYYY ЧЧ:ММ):",
                    reply_markup=ReplyKeyboardMarkup([[KeyboardButton(CANCEL)]], resize_keyboard=True)
                )
            user_states.pop(uid)
            return await update.message.reply_text("❌ Отмена.", reply_markup=MAIN_MENU)
        # ввод нового времени
        if step==3:
            field=data["field"]; entries=data["entries"]; idx=data["idx"]; old=entries[idx]
            try:
                dt_new=datetime.strptime(text,"%d.%m.%Y %H:%M").strftime("%Y-%m-%d %H:%M:%S")
            except:
                user_states.pop(uid)
                return await update.message.reply_text("Неверный формат.", reply_markup=MAIN_MENU)
            new_log=[]
            for e in log:
                if e["action"]==old["action"] and e["time"]==old["time"] and e.get("note","")==field:
                    e["time"]=dt_new
                new_log.append(e)
            save_data(LOG_FILE, trim_old(new_log))
            user_states.pop(uid)
            return await update.message.reply_text("✅ Обновлено.", reply_markup=MAIN_MENU)

    # Простые действия (Еда, Игры, Прогулка)
    for emo, act in VALID_ACTIONS:
        if text==f"{emo} {act}":
            check_rotation()
            log.append({"action":act,"time":now_str,"user":uid})
            save_data(LOG_FILE, trim_old(log))
            if act=="Еда":
                context.job_queue.run_once(send_bio_reminder, when=4*60, data={"user_id":uid})
            return await update.message.reply_text(f"{emo} {act} записано.", reply_markup=MAIN_MENU)

    # 📦 Резервная копия
    if text=="📦 Резервная копия":
        for fn in (LOG_FILE, SETTINGS_FILE, COMMANDS_FILE):
            if os.path.exists(fn):
                await context.bot.send_document(update.effective_chat.id, open(fn,"rb"))
        return

    # fallback
    await update.message.reply_text("Выберите действие из меню.", reply_markup=MAIN_MENU)

# === Точка входа ===
def main():
    if not BOT_TOKEN or not ALLOWED_USER_IDS:
        print("Укажите TELEGRAM_BOT_TOKEN и ALLOWED_USER_IDS в .env")
        return

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    jq = app.job_queue
    # ежедневный бэкап
    jq.run_daily(send_backup, time=time(hour=23, minute=59))
    # напоминания по расписанию
    sched = settings["schedule"]
    for _, tstr in sched.items():
        hh, mm = map(int, tstr.split(":"))
        mt = datetime.combine(date.today(), time(hh, mm))
        jq.run_daily(send_eat_reminder,  time=(mt - timedelta(minutes=5)).time())
        jq.run_daily(send_walk_reminder, time=(mt - timedelta(hours=1, minutes=10)).time())

    print("✅ Bonita_Kani_Korso запущен")
    app.run_polling()

if __name__=="__main__":
    main()
