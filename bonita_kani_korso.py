#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bonita_Kani_Korso — Telegram-бот для режима щенка:
- Сон (засыпание/пробуждение)
- Действия: Еда, Игры, Прогулка, Био-прогулка
- Постфактум-добавление с указанием начала и конца
- Ручная длительность прогулок
- Редактирование/удаление последних 10 записей по действию
- Статистика за 2/5/10 дней:
    • Еда — по расписанию: завтрак, обед, ужин, поздний ужин
    • Сон, Игры, Прогулка — «действие до приёма пищи» с подсчётом количества и средней длительности
- Интерактивный вывод последних 2/5/10/15 записей
- Настройки:
    • Пользовательское расписание приёма пищи
    • Количество приёмов пищи в день
- Напоминания:
    • Еда — за 5 мин до приёма
    • Прогулка — за 1 ч 10 мин до приёма
    • Био-выход — через 4 мин после регистрации «Еда»
- Био-прогулка присутствует в меню и в истории
- CRUD выученных команд: просмотр, добавление, редактирование, удаление
- Ежедневные бэкапы (23:59), ротация логов (>10 МБ), очистка старше 120 дней
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
ALL_ACTIONS     = ["Сон","Еда","Игры","Прогулка","Био-прогулка"]
VALID_ACTIONS   = [("🛌","Сон"),("🍽️","Еда"),("🌿","Игры"),("🌳","Прогулка"),("🧻","Био-прогулка")]
EMOJI_BY_ACTION = {
    "Сон":"🛌","Еда":"🍽️","Игры":"🌿",
    "Прогулка":"🌳","Био-прогулка":"🧻"
}

