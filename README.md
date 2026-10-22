# SciAssist

Локальный AI-ассистент для научной работы. Читает PDF, строит структурированные заметки в Obsidian, отвечает на вопросы по библиотеке (RAG), находит пробелы и генерирует черновики.

**Всё работает offline — без облаков, без API-ключей, без VPN.**

Работает на двух машинах с общим git-репозиторием:

- **ПК** (Windows, CUDA) — GPU-стек
- **Ноутбук** (Linux, Core Ultra 7 255H, 32 ГБ RAM, без dGPU) — CPU-стек

Стек моделей по машинам — **[config.md](config.md)** (источник истины — `configs/model_router*.yaml`).

---

## Требования

| Компонент | Версия |
|---|---|
| ОС | Windows 10/11 x64 или Linux |
| Python | 3.11+ |
| [LM Studio](https://lmstudio.ai/) | последняя |
| [Zotero](https://www.zotero.org/) | 9 |
| [Better BibTeX](https://github.com/retorquere/zotero-better-bibtex) | 9+ |
| Obsidian | любой (vault = папка заметок) |

### Модели в LM Studio (скачать заранее)

Полные таблицы с замерами и обоснованиями — в **[config.md](config.md)**. Кратко:

| Роль | Ноутбук (CPU) | ПК (CUDA) |
|---|---|---|
| heavy-аналитика (critique/gaps) | gemma-4-26b-a4b-qat | qwen3.6-35b-a3b / magistral-small |
| extraction (structure/entity) | gemma-3n-e4b | qwen3.5-9b |
| summary / chat | gemma-4-e4b / gemma-3n-e4b | qwen3.5-9b |
| vision (фигуры) | qwen3-vl-4b | qwen3-vl-8b |
| embeddings | **text-embedding-bge-m3** (одинаково на обеих — НЕ менять) | **text-embedding-bge-m3** |

> ⚠️ Моделям с режимом thinking (gemma-4, qwen-гибриды) выключи **Enable Thinking**
> нативным тумблером в настройках LM Studio — `extra_body` из API LM Studio игнорирует.
> Ноутбучные LM Studio-конфиги (ctx/threads/FA/KV) — в [config.md](config.md).

---

## Установка

```bash
git clone <repo> && cd SciAssist
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e .                                      # или зеркало: --index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

> Команда `sciassist` живёт в venv. В каждом новом терминале: `source .venv/bin/activate` —
> или напрямую `.venv/bin/sciassist`, или алиас в `~/.bashrc`
> (`alias sciassist='/mnt/newdisk/SciAssist/.venv/bin/sciassist'`).

### Настройка Zotero

1. Zotero → Preferences → Advanced → Config Editor:
   `extensions.zotero.httpServer.enabled` → `true`;
   `extensions.zotero.httpServer.localAPI.enabled` → `true`
2. Better BibTeX: Settings → Citation Keys → формула: `auth.lower + year + shorttitle(1,1).lower`
3. **Разрешение local API на запись**: при первом обращении Zotero спросит разрешение
   (или запусти authorize) — выбери **«Always Allow»**; ключ пойдёт в `.env`
   (`ZOTERO_LOCAL_API_KEY`)

### Настройка путей

```bash
cp .env.example .env     # затем отредактируй под свою машину
```

Ключевое: `SCIASSIST_MODEL_ROUTER` — какой роутер грузить
(`configs/model_router.laptop.yaml` на ноуте; на ПК — не задавать, живёт
`configs/model_router.yaml`). Пути Zotero/vault/данных — под свою машину.

### Проверка

```bash
python scripts/healthcheck.py    # все строки должны быть зелёными ✅
```

---

## Обработка статьи

```bash
sciassist process @citekey           # одна статья (PDF → markdown → заметка → индекс)
sciassist process --queue           # вся Zotero-очередь
sciassist process @citekey --force  # переобработать
```

Что происходит внутри (5 LLM-вызовов на ноуте, ~25 мин/статья на CPU;
на ПК существенно быстрее):

```
PDF → markdown (текстовый слой PyMuPDF; сканы — через knowledge add --ocr, роль ocr)
    → фигуры (VLM, роль vision) — описания с дисклеймером
    → индексация (bge-m3 → ChromaDB: papers_full)
    → LLM-конвейер заметки:
        1. summary           (tldr / problem / limitations)
        2. structure         (method / key_ideas / experiments / results, EN-JSON)
        2b. translation      (structure → RU, числа сверяются с оригиналом)
        3. entity_extraction (методы/датасеты/концепты — grounded-фильтр)
        4. critique          (strengths/weaknesses с цитатами из текста)
    → заметка Obsidian + индексация papers_notes
```

**Анти-галлюцинационная обвязка** (подробности в [config.md](config.md)):
числа сверяются с текстом статьи, термы entity — заземлены по тексту,
цитаты критики проверяются substring-ом, пустые секции помечаются явно.

---

## Как работает RAG

Гибридный retrieval + генерация по контексту:

```
Вопрос
  ├─ [dense]  эмбеддинг (bge-m3) → ChromaDB, косинус
  │           HyDE: LLM-гипотеза ответа расширяет dense-запрос (вкл. по умолчанию)
  ├─ [sparse] BM25 (rank_bm25, in-memory)
  │           RU-вопросы к EN-статьям: вопрос автоматически дополняется
  │           EN-переводом (роль rerank) — RU-лексика в EN-корпусе даёт 0 хитов
  └─→ [RRF-fusion] → фильтры (косинусный порог, денилист секций)
        → [rerank: listwise LLM, пул 10 → топ-5] → дедуп (max 3/статья)
        → [generate] ответ с [citekey]-цитатами (роль chat)
```

Метрики score = `1 − distance`: > 0.85 высокая, 0.6–0.85 умеренная, < 0.6 слабая.

### Опции retrieval

| Опция | По умолчанию | Что делает |
|---|---|---|
| `hybrid` | `True` | Dense + Sparse + RRF |
| `rerank` | `True` | LLM-переупорядочивание (отключи для скорости) |
| `hyde` | `True` | Гипотеза-ответ в dense-запрос |
| `expand_en` | `True` | EN-дополнение RU-вопроса для BM25 |
| `pool_k` | `10` | Пул кандидатов на rerank |
| `min_score` | `0.4` | Косинусный порог (BM25-хиты обходят) |
| `max_per_paper` | `3` | Максимум чанков из одной статьи |
| `collection` | `papers_full` | `papers_full` / `papers_notes` / `textbooks` / `both` / `all` |

Коллекции: `papers_full` (статьи, чанк ≈1200 слов), `papers_notes` (заметки, 800),
`textbooks` (база знаний, 400). `both` = статьи+заметки, `all` = все три —
честное чередование при сборке контекста.

### База знаний (учебники)

```bash
sciassist knowledge add <pdf>          # индексировать учебник (с скан-детектом; --ocr для сканов)
sciassist knowledge list               # что в базе
sciassist knowledge stats              # статистика
sciassist ask "..." --col all          # искать по статьям + заметкам + учебникам
```

---

## Ежедневный workflow

```
1. Zotero: добавить статьи в коллекцию SciAssist Queue
2. sciassist process --queue
3. Obsidian: читать структурированные заметки
4. sciassist ask "вопрос"              # поиск по библиотеке
5. sciassist knowledge add <учебник>   # пополнить базу знаний
```

---

## Справка по командам

```bash
# Обработка
sciassist process @citekey [--force] [--only=markdown]   # --queue — вся очередь

# Поиск и вопросы
sciassist search "запрос" --top 10     # только retrieval, без LLM
sciassist ask "вопрос" [--col both|all] [--top N]
sciassist similar @citekey --top 10   # похожие статьи

# Анализ и написание
sciassist analyze @citekey --mode critique
sciassist gaps "тема" --papers 15
sciassist draft related-work "тема" --papers 10

# Заметки
sciassist note @citekey [--force]     # --force сохраняет «Мои мысли»

# База знаний
sciassist knowledge add|list|stats

# Zotero
sciassist zotero list|setup|status

# Система
sciassist health|stats|logs --tail 20
```

---

## Структура данных

```
<PROJECT_ROOT>/                 # репозиторий (общий через git)
├── configs/                    # роутеры моделей + промпты + settings
├── data/raw_markdown/           # markdown-версии статей
├── data/extracted_figures/      # фигуры + описания
├── data/chroma_db/              # векторная БД (papers_full/notes/textbooks)
└── data/logs/                   # логи + llm_calls.jsonl

<OBSIDIAN_VAULT>/                # задаётся в .env
├── papers/@citekey.md           # структурированные заметки
├── concepts/  datasets/  ideas/  drafts/
```

`processed_registry.json` — реестр обработанного (статус Queue/Processed).

---

## Известные ограничения

**Скорость на CPU:** ноут без GPU — полный цикл статьи ~25 мин (5 LLM-вызовов
+ фигуры + индексация). На ПК с CUDA — единицы минут.

**Zotero write:** local API на запись требует одноразового разрешения
(«Always Allow», ключ в `.env`). Без него — 401.

**Thinking-режимы:** моделям с thinking выключай тумблер в LM Studio
(нативный конфиг модели, не API). «Думающие» модели без тумблера на CPU
сгорают минутами (qwen3.5-4b: 912с с нулём контента).

**olmocr:** требует текстовый слой в PDF. Чистые сканы — через `--ocr`
(дорого на CPU). `knowledge add` детектит сканы и останавливается без `--ocr`.

**VLM-описания фигур:** генерируются и НЕ проверяются — в заметке дисклеймер.

**CLI exit code:** одиночный `process` при ошибке честно выходит с кодом 1
(traceback в stderr). `--queue` не останавливается на упавшей статье (✗ в реестре,
дальше по очереди). Не маскируй код возврата пайпами (`| tail` вернёт код tail).

---

## Troubleshooting

**`LM Studio недоступен`** — запусти LM Studio; модели грузятся JIT по роутеру.

**`PDF не найден`** — прикрепи PDF к записи Zotero (linked file — не копия — ок).

**`JSON parse failed` / пустые секции** — проверь, что thinking выключен
в конфиге модели LM Studio. Пустые секции помечаются в заметке явно.

**`401` от Zotero** — повтори authorize и выбери «Always Allow».

**Медленная генерация на ноуте** — уменьши `_NOTE_MAX_WORDS` в
`obsidian_builder.py` (текущее: 5000) или понизь кванты моделей.

**RAG-оценка** — `scripts/rag_eval.py` (hit@K-матрица вариантов retrieval).
