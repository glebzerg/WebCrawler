# Асинхронный веб-краулер

Учебный проект на Python. Цель — загружать страницы конкурентно, извлекать данные из HTML и сохранять результаты обхода.

## Окружение в Windows PowerShell

Для проекта используем Python 3.12. Выполняй команды из корня проекта.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Если `py -3.12` не видит установленный Python, укажи полный путь к интерпретатору. Например, для стандартной установки 3.12 в профиль пользователя первая команда выглядит так:

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m venv .venv
```

`.venv` хранит окружение этого проекта. Команда `python.exe -m pip` запускает pip через тот же интерпретатор, которым мы будем запускать проект. Прямой путь к `python.exe` внутри `.venv` позволяет работать без активации окружения.

`requirements.txt` перечисляет зависимости проекта. `aiohttp` отправляет HTTP-запросы, BeautifulSoup4 и lxml разбирают HTML, а `aiofiles` пригодится для асинхронной работы с файлами.

## Проверка установки

```powershell
.\.venv\Scripts\python.exe -c "import sys, aiohttp, aiofiles, bs4, lxml; print(sys.version.split()[0]); print(sys.executable); print('All project dependencies imported')"
```

Первая строка показывает версию Python, вторая — путь к запущенному интерпретатору внутри `.venv`. Последняя строка появляется после успешного импорта всех библиотек.

## Локальные тесты

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
```

Тест поднимает сервер на `127.0.0.1` со свободным портом и делает запрос к `/ok`. Подключение к внешним сайтам для проверки не требуется.

## Демонстрация загрузки

```powershell
.\.venv\Scripts\python.exe -m examples.day1_downloads
```

Демонстрация загружает шесть локальных страниц с задержкой 0,2 секунды на ответ. Сначала краулер загружает по одной странице, затем до трёх одновременно. Скрипт выводит журнал, результат по каждому URL и время обоих запусков, после чего сравнивает загруженные страницы.

## Демонстрация разбора HTML

```powershell
.\.venv\Scripts\python.exe -m examples.day2_parsing
```

Краулер загружает две страницы с локального сервера и печатает JSON с заголовками, ссылками, длиной текста и числом изображений для каждой страницы.

## Демонстрация обхода сайта

```powershell
.\.venv\Scripts\python.exe -m examples.day3_crawl
```

Краулер начинает с одной локальной страницы и переходит по найденным ссылкам. В терминале видны текущие счётчики и скорость, а после обхода обновляется `results/day3-crawl.json` со страницами, ошибками и итоговой статистикой.

## Демонстрация соблюдения robots.txt

```powershell
.\.venv\Scripts\python.exe -m examples.day4_polite_crawl
```

Локальный сайт задаёт `Crawl-delay: 0.2` и запрещает `/private`. Демо показывает время каждого полученного сервером GET, средний интервал между запросами, запросы в секунду и число заблокированных адресов. Загрузка `robots.txt` входит в статистику HTTP-запросов.

## Конфигурация JSON

`load_config(path)` читает файл в UTF-8 и возвращает проверенный `CrawlerConfig`.

```json
{
  "start_urls": ["https://example.com"],
  "sitemap_urls": ["https://example.com/sitemap.xml"],
  "same_domain_only": true,
  "exclude_patterns": ["*/archive/*"],
  "max_pages": 50,
  "max_depth": 2,
  "max_concurrent": 5,
  "rate_limit": 2,
  "storage": {"format": "jsonl", "path": "results/pages.jsonl"}
}
```

- `start_urls` и `sitemap_urls` принимают массивы абсолютных HTTP(S)-адресов. `include_patterns` и `exclude_patterns` принимают шаблоны адресов, например `*/archive/*`. `same_domain_only` ограничивает обход хостами стартовых адресов и карт.
- `max_pages` по умолчанию равен 100, `max_concurrent` — 10. `max_depth` и `max_per_host` принимают `null`, если отдельный лимит не задан. `rate_limit` задаёт запросы в секунду, а `null` отключает этот лимит. `per_domain` включает отдельный лимит для каждого хоста.
- `respect_robots` по умолчанию равен `true`. Паузы `min_delay` и `jitter`, а также `connect_timeout`, `read_timeout` и `total_timeout` задаются в секундах. `max_redirects` и `max_retries` ограничивают редиректы и повторы HTTP-запросов.
- `storage` принимает `null` или объект с `path` и форматом `json`, `jsonl`, `csv` или `sqlite`. Для CSV можно указать `encoding`, для SQLite — `batch_size`. Относительный путь считается от рабочей папки запуска. `storage_queue_size` и `storage_max_retries` управляют очередью записи и числом повторов.

Для запуска из Python передай путь к файлу в `AdvancedCrawler.from_config()`.

```python
import asyncio

from crawler import AdvancedCrawler


async def main():
    crawler = AdvancedCrawler.from_config("config.json")
    try:
        await crawler.crawl()
    finally:
        await crawler.close()

    await crawler.export_to_json("results/stats.json")
    await crawler.export_to_html_report("results/report.html")
    print(crawler.get_stats()["saved_pages"])


asyncio.run(main())
```

`storage.path` задаёт файл с записями страниц. `export_to_json()` записывает снимок статистики в отдельный файл. Экспорт после `close()` включает итог записи в хранилище.

`export_to_html_report()` создаёт самостоятельный отчёт с таблицами и диаграммой HTTP-статусов. Файл `results/report.html` можно открыть в браузере на своём компьютере.

## Запуск из терминала

```powershell
.\.venv\Scripts\python.exe -m crawler --help
.\.venv\Scripts\python.exe crawler.py --config config.json --max-pages 2
.\.venv\Scripts\python.exe -m crawler --urls https://example.com --output results/pages.json
```

Обе команды используют те же настройки, что и `AdvancedCrawler`. Явно переданный флаг переопределяет соответствующее поле JSON. `--urls` заменяет список `start_urls`, а `--respect-robots` и `--no-respect-robots` задают режим для `robots.txt`.

Если в конфиге указано хранилище, `--output` меняет его путь. При запуске без настроенного хранилища формат определяется по расширению файла. Поддерживаются `.jsonl`, `.csv`, `.db`, `.sqlite` и `.sqlite3`. Для остальных расширений используется JSON.

## Журнал обхода

При запуске из терминала события записываются по одной JSON-строке в stderr и в `results/crawler.log`. Каждая строка содержит время в UTC, уровень, имя события и сообщение. Для HTTP-запросов там также есть URL и, когда доступны, номер попытки и статус.

- `log_level` задаёт минимальный уровень. По умолчанию используется `INFO`.
- `log_file` меняет путь к журналу. Значение `null` оставляет вывод в консоль.
- `log_max_bytes` задаёт порог ротации, по умолчанию 1 000 000 байт. `log_backup_count` задаёт количество архивов, по умолчанию 3.

Чтобы настроить тот же журнал при запуске из Python, оберни вызов обхода в `with configure_logging(config):`. Функция доступна из пакета `crawler` и принимает результат `load_config()`.
