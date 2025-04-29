# start.sh — Быстрый запуск Bonita_Kani_Korso

#!/data/data/com.termux/files/usr/bin/bash

# Не даём телефону усыплять сессию
termux-wake-lock

# Переход в папку, где лежит бот (если требуется, поправь путь)
cd "$(dirname $0)"

# Запуск бота
python bonita_kani_korso.py
