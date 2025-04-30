#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bonita_Kani_Korso — Telegram-бот для режима щенка:
- Сон, Прогулка, Игры, Био-прогулка: toggle start/stop (фиксируем start/end и длительность)
- Еда: моментальная запись + био-напоминание через 4 мин
- Туалет (💩 какашки, 🚰 мочи): подменю Дом/Улица → (Дом) Пеленка/Мимо
- Постфактум-добавление событий с началом/концом или длительностью
- Редактирование/удаление последних 10 записей
- Статистика за 2/5/10 дней:
    • Еда — по расписанию (завтрак/обед/ужин/поздний ужин)
    • Сон, Прогулка — «до приёма пищи»: кол-во + ср. длительность
    • Игры, Туалет — «до приёма пищи»: кол-во + ср. время регистрации
- Интерактивный вывод последних 2/5/10/15 записей
- CRUD выученных команд
- Настройки: расписание + кол-во приёмов пищи
- Напоминания: еда за 5 мин, прогулка за 1 ч 10 мин, био-выход через 4 мин
- Бэкапы (23:59), ротация (>10 МБ), очистка (>120 дн)
- Многопользовательская работа через .env
"""
import os, json
from datetime import datetime, date, timedelta, time
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# --- Конфигурация ---
load_dotenv()
BOT_TOKEN        = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_IDS = [int(x) for x in os.getenv("ALLOWED_USER_IDS","").split(",") if x.strip().isdigit()]

# --- Файлы ---
LOG_FILE      = "activity_log.json"
SETTINGS_FILE = "settings.json"
COMMANDS_FILE = "commands.json"

# --- Списки действий и эмодзи ---
ALL_ACTIONS = [
    "Сон","Еда","Игры","Прогулка","Био-прогулка",
    "Туалет (какашки)","Туалет (мочи)"
]
VALID_ACTIONS = [
    ("🛌","Сон"),("🍽️","Еда"),("🌿","Игры"),
    ("🌳","Прогулка"),("🧻","Био-прогулка"),
    ("💩","Туалет (какашки)"),("🚰","Туалет (мочи)")
]
EMOJI_BY_ACTION = {act:emo for emo,act in VALID_ACTIONS}

# --- Кнопочные меню ---
CANCEL = "❌ Отмена"
MAIN_MENU = ReplyKeyboardMarkup([
    ["🛌 Сон","➕ Добавить вручную","✏️ Редактировать"],
    ["🍽️ Еда","🌿 Игры","⚙️ Настройки"],
    ["🌳 Прогулка","🧻 Био-прогулка","📊 Статистика"],
    ["💩 Туалет (какашки)","🚰 Туалет (мочи)","🕓 Последние"],
    ["💬 Команды","📦 Резервная копия",CANCEL]
], resize_keyboard=True)
STATS_CHOICES = ReplyKeyboardMarkup([[KeyboardButton("2 дня")],[KeyboardButton("5 дней")],[KeyboardButton("10 дней")],[KeyboardButton(CANCEL)]], resize_keyboard=True)
LAST_CHOICES  = ReplyKeyboardMarkup([[KeyboardButton(x)] for x in ("2","5","10","15")] + [[KeyboardButton(CANCEL)]], resize_keyboard=True)
CMD_MENU      = ReplyKeyboardMarkup([["Просмотр","Добавить"],["Редактировать","Удалить"],[CANCEL]], resize_keyboard=True)
SETT_MENU     = ReplyKeyboardMarkup([["Изменить расписание","Изменить кол-во приёмов пищи"],[CANCEL]], resize_keyboard=True)

# --- Настройки по умолчанию ---
default_settings = {
    "feedings_per_day":1,
    "schedule":{
        "breakfast":"08:00","lunch":"13:00",
        "dinner":"18:00","late_dinner":"23:00"
    }
}

# --- Загрузка/сохранение данных ---
def load_data(fn, default):
    if os.path.exists(fn):
        try:
            with open(fn,"r",encoding="utf-8") as f: return json.load(f)
        except json.JSONDecodeError: return default
    return default

def save_data(fn, data):
    with open(fn,"w",encoding="utf-8") as f: json.dump(data,f,ensure_ascii=False,indent=2)

settings = load_data(SETTINGS_FILE, default_settings)
commands = load_data(COMMANDS_FILE, [])

# --- Ротация / очистка ---
def trim_old(records, days=120):
    cut = datetime.now() - timedelta(days=days)
    return [e for e in records if datetime.strptime(e["time"],"%Y-%m-%d %H:%M:%S")>=cut]

def check_rotation():
    if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE)>10*1024*1024:
        log = load_data(LOG_FILE, [])
        save_data(LOG_FILE, trim_old(log,days=20))

# --- Состояния и активные сессии ---
user_states   = {}  # user_id -> {mode,step,data}
active_sleeps = {}  # user_id -> {"start":...}
active_walks  = {}
active_games  = {}
active_bios   = {}

# --- Утилиты времени ---
def average_time(times):
    if not times: return "—"
    tot=sum(dt.hour*60+dt.minute for dt in (datetime.strptime(t,"%Y-%m-%d %H:%M:%S") for t in times))
    avg=tot//len(times)
    return f"{avg//60:02d}:{avg%60:02d}"

def average_duration(mins_list):
    if not mins_list: return "—"
    avg=sum(mins_list)/len(mins_list)
    h=int(avg)//60; m=int(avg)%60
    return f"{h}ч {m}м"

def list_last_entries(log, action, limit=10):
    ents=[e for e in log if e["action"]==action]
    ents.sort(key=lambda x:x["time"],reverse=True)
    return ents[:limit]

def extract_durations(log, action):
    pairs=[]; start=None
    for e in sorted(log,key=lambda x:x["time"]):
        if e["action"]==action and e.get("note")=="start":
            start=datetime.strptime(e["time"],"%Y-%m-%d %H:%M:%S")
        elif e["action"]==action and e.get("note")=="end" and start:
            end=datetime.strptime(e["time"],"%Y-%m-%d %H:%M:%S")
            mins=int((end-start).total_seconds()/60)
            pairs.append((mins,end.time()))
            start=None
    return pairs

# --- Статистика ---
def get_stats(log, days=2):
    cut=datetime.now()-timedelta(days=days)
    ent=[e for e in log if datetime.strptime(e["time"],"%Y-%m-%d %H:%M:%S")>=cut]
    sched=settings["schedule"]
    sch_t={k:datetime.strptime(v,"%H:%M").time() for k,v in sched.items()}
    periods=[("breakfast","Завтрак"),("lunch","Обед"),("dinner","Ужин"),("late_dinner","Поздний ужин")]
    lines=[f"📊 Статистика за {days} дней:"]

    # Еда
    food={p:[] for p,_ in periods}
    for e in ent:
        if e["action"]!="Еда": continue
        t=datetime.strptime(e["time"],"%Y-%m-%d %H:%M:%S").time()
        if t<sch_t["lunch"]: food["breakfast"].append(e["time"])
        elif t<sch_t["dinner"]: food["lunch"].append(e["time"])
        elif t<sch_t["late_dinner"]: food["dinner"].append(e["time"])
        else: food["late_dinner"].append(e["time"])
    lines.append("\n🍽️ Еда:")
    for p,label in periods:
        lines.append(f"  • {label}: {len(food[p])} раз, ср. в {average_time(food[p])}")

    # Сон и Прогулка
    for act,emoji in [("Сон","🛌"),("Прогулка","🌳")]:
        pairs=extract_durations(ent,act)
        grp={p:[] for p,_ in periods}
        for mins,tt in pairs:
            if tt<sch_t["lunch"]: grp["breakfast"].append(mins)
            elif tt<sch_t["dinner"]: grp["lunch"].append(mins)
            elif tt<sch_t["late_dinner"]: grp["dinner"].append(mins)
            else: grp["late_dinner"].append(mins)
        lines.append(f"\n{emoji} {act}-до-приёма:")
        for p,label in periods:
            arr=grp[p]
            lines.append(f"  • {label}: {len(arr)} раз, ср. длительность {average_duration(arr)}")

    # Игры и Туалет
    for act,emoji in [("Игры","🌿"),("Туалет (какашки)","💩"),("Туалет (мочи)","🚰")]:
        grp={p:[] for p,_ in periods}
        for e in ent:
            if e["action"]!=act: continue
            t=datetime.strptime(e["time"],"%Y-%m-%d %H:%M:%S").time()
            if t<sch_t["lunch"]: grp["breakfast"].append(e["time"])
            elif t<sch_t["dinner"]: grp["lunch"].append(e["time"])
            elif t<sch_t["late_dinner"]: grp["dinner"].append(e["time"])
            else: grp["late_dinner"].append(e["time"])
        lines.append(f"\n{emoji} {act}-до-приёма:")
        for p,label in periods:
            lines.append(f"  • {label}: {len(grp[p])} раз, ср. в {average_time(grp[p])}")

    return "\n".join(lines)

# --- Напоминания ---
async def send_backup(context:ContextTypes.DEFAULT_TYPE):
    for uid in ALLOWED_USER_IDS:
        for fn in (LOG_FILE,SETTINGS_FILE,COMMANDS_FILE):
            if os.path.exists(fn):
                await context.bot.send_document(uid,open(fn,"rb"),caption="📦 Ежедневная копия")

async def send_eat_reminder(context:ContextTypes.DEFAULT_TYPE):
    for uid in ALLOWED_USER_IDS:
        await context.bot.send_message(uid,"🍽️ Напоминание: через 5 мин — приём пищи.")

async def send_walk_reminder(context:ContextTypes.DEFAULT_TYPE):
    for uid in ALLOWED_USER_IDS:
        await context.bot.send_message(uid,"🚶 Напоминание: через 1 ч 10 мин — прогулка.")

async def send_bio_reminder(context:ContextTypes.DEFAULT_TYPE):
    uid=context.job.data["user_id"]
    await context.bot.send_message(uid,"🧻 Напоминание: био-выход через 4 мин после еды.")

# --- Хендлеры ---
async def start(update:Update, context:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if uid not in ALLOWED_USER_IDS:
        return await update.message.reply_text("⛔️ Доступ запрещён.")
    await update.message.reply_text("Привет! Я слежу за режимом щенка 🐶",reply_markup=MAIN_MENU)

async def handle_message(update:Update, context:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if uid not in ALLOWED_USER_IDS:
        return await update.message.reply_text("⛔️ Доступ запрещён.")
    text=update.message.text.strip()
    now_str=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log=load_data(LOG_FILE,[])

    # Отмена
    if text==CANCEL and uid in user_states:
        user_states.pop(uid)
        return await update.message.reply_text("❌ Отмена.",reply_markup=MAIN_MENU)

    # Туалет
    if text in ("💩 Туалет (какашки)","🚰 Туалет (мочи)"):
        action=text.split(" ",1)[1]
        user_states[uid]={"mode":"toilet","step":0,"data":{"action":action}}
        kb=[[KeyboardButton("Дом")],[KeyboardButton("Улица")],[KeyboardButton(CANCEL)]]
        return await update.message.reply_text("Где?",reply_markup=ReplyKeyboardMarkup(kb,resize_keyboard=True))
    if uid in user_states and user_states[uid]["mode"]=="toilet":
        st=user_states[uid]; step=st["step"]; data=st["data"]
        if step==0:
            if text=="Улица":
                log.append({"action":data["action"],"time":now_str,"user":uid,"note":"outside"})
                save_data(LOG_FILE,trim_old(log)); user_states.pop(uid)
                return await update.message.reply_text(f"✅ {data['action']} на улице.",reply_markup=MAIN_MENU)
            if text=="Дом":
                st["step"]=1
                kb=[[KeyboardButton("Пеленка")],[KeyboardButton("Мимо")],[KeyboardButton(CANCEL)]]
                return await update.message.reply_text("Пеленка или мимо?",reply_markup=ReplyKeyboardMarkup(kb,resize_keyboard=True))
            user_states.pop(uid)
            return await update.message.reply_text("❌ Отмена.",reply_markup=MAIN_MENU)
        if step==1:
            note="home-pad" if text=="Пеленка" else "home-miss"
            log.append({"action":data["action"],"time":now_str,"user":uid,"note":note})
            save_data(LOG_FILE,trim_old(log)); user_states.pop(uid)
            return await update.message.reply_text(f"✅ {data['action']} дома: {text.lower()}.",reply_markup=MAIN_MENU)

    # Сон toggle
    if text=="🛌 Сон":
        if uid in active_sleeps:
            start=active_sleeps.pop(uid)["start"]
            dt0=datetime.strptime(start,"%Y-%m-%d %H:%M:%S")
            mins=int((datetime.now()-dt0).total_seconds()//60)
            log.extend([{"action":"Сон","time":start,"user":uid,"note":"start"},
                        {"action":"Сон","time":now_str,"user":uid,"note":"end"}])
            save_data(LOG_FILE,trim_old(log))
            return await update.message.reply_text(f"😴 Сон: {mins//60}ч {mins%60}м",reply_markup=MAIN_MENU)
        active_sleeps[uid]={"start":now_str}
        return await update.message.reply_text("😴 Сон начат.",reply_markup=MAIN_MENU)

    # Прогулка toggle
    if text=="🌳 Прогулка":
        if uid in active_walks:
            start=active_walks.pop(uid)["start"]
            dt0=datetime.strptime(start,"%Y-%m-%d %H:%M:%S")
            mins=int((datetime.now()-dt0).total_seconds()//60)
            log.extend([{"action":"Прогулка","time":start,"user":uid,"note":"start"},
                        {"action":"Прогулка","time":now_str,"user":uid,"note":"end"}])
            save_data(LOG_FILE,trim_old(log))
            return await update.message.reply_text(f"🚶 Прогулка: {mins//60}ч {mins%60}м",reply_markup=MAIN_MENU)
        active_walks[uid]={"start":now_str}
        return await update.message.reply_text("🚶 Прогулка начата.",reply_markup=MAIN_MENU)

    # Игры toggle
    if text=="🌿 Игры":
        if uid in active_games:
            start=active_games.pop(uid)["start"]
            dt0=datetime.strptime(start,"%Y-%m-%d %H:%M:%S")
            mins=int((datetime.now()-dt0).total_seconds()//60)
            log.extend([{"action":"Игры","time":start,"user":uid,"note":"start"},
                        {"action":"Игры","time":now_str,"user":uid,"note":"end"}])
            save_data(LOG_FILE,trim_old(log))
            return await update.message.reply_text(f"🌿 Игры: {mins//60}ч {mins%60}м",reply_markup=MAIN_MENU)
        active_games[uid]={"start":now_str}
        return await update.message.reply_text("🌿 Игры начаты.",reply_markup=MAIN_MENU)

    # Био-прогулка toggle
    if text=="🧻 Био-прогулка":
        if uid in active_bios:
            start=active_bios.pop(uid)["start"]
            dt0=datetime.strptime(start,"%Y-%m-%d %H:%M:%S")
            mins=int((datetime.now()-dt0).total_seconds()//60)
            log.extend([{"action":"Био-прогулка","time":start,"user":uid,"note":"start"},
                        {"action":"Био-прогулка","time":now_str,"user":uid,"note":"end"}])
            save_data(LOG_FILE,trim_old(log))
            return await update.message.reply_text(f"🧻 Био-прогулка: {mins//60}ч {mins%60}м",reply_markup=MAIN_MENU)
        active_bios[uid]={"start":now_str}
        return await update.message.reply_text("🧻 Био-прогулка начата.",reply_markup=MAIN_MENU)

    # Еда
    if text=="🍽️ Еда":
        check_rotation()
        log.append({"action":"Еда","time":now_str,"user":uid})
        save_data(LOG_FILE,trim_old(log))
        context.job_queue.run_once(send_bio_reminder, when=4*60, data={"user_id":uid})
        return await update.message.reply_text("🍽️ Еда записана.",reply_markup=MAIN_MENU)

    # ➕ Добавить вручную
    if text=="➕ Добавить вручную":
        user_states[uid]={"mode":"postfact","step":0,"data":{}}
        kb=[[KeyboardButton(a)] for a in ALL_ACTIONS]+[[KeyboardButton(CANCEL)]]
        return await update.message.reply_text("Выберите действие:",reply_markup=ReplyKeyboardMarkup(kb,resize_keyboard=True))
        # Постфактум-добавление
    if uid in user_states and user_states[uid]["mode"] == "postfact":
        st = user_states[uid]
        step = st["step"]
        data = st["data"]

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

        # Шаг 1: начало
        if step == 1:
            try:
                dt0 = datetime.strptime(text, "%d.%m.%Y %H:%M")
            except ValueError:
                return await update.message.reply_text("Неверный формат. Попробуйте: ДД.MM.YYYY ЧЧ:ММ")
            data["start"] = dt0
            action = data["action"]

            if action == "Сон":
                st["step"] = 2
                return await update.message.reply_text(
                    "Введите время конца сна (ДД.MM.YYYY ЧЧ:ММ):",
                    reply_markup=ReplyKeyboardMarkup([[KeyboardButton(CANCEL)]], resize_keyboard=True)
                )
            if action in ("Прогулка", "Био-прогулка"):
                st["step"] = 3
                return await update.message.reply_text(
                    "Введите длительность в минутах:",
                    reply_markup=ReplyKeyboardMarkup([[KeyboardButton(CANCEL)]], resize_keyboard=True)
                )
            # Еда, Игры, Туалет
            log.append({
                "action": action,
                "time": dt0.strftime("%Y-%m-%d %H:%M:%S"),
                "user": uid
            })
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
                {"action": "Сон", "time": dt1.strftime("%Y-%m-%d %H:%M:%S"), "user": uid, "note": "end"},
            ])
            save_data(LOG_FILE, trim_old(log))
            user_states.pop(uid)
            return await update.message.reply_text("✅ Сон записан.", reply_markup=MAIN_MENU)

        # Шаг 3: длительность прогулки/био-прогулки
        if step == 3:
            try:
                mins = int(text)
            except ValueError:
                return await update.message.reply_text("Нужно число минут.")
            dt0 = data["start"]
            dt1 = dt0 + timedelta(minutes=mins)
            log.extend([
                {"action": data["action"], "time": dt0.strftime("%Y-%m-%d %H:%M:%S"), "user": uid, "note": "start"},
                {"action": data["action"], "time": dt1.strftime("%Y-%m-%d %H:%M:%S"), "user": uid, "note": "end"},
            ])
            save_data(LOG_FILE, trim_old(log))
            user_states.pop(uid)
            return await update.message.reply_text("✅ Длительность записана.", reply_markup=MAIN_MENU)

    # Запуск постфактум-режима
    if text == "➕ Добавить вручную":
        user_states[uid] = {"mode": "postfact", "step": 0, "data": {}}
        kb = [[KeyboardButton(a)] for a in ALL_ACTIONS] + [[KeyboardButton(CANCEL)]]
        return await update.message.reply_text("Что добавить?", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))

    # Редактирование последних записей
    if text == "✏️ Редактировать":
        user_states[uid] = {"mode": "edit", "step": 0, "data": {}}
        kb = [[KeyboardButton(a)] for a in ALL_ACTIONS] + [[KeyboardButton(CANCEL)]]
        return await update.message.reply_text("Какое действие редактировать?", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))

    if uid in user_states and user_states[uid]["mode"] == "edit":
        st = user_states[uid]
        step = st["step"]
        data = st["data"]

        # Шаг 0: выбор действия
        if step == 0:
            if text not in ALL_ACTIONS:
                user_states.pop(uid)
                return await update.message.reply_text("❌ Отмена.", reply_markup=MAIN_MENU)
            data["action"] = text
            entries = list_last_entries(log, text, limit=10)
            if not entries:
                user_states.pop(uid)
                return await update.message.reply_text("Нет записей для редактирования.", reply_markup=MAIN_MENU)
            data["entries"] = entries
            st["step"] = 1
            kb = [[KeyboardButton(str(i+1))] for i in range(len(entries))] + [[KeyboardButton(CANCEL)]]
            msg = "\n".join(f"{i+1}. {e['time']} ({e.get('note','')})" for i, e in enumerate(entries))
            return await update.message.reply_text("Выберите номер:\n"+msg, reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))

        # Шаг 1: операция над записью
        if step == 1:
            try:
                idx = int(text) - 1
                entry = data["entries"][idx]
            except:
                user_states.pop(uid)
                return await update.message.reply_text("❌ Отмена.", reply_markup=MAIN_MENU)
            data["idx"] = idx
            st["step"] = 2
            kb = [[KeyboardButton("1")],[KeyboardButton("2")],[KeyboardButton("3")],[KeyboardButton(CANCEL)]]
            return await update.message.reply_text(
                "1. Изменить начало\n2. Изменить конец\n3. Удалить запись",
                reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
            )

        # Шаг 2: выбор действия
        if step == 2:
            choice = text
            entry = data["entries"][data["idx"]]
            if choice == "3":
                log2 = [e for e in log if not (e["action"] == entry["action"] and e["time"] == entry["time"])]
                save_data(LOG_FILE, trim_old(log2))
                user_states.pop(uid)
                return await update.message.reply_text("✅ Удалено.", reply_markup=MAIN_MENU)
            if choice in ("1","2"):
                data["field"] = "start" if choice=="1" else "end"
                st["step"] = 3
                return await update.message.reply_text(
                    "Введите новое время (ДД.MM.YYYY ЧЧ:ММ):",
                    reply_markup=ReplyKeyboardMarkup([[KeyboardButton(CANCEL)]], resize_keyboard=True)
                )
            user_states.pop(uid)
            return await update.message.reply_text("❌ Отмена.", reply_markup=MAIN_MENU)

        # Шаг 3: ввод нового времени
        if step == 3:
            field = data["field"]
            entry = data["entries"][data["idx"]]
            try:
                dt_new = datetime.strptime(text, "%d.%m.%Y %H:%M").strftime("%Y-%m-%d %H:%M:%S")
            except:
                user_states.pop(uid)
                return await update.message.reply_text("Неверный формат.", reply_markup=MAIN_MENU)
            new_log = []
            for e in log:
                if e["action"] == entry["action"] and e["time"] == entry["time"] and e.get("note","") == field:
                    e["time"] = dt_new
                new_log.append(e)
            save_data(LOG_FILE, trim_old(new_log))
            user_states.pop(uid)
            return await update.message.reply_text("✅ Обновлено.", reply_markup=MAIN_MENU)

    # Статистика
    if text == "📊 Статистика":
        return await update.message.reply_text("Выберите период:", reply_markup=STATS_CHOICES)
    if text in ("2 дня","5 дней","10 дней"):
        days = int(text.split()[0])
        return await update.message.reply_text(get_stats(log, days), reply_markup=MAIN_MENU)

    # Последние записи
    if text == "🕓 Последние":
        user_states[uid] = {"mode":"last","step":0,"data":{}}
        kb = [[KeyboardButton(a)] for a in ALL_ACTIONS] + [[KeyboardButton(CANCEL)]]
        return await update.message.reply_text("Выберите действие:", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))

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
            ents = list_last_entries(log, data["action"], limit=n)
            user_states.pop(uid)
            if not ents:
                return await update.message.reply_text("Нет записей.", reply_markup=MAIN_MENU)
            msg = [f"Последние {n} для {data['action']}:"]
            msg += [f"{e['time']} {e.get('note','')}" for e in ents]
            return await update.message.reply_text("\n".join(msg), reply_markup=MAIN_MENU)

    # CRUD выученных команд
    if text == "💬 Команды":
        user_states[uid] = {"mode":"cmd","step":0,"data":{}}
        return await update.message.reply_text("Выберите действие:", reply_markup=CMD_MENU)
    if uid in user_states and user_states[uid]["mode"] == "cmd":
        st = user_states[uid]; step = st["step"]; data = st["data"]
        if step == 0:
            if text == "Просмотр":
                user_states.pop(uid)
                if not commands:
                    return await update.message.reply_text("Нет команд.", reply_markup=MAIN_MENU)
                lines = ["📚 Выученные команды:"]
                lines += [f"/{c['command']}: {c['description']}" for c in commands]
                return await update.message.reply_text("\n".join(lines), reply_markup=MAIN_MENU)
            if text in ("Добавить","Редактировать","Удалить"):
                data["op"] = text; st["step"] = 1
                return await update.message.reply_text("Введите имя команды (без /):")
            user_states.pop(uid)
            return await update.message.reply_text("❌ Отмена.", reply_markup=MAIN_MENU)
        if step == 1:
            name = text.strip(); data["name"] = name; op = data["op"]
            found = [c for c in commands if c["command"] == name]
            if op == "Добавить":
                st["step"] = 2
                return await update.message.reply_text("Введите описание команды:")
            if not found:
                user_states.pop(uid)
                return await update.message.reply_text("Команда не найдена.", reply_markup=MAIN_MENU)
            data["cmd"] = found[0]
            if op == "Удалить":
                commands.remove(data["cmd"]); save_data(COMMANDS_FILE, commands)
                user_states.pop(uid)
                return await update.message.reply_text("✅ Удалено.", reply_markup=MAIN_MENU)
            st["step"] = 2
            return await update.message.reply_text("Введите новое описание:")
        if step == 2:
            desc = text.strip(); op = data["op"]
            if op == "Добавить":
                commands.append({"command":data["name"],"description":desc})
                res = "✅ Добавлено."
            else:
                data["cmd"]["description"] = desc
                res = "✅ Обновлено."
            save_data(COMMANDS_FILE, commands)
            user_states.pop(uid)
            return await update.message.reply_text(res, reply_markup=MAIN_MENU)

    # Настройки
    if text == "⚙️ Настройки":
        return await update.message.reply_text("Выберите опцию:", reply_markup=SETT_MENU)
    if uid in user_states and user_states[uid]["mode"] == "schedule":
        st = user_states[uid]; step = st["step"]; data = st["data"]
        times = ["breakfast","lunch","dinner","late_dinner"]
        labels = {"breakfast":"завтрак","lunch":"обед","dinner":"ужин","late_dinner":"поздний ужин"}
        try:
            datetime.strptime(text, "%H:%M")
        except:
            return await update.message.reply_text("Неверный формат ЧЧ:ММ")
        data[times[step]] = text
        step += 1
        if step < 4:
            st["step"] = step
            return await update.message.reply_text(f"Введите время {labels[times[step]]} (ЧЧ:ММ):")
        settings["schedule"] = data; save_data(SETTINGS_FILE, settings)
        user_states.pop(uid)
        return await update.message.reply_text("✅ Расписание обновлено.", reply_markup=MAIN_MENU)
    if text == "Изменить расписание":
        sched = settings["schedule"]
        msg = "\n".join(f"{k}: {v}" for k,v in sched.items())
        user_states[uid] = {"mode":"schedule","step":0,"data":{}}
        return await update.message.reply_text(f"Текущее расписание:\n{msg}\n\nВедите время завтрака (ЧЧ:ММ):")
    if uid in user_states and user_states[uid]["mode"] == "set_feedings":
        try:
            n = int(text)
        except:
            return await update.message.reply_text("Нужно число.")
        settings["feedings_per_day"] = n; save_data(SETTINGS_FILE, settings)
        user_states.pop(uid)
        return await update.message.reply_text(f"✅ Приёмов пищи в день: {n}", reply_markup=MAIN_MENU)
    if text == "Изменить кол-во приёмов пищи":
        user_states[uid] = {"mode":"set_feedings","step":0,"data":{}}
        return await update.message.reply_text("Сколько приёмов пищи в день?", reply_markup=MAIN_MENU)

    # Резервная копия
    if text == "📦 Резервная копия":
        for fn in (LOG_FILE, SETTINGS_FILE, COMMANDS_FILE):
            if os.path.exists(fn):
                await context.bot.send_document(update.effective_chat.id, open(fn, "rb"))
        return


    # Фоллбек
    await update.message.reply_text("Выберите действие из меню.",reply_markup=MAIN_MENU)

def main():
    if not BOT_TOKEN or not ALLOWED_USER_IDS:
        print("❌ TELEGRAM_BOT_TOKEN и ALLOWED_USER_IDS в .env")
        return
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,handle_message))
    jq = app.job_queue
    jq.run_daily(send_backup, time=time(hour=23,minute=59))
    # Расписание напоминаний
    sched=settings["schedule"]
    for _,tstr in sched.items():
        hh,mm=map(int,tstr.split(":"))
        mt=datetime.combine(date.today(),time(hh,mm))
        jq.run_daily(send_eat_reminder,time=(mt-timedelta(minutes=5)).time())
        jq.run_daily(send_walk_reminder,time=(mt-timedelta(hours=1,minutes=10)).time())
    print("✅ Bonita_Kani_Korso запущен")
    app.run_polling()

if __name__=="__main__":
    main()
