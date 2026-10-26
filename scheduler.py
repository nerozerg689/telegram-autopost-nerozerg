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
  album: список ссылок на картинки (альбом до 10 штук); подпись берётся из text.
  followup_text: отдельное сообщение, которое уходит сразу после поста (необязательно).

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
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tg_post import API, CAPTION_LIMIT, MESSAGE_LIMIT, call

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


def call_multi(token, method, fields, files):
    """Запрос с несколькими файлами (multipart). files: {имя_поля: путь}."""
    boundary = uuid.uuid4().hex
    body = bytearray()
    for key, value in fields.items():
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n').encode()
    for name, path in files.items():
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as f:
            data = f.read()
        body += (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
            f'filename="{os.path.basename(path)}"\r\nContent-Type: {ctype}\r\n\r\n'
        ).encode()
        body += data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        API.format(token=token, method=method),
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            return json.load(e)
        except Exception:
            return {"ok": False, "description": f"HTTP {e.code}"}
    except urllib.error.URLError as e:
        return {"ok": False, "description": f"Нет соединения: {e.reason}"}


def send_album(token, chat_id, post):
    """Альбом из картинок: сначала по ссылкам, при неудаче скачиваем и грузим файлами."""
    urls = post["album"]
    caption = get_text(post)
    items = []
    for i, m in enumerate(urls):
        item = {"type": "photo", "media": m}
        if i == 0:
            item.update(caption=caption, parse_mode="HTML")
        items.append(item)
    res = call(token, "sendMediaGroup", {"chat_id": chat_id, "media": items})
    if res.get("ok"):
        return res

    tmp, files, items2 = [], {}, []
    try:
        for i, m in enumerate(urls):
            path, is_tmp = local_copy(m)
            if is_tmp:
                tmp.append(path)
            files[f"file{i}"] = path
            item = {"type": "photo", "media": f"attach://file{i}"}
            if i == 0:
                item.update(caption=caption, parse_mode="HTML")
            items2.append(item)
        return call_multi(token, "sendMediaGroup",
                          {"chat_id": chat_id, "media": json.dumps(items2, ensure_ascii=False)}, files)
    except Exception as e:
        return {"ok": False, "description": f"Альбом не отправлен: {res.get('description')}; запасной способ: {e}"}
    finally:
        for p in tmp:
            if os.path.exists(p):
                os.remove(p)


def first_message_id(res):
    r = res["result"]
    return r[0]["message_id"] if isinstance(r, list) else r["message_id"]


def send(token, chat_id, post):
    if post.get("album"):
        res = send_album(token, chat_id, post)
        if res.get("ok") and post.get("followup_text"):
            extra = call(token, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML", "text": post["followup_text"].strip()})
            if not extra.get("ok"):
                post["followup_error"] = extra.get("description", str(extra))
                print(f"[внимание] {post['id']}: альбом отправлен, но второе сообщение нет: {post['followup_error']}", file=sys.stderr)
        return res

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
                post["message_id"] = first_message_id(res)
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
