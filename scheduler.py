#!/usr/bin/env python3
"""Отправляет в Telegram-канал посты из queue.json, время которых пришло.

Запускается GitHub Actions каждый час. Пост уходит один раз: после успешной
отправки его статус меняется на "sent". Если пост опоздал больше чем на сутки,
он помечается "missed" и не отправляется.

Запуск вручную (проверка без отправки):
  python3 scheduler.py --dry-run
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tg_post import CAPTION_LIMIT, MESSAGE_LIMIT, call

TZ = ZoneInfo("Europe/Berlin")  # время во Франкфурте
QUEUE = "queue.json"
MAX_LATE = timedelta(hours=24)


def send(token, chat_id, post):
    """Отправляет один пост. Возвращает ответ Telegram."""
    with open(post["text_file"], encoding="utf-8") as f:
        text = f.read().strip()
    if len(text) > MESSAGE_LIMIT:
        return {"ok": False, "description": f"Текст длиннее {MESSAGE_LIMIT} символов"}

    photo = post.get("photo")
    base = {"chat_id": chat_id, "parse_mode": "HTML"}

    if photo and len(text) <= CAPTION_LIMIT:
        fields = {**base, "caption": text}
        if photo.startswith("http"):
            return call(token, "sendPhoto", {**fields, "photo": photo})
        return call(token, "sendPhoto", fields, "photo", photo)

    if photo:  # подпись слишком длинная: фото и текст отдельно
        if photo.startswith("http"):
            res = call(token, "sendPhoto", {"chat_id": chat_id, "photo": photo})
        else:
            res = call(token, "sendPhoto", {"chat_id": chat_id}, "photo", photo)
        if not res.get("ok"):
            return res

    return call(token, "sendMessage", {**base, "text": text})


def save(queue):
    with open(QUEUE, "w", encoding="utf-8") as f:
        json.dump(queue, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="ничего не отправлять")
    args = parser.parse_args()

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not args.dry_run and (not token or not chat_id):
        print("Не заданы TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID.", file=sys.stderr)
        sys.exit(1)

    with open(QUEUE, encoding="utf-8") as f:
        queue = json.load(f)

    now = datetime.now(TZ)
    failed = False
    print(f"Сейчас: {now:%Y-%m-%d %H:%M} (Europe/Berlin)")

    for post in queue:
        if post.get("status", "pending") != "pending":
            continue
        when = datetime.fromisoformat(post["when"]).replace(tzinfo=TZ)
        if when > now:
            continue

        if now - when > MAX_LATE:
            print(f"[пропущен] {post['id']}: опоздал больше чем на сутки")
            if not args.dry_run:
                post["status"] = "missed"
                save(queue)
            continue

        if args.dry_run:
            print(f"[отправил бы] {post['id']} ({post['when']})")
            continue

        res = send(token, chat_id, post)
        if res.get("ok"):
            post["status"] = "sent"
            post["sent_at"] = now.isoformat(timespec="seconds")
            post["message_id"] = res["result"]["message_id"]
            post.pop("last_error", None)
            print(f"[отправлен] {post['id']}, message_id={post['message_id']}")
        else:
            post["last_error"] = res.get("description", str(res))
            print(f"[ошибка] {post['id']}: {post['last_error']}", file=sys.stderr)
            failed = True
        save(queue)  # сохраняем сразу, чтобы не отправить дважды

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
