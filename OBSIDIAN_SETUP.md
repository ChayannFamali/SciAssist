## 1. `docs/OBSIDIAN_SETUP.md`

```markdown
# Obsidian — Настройка для SciAssist

## Установка

1. Скачай [Obsidian](https://obsidian.md/) — бесплатно, Windows
2. При первом запуске: **Open folder as vault**
3. Укажи путь: `D:\SciVault`

> ⚠️ Путь должен совпадать с `OBSIDIAN_VAULT` в `.env`

---

## Структура vault (создаётся автоматически)

```
D:\SciVault\
├── papers\        ← заметки на статьи (@citekey.md)
├── concepts\      ← методы, термины (stub-файлы)
├── datasets\      ← датасеты (stub-файлы)
├── ideas\         ← твои идеи (только вручную, SciAssist не трогает)
└── drafts\        ← черновики (только вручную, SciAssist не трогает)
```

---

## Рекомендуемые плагины

Установка: **Settings → Community plugins → Browse**

| Плагин | Зачем |
|---|---|
| **Dataview** | SQL-подобные запросы по заметкам (`TABLE`, `LIST` по тегам) |
| **Graph Analysis** | Расширенный анализ графа связей |
| **Copilot** | AI-ассистент прямо в Obsidian (через LM Studio) |
| **Zotero Integration** | Вставка цитат из Zotero в заметки |
| **Tag Wrangler** | Управление тегами (#stub, #paper, #manual_notes) |

---

## Анатомия сгенерированной заметки

```
@vaswani2023attention.md
│
├── YAML frontmatter    ← метаданные (citekey, tags, year, doi)
├── TL;DR callout       ← 2 предложения о статье
├── 🎯 Проблема         ← что решает статья
├── 🔬 Метод            ← как решает
├── Ключевые идеи       ← список ключевых идей
├── 📊 Эксперименты     ← на чём тестировали
├── 📈 Результаты       ← числовые результаты
├── Фигуры              ← описание графиков (если есть)
├── ⚠️ Ограничения      ← от авторов
├── 🔍 Критика (LLM)   ← сильные/слабые стороны
├── 🔗 Связи            ← [[method]], [[dataset]], [[@citekey]]
├── 💡 Мои мысли        ← ТОЛЬКО ручной ввод
└── 📝 Заметки          ← ТОЛЬКО ручной ввод
```

> ⚠️ `sciassist note --force` перезаписывает заметку, но **сохраняет**
> секции "Мои мысли" и "Заметки при чтении". Защита: добавь тег
> `#manual_notes` в frontmatter — тогда заметка не перезапишется вообще.

---

## Граф знаний

**Открыть:** Ctrl+G или кнопка граф в левой панели

Что видишь:
- Большие узлы = часто упоминаемые концепции
- `@citekey` → статьи
- `concept-name` → методы/термины из `concepts/`
- Stub-файлы (пустые) видны как узлы без связей внутри

**Настройка фильтрации в графе:**
- Filters → Files → включи `papers/`, `concepts/`
- Groups → добавь цвет по тегу: `tag:#stub` — серый, `tag:#paper` — синий

---

## Полезные Dataview-запросы

Вставь в любую заметку как код-блок ` ```dataview ` :

```dataview
TABLE year, status FROM "papers"
SORT year DESC
```

```dataview
LIST FROM "papers"
WHERE contains(tags, "paper")
AND status = "to_read"
```

```dataview
LIST FROM "concepts"
WHERE contains(tags, "stub")
SORT file.name ASC
```

---

## Тег `#manual_notes`

Если ты активно работаешь с заметкой и не хочешь перезаписи:

```yaml
---
tags: [paper, transformer, manual_notes]
---
```

`sciassist note --force` сохранит файл нетронутым. Убери тег, когда захочешь обновить LLM-секции.

---

## Obsidian Copilot + LM Studio

1. Установи плагин **Copilot**
2. Settings → Copilot → Model Provider: `LM Studio (OpenAI Compatible)`
3. Base URL: `http://localhost:1234/v1`
4. Model: `qwen/qwen3.5-9b` (или любой загруженный)

Теперь можно чатиться с LLM прямо внутри Obsidian, используя контекст открытой заметки.