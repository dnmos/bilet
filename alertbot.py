import requests
from bs4 import BeautifulSoup
import telegram
import asyncio
import aiohttp
import hashlib
import datetime
import logging
import pytz  # Для работы с часовыми поясами
import os
from dotenv import load_dotenv  # Импортируем load_dotenv
import json  # Импортируем json
import pkg_resources
import schedule

# --- Настройки ---
load_dotenv()  # Загружаем переменные окружения из .env

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")  # Замените на токен вашего бота
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")  # Замените на ID чата из .env
URLS_TO_MONITOR_STR = os.getenv("URLS_TO_MONITOR")  # Замените на ваши URL
URLS_TO_MONITOR = json.loads(URLS_TO_MONITOR_STR) if URLS_TO_MONITOR_STR else []
CHECK_INTERVAL_SECONDS = 300  # 5 минут
NO_CHANGE_NOTIFICATION_TIMES = ["12:00", "18:00"]  # Время для уведомлений об отсутствии изменений (по Москве)

HASH_FILE = "page_hashes.json"  # Файл для хранения хешей

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Выводим версию BeautifulSoup4
try:
    version = pkg_resources.get_distribution("beautifulsoup4").version
    logging.info(f"Версия beautifulsoup4: {version}")
except pkg_resources.DistributionNotFound:
    logging.warning("Не удалось определить версию beautifulsoup4.")

# -----------------

bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN)
previous_hashes = {}  # Словарь для хранения предыдущих хешей страниц

def calculate_hash(text):
    """Вычисляет SHA-256 хеш строки."""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


async def check_page(url, session):
    """Асинхронно проверяет страницу и возвращает текст контента и хеш контента."""
    try:
        async with session.get(url) as response:
            response.raise_for_status()  # Raises HTTPError for bad responses (4xx, 5xx)
            html = await response.text()
            soup = BeautifulSoup(html, 'html.parser')
            page_text = soup.get_text()
            page_hash = calculate_hash(page_text)
            return page_text, page_hash

    except aiohttp.ClientError as e:
        logging.error(f"Ошибка при запросе {url}: {e}")
        return None, None  # Ошибка при запросе, возвращаем None


async def send_telegram_message(message):
    """Асинхронно отправляет сообщение в Telegram."""
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
        logging.info(f"Сообщение отправлено в Telegram: {message}")
    except Exception as e:
        logging.error(f"Ошибка при отправке сообщения в Telegram: {e}")


def load_hashes():
    """Загружает хеши из файла."""
    try:
        with open(HASH_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        logging.warning("Ошибка при чтении файла хешей. Использованы пустые значения.")
        return {}


def save_hashes(hashes):
    """Сохраняет хеши в файл."""
    try:
        with open(HASH_FILE, "w") as f:
            json.dump(hashes, f)
    except Exception as e:
        logging.error(f"Ошибка при записи в файл хешей: {e}")


async def check_all_pages():
    """Асинхронно проверяет все страницы и отправляет уведомления при изменениях."""
    try:
        async with aiohttp.ClientSession() as session:
            for url in URLS_TO_MONITOR:
                try:
                    page_text, current_hash = await check_page(url, session)

                    if page_text is None:
                        logging.warning(f"Пропущена проверка {url} из-за ошибки.")
                        continue

                    if url not in previous_hashes:
                        logging.info(f"Первая проверка {url}, хеш сохранен.")
                        previous_hashes[url] = current_hash # Инициализация начального состояния
                        save_hashes(previous_hashes) # Сохраняем после первого запуска

                    if "Билеты появятся позже" not in page_text:
                        await send_telegram_message(f"Внимание! На странице {url} билеты, возможно, появились!")
                        previous_hashes[url] = current_hash # Обновляем состояние
                        save_hashes(previous_hashes) # Сохраняем после каждого изменения

                    elif url in previous_hashes and current_hash != previous_hashes[url]:
                        logging.info(f"Хеш изменился для {url}:")
                        logging.info(f"Старый хеш: {previous_hashes[url]}")
                        logging.info(f"Новый хеш: {current_hash}")
                        await send_telegram_message(f"Внимание! На странице {url} произошли изменения (хеш изменился), но билеты все еще 'появятся позже'.")
                        previous_hashes[url] = current_hash # Обновляем состояние
                        save_hashes(previous_hashes) # Сохраняем после каждого изменения

                    else:
                        logging.info(f"Изменений на {url} не обнаружено.")

                except Exception as e:
                    logging.exception(f"Непредвиденная ошибка при обработке {url}: {e}")
    except Exception as e:
        logging.exception(f"Произошла ошибка при создании aiohttp.ClientSession: {e}")


async def send_no_change_notification():
    """Асинхронно отправляет уведомление об отсутствии изменений."""
    try:
        await send_telegram_message("Ежедневное уведомление: Изменений на отслеживаемых страницах не обнаружено.")
    except Exception as e:
        logging.error(f"Ошибка при отправке сообщения об отсутствии изменений: {e}")

# --- Основной цикл (БЕЗ CRON, с systemd) ---
async def main():
    import json  # Импортируем json здесь, чтобы он был доступен в __main__

    # Проверяем, существует ли файл
    if not os.path.exists(HASH_FILE):
        # Если файл не существует, то выполняем первоначальный сбор хешей
        logging.info("Файл page_hashes.json не найден.  Выполняем первоначальный сбор хешей.")
        previous_hashes = {}
        async with aiohttp.ClientSession() as session:
            for url in URLS_TO_MONITOR:
                try:
                    page_text, current_hash = await check_page(url, session)
                    if page_text is not None:  # Проверяем, что запрос не вызвал ошибку
                        previous_hashes[url] = current_hash
                        logging.info(f"Первоначальный хеш для {url} сохранен.")
                except Exception as e:
                    logging.exception(f"Ошибка при первоначальном сборе хешей для {url}: {e}")
        save_hashes(previous_hashes)  # Сохраняем хеши

    else:
        # Если файл существует, загружаем хеши
        previous_hashes = load_hashes()  # Загружаем хеши при старте
        logging.info("Файл page_hashes.json найден и загружен.")

    logging.info("Мониторинг запущен...")

    moscow_tz = pytz.timezone('Europe/Moscow') # часовой пояс

    while True:
        try:
            # Выполняем проверку страниц
            await check_all_pages()

            # Проверяем, нужно ли отправлять ежедневное уведомление
            now = datetime.datetime.now(moscow_tz).time()
            for time_str in NO_CHANGE_NOTIFICATION_TIMES:
                time_obj = datetime.datetime.strptime(time_str, "%H:%M").time()
                if now.hour == time_obj.hour and now.minute == time_obj.minute:
                    await send_no_change_notification()

            await asyncio.sleep(CHECK_INTERVAL_SECONDS)  # Пауза

        except Exception as e:
            logging.exception(f"Произошла ошибка: {e}. Скрипт будет перезапущен через 60 секунд.")
            await asyncio.sleep(60)  # Пауза перед перезапуском

if __name__ == "__main__":
    asyncio.run(main())