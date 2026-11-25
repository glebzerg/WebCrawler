# Асинхронный веб-краулер

Учебный проект на Python 3.12. Краулер загружает страницы параллельно, извлекает данные из HTML, соблюдает `robots.txt`, читает карты сайта и сохраняет страницы и отчёты обхода.

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

`requirements.txt` перечисляет зависимости проекта. `aiohttp` выполняет HTTP-запросы, BeautifulSoup4 и lxml разбирают HTML и XML, `aiofiles` работает с файлами, а `aiosqlite` — с базой SQLite.

## Проверка установки

```powershell
.\.venv\Scripts\python.exe -c "import sys, aiohttp, aiofiles, aiosqlite, bs4, lxml; print(sys.version.split()[0]); print(sys.executable); print('All project dependencies imported')"
```

Первая строка показывает версию Python, вторая — путь к запущенному интерпретатору внутри `.venv`. Последняя строка появляется после успешного импорта всех библиотек.

## Быстрый запуск на локальном сайте

В первом терминале запусти демонстрационный сайт. Он слушает только `127.0.0.1`.

```powershell
.\.venv\Scripts\python.exe -m examples.day7_demo_site
```

Во втором терминале выполни команды из корня проекта.

```powershell
.\.venv\Scripts\python.exe -m crawler --config examples/crawler.example.json --stats-json results/quickstart/cli-stats.json --report-html results/quickstart/cli-report.html
Get-Content .\results\quickstart\cli-stats.json -Encoding UTF8
Invoke-Item .\results\quickstart\cli-report.html
```

Краулер должен сохранить три страницы и заблокировать одну. В итоговой строке будет `Finished 4/10 pages (40.0% of budget)`, поскольку заблокированный адрес тоже занимает одну позицию бюджета. В отчёте будет пять ответов `200` за три страницы, `robots.txt` и карту сайта.

Чтобы запустить тот же обход через Python API, выполни следующую команду при работающем сайте.

```powershell
.\.venv\Scripts\python.exe -m examples.day7_python_api
Invoke-Item .\results\quickstart\python-report.html
```

Оба запуска записывают страницы в `results/quickstart/pages.json`, а каждый запуск сохраняет отчёты со своим префиксом. Параметры сайта и результата находятся в [готовом конфиге](examples/crawler.example.json). Если порт 8765 занят, передай сайту `--port 8766` и укажи этот порт в `sitemap_urls` своей копии конфига. После проверки останови сайт через Ctrl+C в первом терминале.

## Локальные тесты

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
```

Сетевые тесты поднимают локальные серверы на свободных портах. Сквозной тест сравнивает запросы, записи SQLite и отчёты при запусках через Python и CLI. Отдельная проверка отменяет обход и проверяет закрытие HTTP-сессии, задачи записи и соединения SQLite.

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

## Демонстрации повторов и хранения

```powershell
.\.venv\Scripts\python.exe -m examples.day5_retry_report
.\.venv\Scripts\python.exe -m examples.day6_storage_comparison
```

Первый скрипт показывает таймаут, восстановление после `503` и постоянный `404`. Второй обходит один локальный сайт с JSON, CSV и SQLite и сравнивает прочитанные записи. Оба скрипта выводят путь к отчёту.

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
.\.venv\Scripts\python.exe -m crawler --config config.json --stats-json results/stats.json --report-html results/report.html
```

Обе команды используют те же настройки, что и `AdvancedCrawler`. Явно переданный флаг переопределяет соответствующее поле JSON. `--urls` заменяет список `start_urls`, а `--respect-robots` и `--no-respect-robots` задают режим для `robots.txt`.

Во время обхода CLI показывает долю использованного бюджета, активные страницы и запросы, скорость и оценку времени до `max_pages`. Если очередь опустеет раньше, последняя строка показывает фактическое число завершённых страниц.

`--stats-json` и `--report-html` сохраняют итоговые отчёты после закрытия хранилища. Для отчётов нужны отдельные пути.

Если в конфиге указано хранилище, `--output` меняет его путь. При запуске без настроенного хранилища формат определяется по расширению файла. Поддерживаются `.jsonl`, `.csv`, `.db`, `.sqlite` и `.sqlite3`. Для остальных расширений используется JSON.

## Журнал обхода

