# INCI Telegram Bot

Telegram-бот, который разбирает состав косметики (INCI) и говорит, чего там много, чего стоит избегать и подходит ли продукт твоему типу кожи. Бесплатный, без подписок и рекламы.

Состав можно прислать тремя способами: текстом, фото этикетки (Tesseract OCR) или просто названием продукта — бот сам найдёт состав.

## Что внутри

- **28 354 INCI** — словарь `beauteeru/cosmetic-ingredients-dataset` (MIT) + кураторские overlay JSON (комедогены, аллергены, ирританты, полезные, синонимы).
- **EU 1223/2009** — флаги Annex II–VI (запрещено / ограничено / разрешённые красители-консерванты-UV-фильтры) с детальными max-концентрациями: «Salicylic Acid — макс. 3 % rinse-off, 0.5 % как консервант leave-on, не для детей до 3 лет». Источник — официальный CosIng Inventory + мирор EU-таблиц от Open Beauty Facts.
- **Локальный каталог Sephora** (~1 100 продуктов) — мгновенный поиск по бренду/названию без сетевых вызовов. Open Beauty Facts подключается как fallback для масс-маркета.
- **Подбор аналогов почище** — берём ту же категорию OBF, прогоняем топ-30 через анализатор, отдаём 3 варианта с лучшим скором.
- **Поправки на тип кожи** — dry / oily / combination / sensitive / normal / acne_prone.
- **Профиль** с историей анализов и пагинацией.
- **Anti-abuse rate-limits** на Redis: 60 текстов/ч, 10 фото/10 мин, 30 поисков/ч. Если Redis отвалился — fail-open, бот продолжает работать.

Дополнительные источники для обогащения: **PubChem** (XLogP, молвес, IUPAC), **EU CosIng API** (только когда офлайн-картинка неполная), **skinsignal.ru** (русские переводы и теги). Все ответы кэшируются в Redis на 7 дней.

## Стек

Python 3.12, aiogram 3, SQLAlchemy 2 async, Alembic, Redis, Tesseract + OpenCV, rapidfuzz, httpx. Запускается через Docker Compose.

## Быстрый старт

```bash
cp .env.example .env
# Заполни как минимум BOT_TOKEN
docker compose up -d --build
```

Что произойдёт: поднимутся `postgres` и `redis` (только во внутренней docker-сети), `migrate` накатит Alembic, `bot` стартует polling и подгрузит 28K INCI в память.

## Тесты

```bash
python -m venv .venv
.venv/Scripts/activate          # Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/
```

Postgres и Redis для тестов не нужны.

## Структура

```
inci-bot/
  data/                 # inci_dict.csv (28K, MIT) + overlay JSON + EU Annex CSV/JSON + sephora каталог
  src/
    bot/                # aiogram: handlers, FSM, клавиатуры, форматтер, rate-limit
    core/               # config, db, models, repositories, enums
    services/           # ingredients_db, parser, analyzer,
                        # ocr, product_search, analogs, cosing, pubchem
  alembic/              # миграции БД
  scripts/              # пересборка overlay-ов из исходных CSV
  tests/                # юнит-тесты парсера, анализатора, форматтера, rate-limit
  Dockerfile, docker-compose.yml
```

## Как считается риск

1. **Парсер** режет состав по запятым/точкам с запятой, чистит префиксы (`Ingredients:`/`Состав:`), bullet-points, lowercase.
2. **Cascade lookup** в `IngredientsDB`: synonyms → exact в 28K-словаре → exact в overlay → rapidfuzz `token_set_ratio ≥ 92%` (с пре-фильтром по первой букве) → `unknown`.
3. **Базовый скор** — экспоненциальный позиционный вес (`exp(-i/8)`, LOI-правило). Поправки на тип кожи: acne_prone/oily — комедогены ×2; sensitive — раздражители/аллергены ×1.5; dry — агрессивные ПАВ ×1.3.
4. **Регуляторная надбавка (EU 1223/2009)** добавляется поверх взвешенного скора плоским bonus-ом, чтобы статус «запрещено в ЕС» не размывался остальной формулой:
   - Annex II — `+35` и принудительный `verdict=high`;
   - CMR1A / CMR1B (если ещё не Annex II) — `+18` и тоже `high`;
   - Annex III — `+8`;
   - CMR2 — `+8`.
