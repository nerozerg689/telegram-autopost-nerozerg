# Автопубликация в Telegram

Каждый час GitHub Actions запускает `scheduler.py`. Он смотрит `queue.json`
и отправляет в канал посты, время которых наступило (время указано по Франкфурту).

## Как добавить пост
1. Положи картинку в папку `images/`, текст (HTML для Telegram) в `posts/`.
2. Добавь запись в `queue.json`: `id`, `when` (`ГГГГ-ММ-ДДTЧЧ:ММ`), `photo`, `text_file`, `status: "pending"`.

## Секреты (Settings → Secrets and variables → Actions)
- `TELEGRAM_BOT_TOKEN`: токен бота от @BotFather
- `TELEGRAM_CHAT_ID`: `@username_канала`

## Проверка
Вкладка Actions → Publish to Telegram → Run workflow → отметь `dry_run`.
