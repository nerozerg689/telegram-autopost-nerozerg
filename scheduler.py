#!/usr/bin/env python3
"""Отправляет в Telegram-канал посты из очередей, время которых пришло.

Очереди: queue.json (старая) и все файлы queue/*.json (по одному на неделю).
Пост уходит один раз: после успешной отправки статус меняется на "sent".
Опоздавший больше чем на сутки пост получает статус "missed" и не отправляется.

Поля записи очереди:
  id, when ("ГГГГ-ММ-ДДTЧЧ:ММ" по Франкфурту), status ("pending"),
  text или text_file (HTML для Telegram),
  media: ссылка (https://...) или путь к файлу в репозитории (необязательно),
  media_type: "photo" или "video" (если не указан, определяется по расширению).

Проверка без отправки:
  python3 scheduler.py --dry-run
"""
import argparse
import glob
import json
import mimetypes
import os
import sys
import tempfile
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tg_post import CAPTION_LIMIT, MESSAGE_LIMIT, call

TZ = ZoneInfo("Europe/Berlin")  # время во Франкфурте
MAX_LATE = timedelta(hours=24)
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm")


def queue_files():
    files = []
    if os.path.isfile("queue.json"):
        files.append("queue.json")
    files += sorted(glob.glob("queue/*.json"))
    return files


def get_text(post):
    if post.get("text"):
        return post["text"].strip()
    with open(post["text_file"], encoding="utf-8") as f:
        return f.read().strip()


def local_copy(media):
    """Если media это ссылка, скачивает файл во временный. Возвращает (путь, временный?)."""
    if not media.startswith("http"):
        return media, False
    req = urllib.request.Request(media, headers={"User-Agent": "Mozilla/5.0"})
    ext = os.path.splitext(media.split("?")[0])[1] or ".bin"
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
    fd, path = tempfile.mkstemp(suffix=ext)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path, True


def send(token, chat_id, post):
    text = get_text(post)
    media = post.get("media")
    base = {"chat_id": chat_id, "parse_mode": "HTML"}

    if not media:
        if len(text) > MESSAGE_LIMIT:
            return {"ok": False, "description": f"Текст длиннее {MESSAGE_LIMIT} символов"}
        return call(token, "sendMessage", {**base, "text": text})

    kind = post.get("media_type")
    if not kind:
        ext = os.path.splitext(media.split("?")[0])[1].lower()
        kind = "video" if ext in VIDEO_EXT else "photo"

    try:
        path, is_tmp = local_copy(media)
    except Exception as e:  # сеть, 404 и т.п.
        return {"ok": False, "description": f"Не удалось скачать медиа: {e}"}

    try:
        caption_ok = len(text) <= CAPTION_LIMIT
        fields = {**base, "caption": text} if caption_ok else {"chat_id": chat_id}
        if kind == "video":
            fields["supports_streaming"] = "true"
            res = call(token, "sendVideo", fields, "video", path)
        else:
            res = call(token, "sendPhoto", fields, "photo", path)
            if not res.get("ok") and "PHOTO_" in res.get("description", "").upper():
                # неподходящие размеры для фото: отправляем как файл без сжатия
                res = call(token, "sendDocument", fields, "document", path)
        if res.get("ok") and not caption_ok:
            res = call(token, "sendMessage", {**base, "text": text})
        return res
    finally:
        if is_tmp and os.path.exists(path):
            os.remove(path)


def save(path, queue):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(queue, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="ничего не отправлять")
    parser.add_argument("--now", help="подменить текущее время (для проверки), ГГГГ-ММ-ДДTЧЧ:ММ")
    args = parser.parse_args()

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not args.dry_run and (not token or not chat_id):
        print("Не заданы TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID.", file=sys.stderr)
        sys.exit(1)

    now = datetime.now(TZ)
    if args.now:
        now = datetime.fromisoformat(args.now).replace(tzinfo=TZ)
    print(f"Сейчас: {now:%Y-%m-%d %H:%M} (Europe/Berlin)")

    failed = False
    for path in queue_files():
        with open(path, encoding="utf-8") as f:
            queue = json.load(f)

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
                    save(path, queue)
                continue

            if args.dry_run:
                print(f"[отправил бы] {post['id']} ({post['when']}) из {path}")
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
            save(path, queue)  # сразу, чтобы не отправить дважды

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