5. **Enrichment**: офлайн-функции CosIng (28 487 INCI) — мгновенно; живой CosIng API — только если в офлайне нет ни functions, ни regulatory; PubChem — XLogP (>4 ⇒ липофильность ⇒ повышенное проникновение через барьер кожи).
6. **Вердикт**: `<25` низкий, `25–55` средний, `>55` высокий. Annex II / CMR1A / CMR1B всегда `high`.

## Поиск по названию

1. Локальный Sephora-каталог (`data/products.json`, 1 131 продукт) по бренду/названию/категории — мгновенно, in-memory.
2. Если локально < 4 совпадений — добавляем результаты Open Beauty Facts (живой HTTP). Локальные и OBF дедуплицируются и показываются единым списком.
3. После выбора продукт уходит в общий `analyze_full` — анализ от источника не зависит.

## Подбор аналогов

После анализа продукта по названию доступна кнопка «🔄 Найти аналог почище».

1. Берём самую конкретную OBF-категорию исходника (например, `en:face-creams`). Локальный Sephora-каталог хранит совместимые `en:moisturizers` / `en:cleansers` / `en:facial-treatments` / `en:facial-masks` / `en:eye-creams`, поэтому аналоги работают для обоих источников.
2. Запрашиваем топ-30 продуктов в этой категории через OBF.
3. Прогоняем каждый через `analyze` (без внешних HTTP — только in-memory словарь).
4. Возвращаем 3 варианта с самым низким скором и улучшением ≥ 5 пунктов.

## Источники данных

Все источники бесплатные и совместимы с публичным сервисом.

| Файл / источник | Что даёт | Лицензия |
|---|---|---|
| `data/inci_dict.csv` | 28 354 канонических INCI | [beauteeru/cosmetic-ingredients-dataset](https://github.com/beauteeru/cosmetic-ingredients-dataset), MIT |
| `data/cosing_inventory.csv` → `regulatory.json` (1 871) + `functions.json` (28 487) | Annex / CMR-флаги, функции INCI | [EU CosIng Inventory](https://ec.europa.eu/growth/tools-databases/cosing/), снапшот 15/12/2020, [Decision 2011/833/EU](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32011D0833) |
| `data/annexes/annex_*.csv` → `annex_details.json` (2 760) | max %, тип продукта, предупреждения по Annex II–VI | [openfoodfacts/openbeautyfacts cosing](https://github.com/openfoodfacts/openbeautyfacts/tree/develop/cosing), Decision 2011/833/EU |
| `data/sephora_products.csv` → `products.json` (1 131) | Локальный каталог продуктов для быстрого поиска | [Vazquez/Cosmetic-Price-Analysis](https://github.com/VazquezJocelyn/Cosmetic-Price-Analysis), MIT |
| EU CosIng API ([cosingchecker.com](https://cosingchecker.com)) | Runtime safety-net для INCI вне snapshot 2020 | Публичные материалы Еврокомиссии |
| PubChem PUG REST | XLogP, молвес, IUPAC, H-bond donors/acceptors | NIH, открытая база |
| Open Beauty Facts | Поиск масс-маркет продуктов и подбор аналогов | ODbL |
| skinsignal.ru | Русские переводы и теги. `robots.txt` разрешает `/ingredients/*`, ToS отсутствует. Скрейпер вежливый: 1 req/s, идентифицирующий User-Agent, кэш Redis (успех 7д, 404 30д) | Отключается `SKINSIGNAL_ENABLED=false` |
| Overlay JSON (`comedogenic`, `allergens`, `irritants`, `beneficial`, `synonyms`) | Кураторские разметки | Fulton 1989, EU 1223/2009 Annex III + 2023/1545, ICDRG/NACDG, CIR |

Скрипты пересборки overlay-ов лежат в `scripts/` (`build_regulatory.py`, `build_functions.py`, `build_annex_details.py`, `build_products.py`).

## Конфигурация (`.env`)

| Переменная | Назначение |
|---|---|
| `BOT_TOKEN` | Токен Telegram-бота |
| `DATABASE_URL` / `ALEMBIC_DATABASE_URL` | Postgres async / sync |
| `REDIS_URL` | Redis (rate-limit + enrichment cache) |
| `TESSERACT_CMD` / `TESSERACT_LANGS` | Путь к Tesseract и языки (`eng+rus` по умолчанию) |
| `SKINSIGNAL_ENABLED` / `SKINSIGNAL_MAX_LOOKUPS` | Скрейпер skinsignal.ru |

## Лицензия

Код — MIT (`LICENSE`). Атрибуция датасета — `data/LICENSE-BEAUTEE.md`. Open Beauty Facts — [ODbL](https://opendatacommons.org/licenses/odbl/).