При запуске из терминала события записываются по одной JSON-строке в stderr и в `results/crawler.log`. Каждая строка содержит время в UTC, уровень, имя события и сообщение. Для HTTP-запросов там также есть URL и, когда доступны, номер попытки и статус.

- `log_level` задаёт минимальный уровень. По умолчанию используется `INFO`.
- `log_file` меняет путь к журналу. Значение `null` оставляет вывод в консоль.
- `log_max_bytes` задаёт порог ротации, по умолчанию 1 000 000 байт. `log_backup_count` задаёт количество архивов, по умолчанию 3.

Чтобы настроить тот же журнал при запуске из Python, оберни вызов обхода в `with configure_logging(config):`. Функция доступна из пакета `crawler` и принимает результат `load_config()`.

## Python API

| Метод | Что делает |
|---|---|
| `AsyncCrawler.fetch_url(url)` | Возвращает текст ответа для одного адреса. |
| `AsyncCrawler.fetch_urls(urls)` | Загружает несколько адресов параллельно. Причины неудач доступны в `failed_urls`. |
| `AsyncCrawler.fetch_and_parse(url)` | Возвращает запись страницы с полями HTML и HTTP. |
| `AsyncCrawler.fetch_robots(url)` | Загружает или берёт из кэша правила `robots.txt` для сайта. |
| `AsyncCrawler.crawl(start_urls, ..., sitemap_urls=...)` | Обходит очередь адресов с учётом глубины, бюджета и фильтров. |
| `AdvancedCrawler.from_config(path)` | Читает JSON и подключает настроенное хранилище и повторы. |
| `AdvancedCrawler.crawl()` | Запускает обход по конфигу. Принимает `on_progress` и `progress_interval` в секундах. |
| `get_stats()` | Возвращает текущие счётчики страниц, запросов и записи. |
| `close()` | Завершает запись и освобождает ресурсы. Вызывай в `finally`. |
| `export_to_json(path)`, `export_to_html_report(path)` | Асинхронные методы `AdvancedCrawler` для экспорта статистики. |

## Форматы хранения

| Формат | Как прочитать |
|---|---|
| JSON | `json.load()` читает массив записей после закрытия хранилища. |
| JSONL | Читай файл построчно и передавай каждую строку в `json.loads()`. |
| CSV | `csv.DictReader` читает столбцы. Для открытия файла укажи его кодировку и `newline=""`. Вложенные поля разбираются через `json.loads()`. |
| SQLite | `SELECT * FROM pages` читает таблицу. Повторная запись URL обновляет строку. Списки и словари хранятся как JSON. |

Запись страницы содержит исходный URL, заголовок, текст, ссылки, метаданные и извлечённые элементы HTML. Поля `status_code` и `content_type` описывают HTTP-ответ, а `crawled_at` сохраняется в ISO 8601 с часовым поясом UTC. JSON, JSONL и CSV при новом запуске записываются заново. SQLite сохраняет остальные строки и обновляет только те URL, которые встретились в обходе.

## Замеры производительности

```powershell
.\.venv\Scripts\python.exe -m examples.day7_benchmark
```

Замер проводился на Windows 11 с Python 3.12.6. Оба клиента использовали один парсер, повторно использовали HTTP-соединения и получали одинаковые страницы с локального сервера с искусственной задержкой 8 мс. Таблица показывает страницы в секунду.

| Страниц | Синхронно | Async ×1 | Async ×10 | Async ×24 |
|---:|---:|---:|---:|---:|
| 100 | 60 | 57 | 199 | 198 |
| 500 | 66 | 63 | 329 | 334 |
| 1000 | 63 | 62 | 318 | 331 |

На 1000 страницах последовательный обход занял 15,98 с, асинхронный с конкурентностью 10 — 3,14 с, а с конкурентностью 24 — 3,02 с. Пик Python-аллокаций для этих трёх прогонов составил 2,14, 3,74 и 5,20 МиБ соответственно. Память измерялась через `tracemalloc` в отдельном прогоне. Повторный замер для конкурентности 1 и 24 дал 16,13 и 2,70 с.

На 500 страницах при конкурентности 10 контрольный обход без задержки дал 327 страниц/с против 329 страниц/с в основном замере. Это указывает, что при таком числе одновременных запросов заметную долю времени занимают обработка страниц и планирование задач. Скрипт сверяет данные каждого прогона с синхронным обходом и сохраняет полные результаты в `results/day7-benchmark/run-*/report.json`.
