# setup.sh — Установка Bonita_Kani_Korso на Termux

#!/data/data/com.termux/files/usr/bin/bash

# 1. Обновляем систему
pkg update -y && pkg upgrade -y

# 2. Устанавливаем Python и git
pkg install -y python git

# 3. Устанавливаем pip-библиотеки
pip install --upgrade pip
pip install python-telegram-bot apscheduler python-dotenv

# 4. Создаём .env с токеном и id
echo "Введите Telegram Bot Token:"
read TOKEN

echo "Введите список разрешённых user_id через запятую (например: 12345678,87654321):"
read IDS

echo "TELEGRAM_BOT_TOKEN=$TOKEN" > .env
echo "ALLOWED_USER_IDS=$IDS" >> .env

echo "\nФайл .env создан!"

# 5. Включаем wake-lock (бот не засыпает)
termux-wake-lock

# 6. Запускаем бота
echo "\nЗапускаем Bonita_Kani_Korso..."
python bonita_kani_korso.py