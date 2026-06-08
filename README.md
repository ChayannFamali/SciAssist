# SciAssist

Локальный AI-ассистент для научной работы. Читает PDF, строит структурированные заметки в Obsidian, отвечает на вопросы по библиотеке, генерирует черновики.

**Всё работает offline — без облаков, без API-ключей, без VPN.**

---

## Требования

| Компонент | Версия |
|---|---|
| Windows | 10/11 x64 |
| Python | 3.11+ |
| [LM Studio](https://lmstudio.ai/) | последняя |
| [Zotero](https://www.zotero.org/) | 9 |
| [Better BibTeX](https://github.com/retorquere/zotero-better-bibtex) | 9+ |

### Модели в LM Studio (скачать заранее)

| Роль | Модель |
|---|---|
| Основной анализ | Qwen3.6 35B A3B Q4_K_M |
| Reasoning | Magistral Small 2509 Q4_K_M |
| Чат / Саммари | Qwen3.5 9B Q4_K_M |
| Vision (фигуры) | Qwen3-VL-8B Q4_K_M |
| Embeddings | text-embedding-bge-m3 |

> ⚠️ Для Qwen3-моделей в LM Studio отключи **Enable Thinking** (иначе генерация зависает).

---

## Установка

```powershell
cd H:\SciAssist
python -m venv .venv
.venv\Scripts\activate
pip install --index-url https://pypi.tuna.tsinghua.edu.cn/simple -e .
```

### Настройка Zotero

1. Zotero → Preferences → Advanced → Config Editor
2. `extensions.zotero.httpServer.enabled` → `true`
3. `extensions.zotero.httpServer.localAPI.enabled` → `true`
4. Перезапусти Zotero
5. Better BibTeX: Settings → Citation Keys → формула: `auth.lower + year + shorttitle(1,1).lower`

### Настройка путей

```powershell
Copy-Item .env.example .env
# Отредактируй .env под свои пути
```

### Проверка

```powershell
python scripts/healthcheck.py
```

Все строки должны быть зелёными ✅.

---

## Первый запуск

```powershell
# Проверить модели в LM Studio (обнови configs/model_router.yaml)
curl http://localhost:1234/v1/models

# Обработать одну статью
sciassist process vaswani2017attention

# Проверить результат
sciassist ask "What is the main contribution?"
```

---

## Как работает RAG

SciAssist использует **Retrieval-Augmented Generation** — перед ответом система ищет релевантные фрагменты в твоей библиотеке и передаёт их в LLM как контекст.

```
Вопрос
  │
  ▼
[Embed] — вопрос → вектор (text-embedding-bge-m3)
  │
  ▼
[Retrieve] — поиск top-K чанков в ChromaDB по косинусному расстоянию
  │
  ▼
[Augment] — сборка контекста: [citekey] (раздел: section): текст...
  │
  ▼
[Generate] — LLM отвечает ТОЛЬКО по контексту, цитируя [citekey]
  │
  ▼
Ответ + список источников со score
```

### Как статья попадает в индекс

```
PDF → olmocr (OCR) → Markdown → чанки по разделам → эмбеддинги → ChromaDB
```

При `sciassist process @citekey` статья автоматически индексируется.  
Индекс хранится в `data\chroma_db\`, коллекция — `papers_full`.

### Параметры поиска

| Параметр | По умолчанию | Описание |
|---|---|---|
| `--top` | 5 | Кол-во чанков для контекста |
| `collection` | `papers_full` | Коллекция ChromaDB |

```powershell
sciassist ask "вопрос" --top 10   # расширенный контекст
sciassist search "запрос" --top 5  # только поиск, без LLM
```

### Что значит score

Score = `1 − distance` (косинусная схожесть).

| Score | Интерпретация |
|---|---|
| > 0.85 | Высокая релевантность |
| 0.6–0.85 | Умеренная релевантность |
| < 0.6 | Слабая связь с вопросом |

### Ограничения RAG

- LLM отвечает **только по проиндексированным статьям** — не придумывает.  
- Если статья не обработана (`sciassist process`) — она недоступна для поиска.  
- Качество ответа зависит от качества OCR: плохой текстовый слой → плохие чанки.  
- Очень короткий вопрос → слабый эмбеддинг → менее точный retrieval. Формулируй развёрнуто.

## Ежедневный workflow

```
1. Открыть Zotero, добавить статьи в коллекцию SciAssist Queue
2. sciassist process --queue
3. Obsidian — читать структурированные заметки
4. sciassist ask "вопрос" — искать по библиотеке
```

---

## Справка по командам

### Обработка

```powershell
sciassist process @citekey              # одна статья
sciassist process @citekey --only=markdown  # только OCR
sciassist process --queue              # вся очередь
sciassist process --queue --force      # переобработать всё
```

### Поиск и вопросы

```powershell
sciassist search "запрос" --top 10     # семантический поиск (без LLM)
sciassist ask "вопрос"                 # RAG-ответ с цитатами
sciassist similar @citekey --top 10   # похожие статьи
```

### Анализ и написание

```powershell
sciassist analyze @citekey --mode critique
sciassist gaps "тема" --papers 15
sciassist draft related-work "тема" --papers 10
```

### Управление заметками

```powershell
sciassist note @citekey                # создать/обновить заметку
sciassist note @citekey --force        # пересоздать (сохраняет "Мои мысли")
```

### Zotero

```powershell
sciassist zotero list                  # список в Queue
sciassist zotero list --collection "Reading"
sciassist zotero setup                 # создать коллекции
sciassist zotero status               # статистика
```

### Система

```powershell
sciassist health                       # диагностика
sciassist stats                        # статистика индекса
sciassist logs --tail 20               # последние LLM-вызовы
```

---

## Структура данных

```
H:\SciAssist\
├── data\
│   ├── raw_markdown\           # OCR-результаты
│   ├── extracted_figures\      # фигуры + figures.json
│   ├── chroma_db\              # векторная БД
│   ├── processed_registry.json # что обработано
│   └── logs\                   # логи + llm_calls.jsonl
D:\SciVault\
├── papers\   @citekey.md       # заметки Obsidian
├── concepts\ concept.md        # методы и концепции
└── datasets\ dataset.md        # датасеты
```

---

## Известные ограничения

**Zotero write (501):** Local API не поддерживает запись — теги `processed` и перемещение в `SciAssist Processed` не работают. Статус отслеживается через `processed_registry.json`.

**Olmocr:** Требует anchor text — работает только если в PDF есть текстовый слой. Для чистых сканов нужна отдельная настройка.

**Qwen3 thinking mode:** Если включён — модель зависает. Отключи в LM Studio для всех используемых моделей.

**VLM контекст:** Qwen3-VL загружен с 4096 токенами. Крупные изображения автоматически ресайзятся до 1024px.

---

## Troubleshooting

**`LM Studio недоступен`** — запусти LM Studio, загрузи нужные модели.

**`PDF не найден`** — проверь что в Zotero к записи прикреплён PDF (стрелка ▶ под записью). Правый клик → Find Available PDF.

**`JSON parse failed`** — модель вернула пустой ответ (thinking mode). Отключи в LM Studio.

**`501 Not Implemented`** — Zotero local API не поддерживает запись. Игнорируй, обработка прошла успешно.

**Медленная генерация** — уменьши `_NOTE_MAX_WORDS` в `obsidian_builder.py` (текущее: 5000).
```

---

## Итого: все фазы закрыты

| Фаза | Результат |
|---|---|
| 0–1 | Инфраструктура: Zotero + LM Studio + ChromaDB |
| 2 | PDF → Markdown → RAG → Ответ |
| 3 | Фигуры + Obsidian-заметки + wiki-links |
| 4 | Queue orchestration |
| 5 | Промпты + CLI polish |
| 6 | 4 реальные статьи в продакшне |
| 7 | similar, gaps, draft, analyze |
| 8 | Документация |

**Добавляй статьи в Zotero Queue → `sciassist process --queue` → Obsidian + RAG обновляются.**