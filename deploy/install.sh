#!/usr/bin/env bash
# Установка и обновление бота на VPS (Ubuntu 22.04 / 24.04, Debian 12).
#   Первая установка:  git clone <репозиторий> ~/aibottrading && cd ~/aibottrading && sudo bash deploy/install.sh
#   Обновление:        cd ~/aibottrading && git pull && sudo bash deploy/install.sh
# Код копируется в /opt/aibottrading; ключи (.env) и история (data/) при обновлении сохраняются.
set -euo pipefail

APP=/opt/aibottrading
SRC="$(cd "$(dirname "$0")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then echo "Запустите через sudo"; exit 1; fi

echo "==> Системные пакеты"
apt-get update -q
apt-get install -y -q python3 python3-venv python3-pip git chrony

echo "==> Пользователь aibot"
id aibot &>/dev/null || useradd --system --create-home --shell /usr/sbin/nologin aibot

echo "==> Код в $APP"
if [[ "$SRC" != "$APP" ]]; then
  mkdir -p "$APP"
  # копируем код, не трогая .env и data/ на сервере
  tar -C "$SRC" --exclude=.git --exclude=.env --exclude=data --exclude=.venv -cf - . | tar -C "$APP" -xf -
fi
mkdir -p "$APP/data"

echo "==> Python-окружение"
python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -q --upgrade pip
"$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"

if [[ ! -f "$APP/.env" ]]; then
  cp "$APP/.env.example" "$APP/.env"
  NEED_ENV=1
fi
chown -R aibot:aibot "$APP"
chmod 600 "$APP/.env"

echo "==> Сервис systemd"
cp "$APP/deploy/aibot.service" /etc/systemd/system/aibot.service
systemctl daemon-reload
systemctl enable aibot >/dev/null

echo "==> Проверка тестами"
sudo -u aibot bash -c "cd $APP && .venv/bin/python -m pytest -q" || { echo "Тесты не прошли"; exit 1; }

if [[ "${NEED_ENV:-0}" == 1 ]]; then
  echo
  echo "Готово. Осталось заполнить ключи:"
  echo "  sudo nano $APP/.env"
  echo "и запустить бота:"
  echo "  sudo systemctl start aibot"
else
  systemctl restart aibot
  echo "Готово, бот перезапущен. Логи: sudo journalctl -u aibot -f"
fi