# === Меню ===
CANCEL = "❌ Отмена"
MAIN_MENU = ReplyKeyboardMarkup([
    ["🛌 Сон",   "➕ Добавить вручную",  "✏️ Редактировать"],
    ["🍽️ Еда",  "🌿 Игры",             "⚙️ Настройки"],
    ["🌳 Прогулка","🧻 Био-прогулка",    "📊 Статистика"],
    ["🕓 Последние","💬 Команды",        "📦 Резервная копия"],
    [KeyboardButton(CANCEL)]
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

CMD_MENU = ReplyKeyboardMarkup([
    [KeyboardButton("Просмотр"), KeyboardButton("Добавить")],
    [KeyboardButton("Редактировать"), KeyboardButton("Удалить")],
    [KeyboardButton(CANCEL)]
], resize_keyboard=True)

SETTINGS_MENU = ReplyKeyboardMarkup([
    [KeyboardButton("Изменить расписание"), KeyboardButton("Изменить кол-во приёмов пищи")],
    [KeyboardButton(CANCEL)]
], resize_keyboard=True)

# === Настройки по умолчанию ===
default_settings = {
    "feedings_per_day": 4,
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
            with open(fn,"r",encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return default
    return default

def save_data(fn, data):
    with open(fn,"w",encoding="utf-8") as f:
        json.dump(data,f,ensure_ascii=False,indent=2)

settings = load_data(SETTINGS_FILE, default_settings)
commands = load_data(COMMANDS_FILE, [])

def trim_old(records, days=120):
    cutoff = datetime.now() - timedelta(days=days)
    return [e for e in records
            if datetime.strptime(e["time"],"%Y-%m-%d %H:%M:%S") >= cutoff]

def check_rotation():
    if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 10*1024*1024:
        log = load_data(LOG_FILE,[])
        save_data(LOG_FILE, trim_old(log,days=20))

# === Состояния пользователя ===
user_states   = {}  # user_id -> {"mode","step","data"}
active_walks  = {}  # user_id -> {"start": str}
active_sleeps = {}  # user_id -> {"start": str}

# === Утилиты ===
def average_time(times):
    if not times:
        return "—"
    total = sum(dt.hour*60+dt.minute
                for dt in (datetime.strptime(t,"%Y-%m-%d %H:%M:%S") for t in times))
    avg = total//len(times)
    return f"{avg//60:02d}:{avg%60:02d}"

def average_duration(mins_list):
    if not mins_list:
        return "—"
    avg = sum(mins_list)/len(mins_list)
    h = int(avg)//60
    m = int(avg)%60
    return f"{h}ч {m}м"

def list_last_entries(log, action, limit=10):
    ents = [e for e in log if e["action"]==action]
    ents.sort(key=lambda e:e["time"],reverse=True)
    return ents[:limit]

def extract_durations(log, action):
    """Возвращает list of duration_minutes до приёма."""
    ents = [e for e in log if e["action"]==action and e.get("note") in ("start","end")]
    ents.sort(key=lambda e:e["time"])
    durations = []
    start = None
    for e in ents:
        if e["note"]=="start":
            start = datetime.strptime(e["time"],"%Y-%m-%d %H:%M:%S")
        elif e["note"]=="end" and start:
            end = datetime.strptime(e["time"],"%Y-%m-%d %H:%M:%S")
            durations.append(int((end-start).total_seconds()//60))
            start = None
    return durations

# === Статистика ===
def get_stats(log, days=2):
    cutoff = datetime.now()-timedelta(days=days)
    entries = [e for e in log
               if datetime.strptime(e["time"],"%Y-%m-%d %H:%M:%S")>=cutoff]
    sched = settings["schedule"]
    sch_t = {k: datetime.strptime(v,"%H:%M").time() for k,v in sched.items()}
    periods=[("breakfast","Завтрак"),("lunch","Обед"),
             ("dinner","Ужин"),("late_dinner","Поздний ужин")]
    lines=[f"📊 Статистика за {days} дней:"]

    # Еда
    food={p:[] for p,_ in periods}
    for e in entries:
        if e["action"]!="Еда": continue
        t=datetime.strptime(e["time"],"%Y-%m-%d %H:%M:%S").time()
        if   t<sch_t["lunch"]:       food["breakfast"].append(e["time"])
        elif t<sch_t["dinner"]:      food["lunch"].append(e["time"])
        elif t<sch_t["late_dinner"]: food["dinner"].append(e["time"])
        else:                        food["late_dinner"].append(e["time"])
    lines.append("\n🍽️ Еда:")
    for p,label in periods:
        times=food[p]
        lines.append(f"  • {label}: {len(times)} раз, ср. в {average_time(times)}")

    # Сон и Прогулка — до приёма: кол-во и ср. длительность
    for act,emoji in [("Сон","🛌"),("Прогулка","🌳")]:
        durs=extract_durations(entries,act)
        grp={p:[] for p,_ in periods}
        for mins, e in zip(durs, durs):
            # use end times from log for grouping
            pass
        # Instead grouping by durations list order matching end times:
        end_times=[datetime.strptime(e["time"],"%Y-%m-%d %H:%M:%S").time()
                   for e in log if e["action"]==act and e.get("note")=="end"]
        for dt_end,mins in zip(end_times,durs):
            if   dt_end<sch_t["lunch"]:       grp["breakfast"].append(mins)
            elif dt_end<sch_t["dinner"]:      grp["lunch"].append(mins)
            elif dt_end<sch_t["late_dinner"]: grp["dinner"].append(mins)
            else:                             grp["late_dinner"].append(mins)
        lines.append(f"\n{emoji} {act}-до-приёма:")
        for p,label in periods:
            arr=grp[p]
            lines.append(f"  • {label}: {len(arr)} раз, ср. длительность {average_duration(arr)}")

    # Игры — до приёма: кол-во
    play={p:[] for p,_ in periods}
    for e in entries:
        if e["action"]!="Игры": continue
        t=datetime.strptime(e["time"],"%Y-%m-%d %H:%M:%S").time()
        if   t<sch_t["lunch"]:       play["breakfast"].append(e["time"])
        elif t<sch_t["dinner"]:      play["lunch"].append(e["time"])
        elif t<sch_t["late_dinner"]: play["dinner"].append(e["time"])
        else:                        play["late_dinner"].append(e["time"])
    lines.append("\n🌿 Игры-до-приёма:")
    for p,label in periods:
        times=play[p]
        lines.append(f"  • {label}: {len(times)} раз, ср. в {average_time(times)}")

    return "\n".join(lines)

# === Напоминания ===
async def send_backup(context:ContextTypes.DEFAULT_TYPE):
    for uid in ALLOWED_USER_IDS:
        for fn in (LOG_FILE,SETTINGS_FILE,COMMANDS_FILE):
            if os.path.exists(fn):
                await context.bot.send_document(chat_id=uid,
                                               document=open(fn,"rb"),
                                               caption="📦 Ежедневная копия")

async def send_eat_reminder(context:ContextTypes.DEFAULT_TYPE):
    for uid in ALLOWED_USER_IDS:
        await context.bot.send_message(chat_id=uid,
                                       text="🍽️ Напоминание: через 5 мин — приём пищи.")

async def send_walk_reminder(context:ContextTypes.DEFAULT_TYPE):
    for uid in ALLOWED_USER_IDS:
        await context.bot.send_message(chat_id=uid,
                                       text="🚶 Напоминание: через 1 ч 10 мин — прогулка.")

async def send_bio_reminder(context:ContextTypes.DEFAULT_TYPE):
    uid=context.job.data["user_id"]
    await context.bot.send_message(chat_id=uid,
                                   text="🧻 Напоминание: био-выход через 4 мин после еды.")

# === Обработчики ===
async def start(update:Update, context:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if uid not in ALLOWED_USER_IDS:
        return await update.message.reply_text("⛔️ Доступ запрещён.")
    await update.message.reply_text("Привет! Я слежу за режимом щенка 🐶",
                                    reply_markup=MAIN_MENU)

async def handle_message(update:Update, context:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if uid not in ALLOWED_USER_IDS:
        return await update.message.reply_text("⛔️ Доступ запрещён.")
    text=update.message.text.strip()
    now_str=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log=load_data(LOG_FILE,[])

    # ❌ Отмена
    if text==CANCEL and uid in user_states:
        user_states.pop(uid)
        return await update.message.reply_text("❌ Отмена.",reply_markup=MAIN_MENU)

    # 🕓 Последние записи
    if text=="🕓 Последние":
        user_states[uid]={"mode":"last","step":0,"data":{}}
        kb=[[KeyboardButton(a)] for a in ALL_ACTIONS]+[[KeyboardButton(CANCEL)]]
        return await update.message.reply_text("Выберите действие:",
            reply_markup=ReplyKeyboardMarkup(kb,resize_keyboard=True))
    if uid in user_states and user_states[uid]["mode"]=="last":
        st=user_states[uid]; step=st["step"]; data=st["data"]
        if step==0:
            if text not in ALL_ACTIONS:
                return await update.message.reply_text("❌ Отмена.",reply_markup=MAIN_MENU)
            data["action"]=text; st["step"]=1
            return await update.message.reply_text("Сколько записей показать?",reply_markup=LAST_CHOICES)
        if step==1:
            if text not in ("2","5","10","15"):
                return await update.message.reply_text("❌ Отмена.",reply_markup=MAIN_MENU)
            n=int(text)
            ents=list_last_entries(log,data["action"],limit=n)
            user_states.pop(uid)
            if not ents:
                return await update.message.reply_text("Нет записей.",reply_markup=MAIN_MENU)
            lines=[f"Последние {n} для {data['action']}:"]
            for e in ents:
                lines.append(f"{e['time']} {e.get('note','')}")
            return await update.message.reply_text("\n".join(lines),reply_markup=MAIN_MENU)

    # 📊 Статистика
    if text=="📊 Статистика":
        return await update.message.reply_text("Выберите период:",reply_markup=STATS_CHOICES)
    if text in ("2 дня","5 дней","10 дней"):
        days=int(text.split()[0])
        return await update.message.reply_text(get_stats(log,days),reply_markup=MAIN_MENU)

    # 💬 Команды CRUD
    if text=="💬 Команды":
        user_states[uid]={"mode":"cmd","step":0,"data":{}}
        return await update.message.reply_text("Выберите:",reply_markup=CMD_MENU)
    if uid in user_states and user_states[uid]["mode"]=="cmd":
        st=user_states[uid]; step=st["step"]; data=st["data"]
        # Шаг 0: выбор операции
        if step==0:
            if text=="Просмотр":
                user_states.pop(uid)
                if not commands:
                    return await update.message.reply_text("Нет выученных команд.",reply_markup=MAIN_MENU)
                lines=["📚 Выученные команды:"]
                for cmd in commands:
                    lines.append(f"/{cmd['command']}: {cmd['description']}")
                return await update.message.reply_text("\n".join(lines),reply_markup=MAIN_MENU)
            if text in ("Добавить","Редактировать","Удалить"):
                data["op"]=text; st["step"]=1
                return await update.message.reply_text("Введите имя команды (без /):")
            return await update.message.reply_text("❌ Отмена.",reply_markup=MAIN_MENU)
        # Шаг 1: имя команды
        if step==1:
            name=text.strip()
            data["name"]=name
            op=data["op"]
            if op=="Добавить":
                st["step"]=2
                return await update.message.reply_text("Введите описание команды:")
            # для редакт./удал.
            found=[c for c in commands if c["command"]==name]
            if not found:
                user_states.pop(uid)
                return await update.message.reply_text("Команда не найдена.",reply_markup=MAIN_MENU)
            data["cmd"]=found[0]
            if op=="Удалить":
                commands.remove(found[0]); save_data(COMMANDS_FILE,commands)
                user_states.pop(uid)
                return await update.message.reply_text("✅ Команда удалена.",reply_markup=MAIN_MENU)
            # Редактировать
            st["step"]=2
            return await update.message.reply_text("Введите новое описание:")
        # Шаг 2: описание
        if step==2:
            desc=text.strip()
            op=data["op"]
            if op=="Добавить":
                commands.append({"command":data["name"],"description":desc})
                save_data(COMMANDS_FILE,commands)
                res="✅ Команда добавлена."
            else:  # Редактировать
                data["cmd"]["description"]=desc
                save_data(COMMANDS_FILE,commands)
                res="✅ Команда обновлена."
            user_states.pop(uid)
            return await update.message.reply_text(res,reply_markup=MAIN_MENU)

    # ⚙️ Настройки
    if text=="⚙️ Настройки":
        return await update.message.reply_text("Выберите опцию:",reply_markup=SETTINGS_MENU)

    # Изменить расписание
    if uid in user_states and user_states[uid]["mode"]=="schedule":
        st=user_states[uid]; step=st["step"]; data=st["data"]
        times=["breakfast","lunch","dinner","late_dinner"]
        labels={"breakfast":"завтрак","lunch":"обед","dinner":"ужин","late_dinner":"поздний ужин"}
        try: datetime.strptime(text,"%H:%M")
        except:
            return await update.message.reply_text("Неверный формат ЧЧ:ММ")
        data[times[step]]=text; step+=1
        if step<4:
            st["step"]=step
            return await update.message.reply_text(f"Введите время {labels[times[step]]} (ЧЧ:ММ):")
        settings["schedule"]=data; save_data(SETTINGS_FILE,settings)
        user_states.pop(uid)
        return await update.message.reply_text("✅ Расписание обновлено!",reply_markup=MAIN_MENU)
    if text=="Изменить расписание":
        sched=settings["schedule"]
        msg="\n".join(f"{k}: {v}" for k,v in sched.items())
        user_states[uid]={"mode":"schedule","step":0,"data":{}}
        return await update.message.reply_text(f"Текущее расписание:\n{msg}\n\nВремя завтрака (ЧЧ:ММ):")

    # Изменить кол-во приемов пищи
    if uid in user_states and user_states[uid]["mode"]=="set_feedings":
        try: n=int(text)
        except:
            return await update.message.reply_text("Нужно число.",reply_markup=MAIN_MENU)
        settings["feedings_per_day"]=n; save_data(SETTINGS_FILE,settings)
        user_states.pop(uid)
        return await update.message.reply_text(f"✅ Приёмов пищи в день: {n}",reply_markup=MAIN_MENU)
    if text=="Изменить кол-во приёмов пищи":
        user_states[uid]={"mode":"set_feedings","step":0}
        return await update.message.reply_text("Сколько приемов пищи в день?",reply_markup=MAIN_MENU)

    # Постфактум-добавление
    if uid in user_states and user_states[uid]["mode"]=="postfact":
        st=user_states[uid]; step=st["step"]; data=st["data"]
        if step==0:
            if text not in ALL_ACTIONS:
                return await update.message.reply_text("Неверное действие.",reply_markup=MAIN_MENU)
            data["action"]=text; st["step"]=1
            return await update.message.reply_text("Введите время начала (ДД.MM.YYYY ЧЧ:ММ):",
                reply_markup=ReplyKeyboardMarkup([[KeyboardButton(CANCEL)]],resize_keyboard=True))
        if step==1:
            try: dt0=datetime.strptime(text,"%d.%m.%Y %H:%M")
            except:
                return await update.message.reply_text("Неверный формат.")
            data["start"]=dt0; act=data["action"]
            if act=="Сон":
                st["step"]=2
                return await update.message.reply_text("Введите время конца (ДД.MM.YYYY ЧЧ:ММ):",
                    reply_markup=ReplyKeyboardMarkup([[KeyboardButton(CANCEL)]],resize_keyboard=True))
            if act in ("Прогулка","Био-прогулка"):
                st["step"]=3
                return await update.message.reply_text("Длительность (мин):",
                    reply_markup=ReplyKeyboardMarkup([[KeyboardButton(CANCEL)]],resize_keyboard=True))
            log.append({"action":act,"time":dt0.strftime("%Y-%m-%d %H:%M:%S"),"user":uid})
            save_data(LOG_FILE,trim_old(log))
            user_states.pop(uid)
            return await update.message.reply_text("✅ Записано.",reply_markup=MAIN_MENU)
        if step==2:
            try: dt1=datetime.strptime(text,"%d.%m.%Y %H:%M")
            except:
                return await update.message.reply_text("Неверный формат.")
            dt0=data["start"]
            log.extend([
                {"action":"Сон","time":dt0.strftime("%Y-%m-%d %H:%M:%S"),"user":uid,"note":"start"},
                {"action":"Сон","time":dt1.strftime("%Y-%m-%d %H:%M:%S"),"user":uid,"note":"end"},
            ])
            save_data(LOG_FILE,trim_old(log))
            user_states.pop(uid)
            return await update.message.reply_text("✅ Сон записан.",reply_markup=MAIN_MENU)
        if step==3:
            try: mins=int(text)
            except:
                return await update.message.reply_text("Нужно число.")
            dt0=data["start"]; dt1=dt0+timedelta(minutes=mins)
            log.extend([
                {"action":data["action"],"time":dt0.strftime("%Y-%m-%d %H:%M:%S"),"user":uid,"note":"start"},
                {"action":data["action"],"time":dt1.strftime("%Y-%m-%d %H:%M:%S"),"user":uid,"note":"end"},
            ])
            save_data(LOG_FILE,trim_old(log))
            user_states.pop(uid)
            return await update.message.reply_text("✅ Записано.",reply_markup=MAIN_MENU)

    if text=="➕ Добавить вручную":
        user_states[uid]={"mode":"postfact","step":0,"data":{}}
        kb=[[KeyboardButton(a)] for a in ALL_ACTIONS]+[[KeyboardButton(CANCEL)]]
        return await update.message.reply_text("Выберите действие:",reply_markup=ReplyKeyboardMarkup(kb,resize_keyboard=True))

    # Прямые кнопки для Сон/Прогулка/Еда/Игры/Био-прогулка
    for emo,act in VALID_ACTIONS:
        if text==f"{emo} {act}":
            check_rotation()
            log.append({"action":act,"time":now_str,"user":uid})
            save_data(LOG_FILE,trim_old(log))
            if act=="Еда":
                context.job_queue.run_once(send_bio_reminder,when=4*60,data={"user_id":uid})
            return await update.message.reply_text(f"{emo} {act} записано.",reply_markup=MAIN_MENU)

    # 📦 Резервная копия
    if text=="📦 Резервная копия":
        for fn in (LOG_FILE,SETTINGS_FILE,COMMANDS_FILE):
            if os.path.exists(fn):
                await context.bot.send_document(update.effective_chat.id,open(fn,"rb"))
        return

    await update.message.reply_text("Выберите действие из меню.",reply_markup=MAIN_MENU)

# === Точка входа ===
def main():
    if not BOT_TOKEN or not ALLOWED_USER_IDS:
        print("❌ TELEGRAM_BOT_TOKEN и ALLOWED_USER_IDS должны быть в .env")
        return
    app=ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,handle_message))
    jq=app.job_queue
    jq.run_daily(send_backup,time=time(hour=23,minute=59))
    # Планирование напоминаний
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
