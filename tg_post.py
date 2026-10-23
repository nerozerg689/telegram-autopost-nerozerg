#!/usr/bin/env python3
"""Публикация поста (фото + текст) в Telegram-канал через Bot API.

Настройка (один раз):
  export TELEGRAM_BOT_TOKEN="токен_от_BotFather"
  export TELEGRAM_CHAT_ID="@username_канала"

Проверка, что бот настроен правильно:
  python3 tg_post.py --check

Публикация:
  python3 tg_post.py --photo photo.jpg --text-file post_telegram.html

Текст отправляется в режиме HTML (<b>, <i>, <a href="...">).
"""
import argparse
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request
import uuid

API = "https://api.telegram.org/bot{token}/{method}"
CAPTION_LIMIT = 1024   # лимит подписи к фото
MESSAGE_LIMIT = 4096   # лимит обычного сообщения


def call(token, method, fields, file_field=None, file_path=None):
    """Вызов метода Bot API. Возвращает разобранный JSON-ответ."""
    url = API.format(token=token, method=method)
    if file_path:
        boundary = uuid.uuid4().hex
        body = bytearray()
        for key, value in fields.items():
            body += (
                f'--{boundary}\r\nContent-Disposition: form-data; '
                f'name="{key}"\r\n\r\n{value}\r\n'
            ).encode()
        ctype = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
        with open(file_path, "rb") as f:
            data = f.read()
        body += (
            f'--{boundary}\r\nContent-Disposition: form-data; '
            f'name="{file_field}"; filename="{os.path.basename(file_path)}"\r\n'
            f"Content-Type: {ctype}\r\n\r\n"
        ).encode()
        body += data + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(
            url,
            data=bytes(body),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
    else:
        req = urllib.request.Request(
            url,
            data=json.dumps(fields).encode(),
            headers={"Content-Type": "application/json"},
        )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            return json.load(e)
        except Exception:
            return {"ok": False, "description": f"HTTP {e.code}"}
    except urllib.error.URLError as e:
        return {"ok": False, "description": f"Нет соединения: {e.reason}"}


def fail(result):
    print("Ошибка Telegram:", result.get("description", result), file=sys.stderr)
    sys.exit(1)


def check(token, chat_id):
    me = call(token, "getMe", {})
    if not me.get("ok"):
        fail(me)
    bot = me["result"]
    print(f"Бот найден: @{bot['username']}")

    chat = call(token, "getChat", {"chat_id": chat_id})
    if not chat.get("ok"):
        fail(chat)
    print(f"Канал найден: {chat['result'].get('title', chat_id)}")

    member = call(token, "getChatMember", {"chat_id": chat_id, "user_id": bot["id"]})
    if not member.get("ok"):
        fail(member)
    info = member["result"]
    if info.get("status") not in ("administrator", "creator"):
        print("Бот не является администратором канала.", file=sys.stderr)
        sys.exit(1)
    if info.get("can_post_messages") is False:
        print("У бота нет права публиковать сообщения.", file=sys.stderr)
        sys.exit(1)
    print("Всё в порядке: бот может публиковать в канал.")


def publish(token, chat_id, photo, text):
    if len(text) > MESSAGE_LIMIT:
        print(f"Текст длиннее {MESSAGE_LIMIT} символов, сократите его.", file=sys.stderr)
        sys.exit(1)

    if photo and len(text) <= CAPTION_LIMIT:
        # Фото и текст одним сообщением
        res = call(
            token,
            "sendPhoto",
            {"chat_id": chat_id, "caption": text, "parse_mode": "HTML"},
            file_field="photo",
            file_path=photo,
        )
    elif photo:
        # Подпись слишком длинная: сначала фото, затем текст отдельно
        res = call(token, "sendPhoto", {"chat_id": chat_id}, "photo", photo)
        if not res.get("ok"):
            fail(res)
        res = call(token, "sendMessage", {"chat_id": chat_id, "text": text, "parse_mode": "HTML"})
    else:
        res = call(token, "sendMessage", {"chat_id": chat_id, "text": text, "parse_mode": "HTML"})

    if not res.get("ok"):
        fail(res)
    print("Опубликовано. message_id:", res["result"]["message_id"])


def main():
    parser = argparse.ArgumentParser(description="Публикация в Telegram-канал")
    parser.add_argument("--photo", help="путь к картинке")
    parser.add_argument("--text-file", help="файл с текстом поста (HTML)")
    parser.add_argument("--text", help="текст поста строкой")
    parser.add_argument("--check", action="store_true", help="проверить настройку бота")
    args = parser.parse_args()

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Задайте TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID.", file=sys.stderr)
        sys.exit(1)

    if args.check:
        check(token, chat_id)
        return

    if args.text_file:
        with open(args.text_file, encoding="utf-8") as f:
            text = f.read().strip()
    elif args.text:
        text = args.text
    else:
        print("Укажите --text-file или --text.", file=sys.stderr)
        sys.exit(1)

    if args.photo and not os.path.isfile(args.photo):
        print(f"Файл не найден: {args.photo}", file=sys.stderr)
        sys.exit(1)

    publish(token, chat_id, args.photo, text)


if __name__ == "__main__":
    main()
