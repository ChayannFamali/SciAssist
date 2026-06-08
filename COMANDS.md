````markdown
## Полный список команд SciAssist CLI

# СИСТЕМА

# Диагностика всей инфраструктуры
sciassist health

# Статистика индекса
sciassist stats

# Последние N LLM-вызовов
sciassist logs --tail 20
sciassist logs --tail 50


# ОБРАБОТКА СТАТЕЙ

# Одна статья — полный пайплайн (OCR → фигуры → индекс → заметка). В качестве примера используется статья Attention is all you need
sciassist process vaswani2023attention

# Только OCR → markdown (без заметки и индексации)
sciassist process vaswani2023attention --only=markdown

# Переобработать принудительно (даже если уже в registry)
sciassist process vaswani2023attention --force

# Вся очередь SciAssist Queue
sciassist process --queue

# Вся очередь принудительно
sciassist process --queue --force


# ЗАМЕТКИ OBSIDIAN

# Создать/обновить заметку (если нет или устарела)
sciassist note vaswani2023attention

# Пересоздать заметку принудительно (сохраняет "Мои мысли" и "Заметки при чтении")
sciassist note vaswani2023attention --force


# ПОИСК И ВОПРОСЫ

# Семантический поиск — сырые чанки без LLM
sciassist search "attention mechanism"
sciassist search "multi-agent reinforcement learning" --top 10
sciassist search "opinion dynamics" --top 5 --col papers_notes

# RAG-вопрос с цитатами [citekey]
sciassist ask "What is the main contribution of the Transformer?"
sciassist ask "Какие методы используются для моделирования мнений?" --top 5
sciassist ask "What datasets are used?" --top 10

# Похожие статьи по embedding-близости
sciassist similar vaswani2023attention
sciassist similar vaswani2023attention --top 10
sciassist similar chen2026memdreamer --top 5


# АНАЛИЗ И НАПИСАНИЕ

# Глубокий критический анализ статьи
sciassist analyze vaswani2023attention --mode critique

# Gap analysis по теме (нерешённые проблемы, противоречия, направления)
sciassist gaps "attention mechanisms in NLP" --papers 4
sciassist gaps "video understanding with memory" --papers 10
sciassist gaps "sycophancy in LLMs" --papers 5

# Черновик Related Work с \cite{citekey}
sciassist draft related-work "multi-agent reinforcement learning" --papers 4
sciassist draft related-work "transformer architecture" --papers 10
sciassist draft related-work "opinion dynamics simulation" --papers 6


# ZOTERO

# Список статей в коллекции (по умолчанию SciAssist Queue)
sciassist zotero list
sciassist zotero list --collection "SciAssist Queue"
sciassist zotero list --collection "Reading"

# Список с проверкой наличия PDF (медленнее)
sciassist zotero list --check-pdf

# Создать коллекции Queue и Processed если не существуют
sciassist zotero setup

# Статистика: сколько в Queue / Processed / registry
sciassist zotero status


# ИНДЕКС

# Инкрементальная переиндексация (только изменённые файлы)
python scripts/reindex.py

# Полная переиндексация с нуля (удали data/chroma_db/ и data/index_registry.json, затем)
```
sciassist process --queue --force
```

---

## Параметры команд

| Команда | Параметр | Значения | По умолчанию |
| --- | --- | --- | --- |
| `process` | `--only` | `markdown`, `full` | `full` |
| `process` | `--force` | флаг | `false` |
| `search` | `--top` / `-k` | число | `5` |
| `search` | `--col` | `papers_full`, `papers_notes` | `papers_full` |
| `ask` | `--top` / `-k` | число | `5` |
| `similar` | `--top` / `-k` | число | `10` |
| `gaps` | `--papers` / `-n` | число | `10` |
| `draft related-work` | `--papers` / `-n` | число | `10` |
| `analyze` | `--mode` | `critique` | `critique` |
| `zotero list` | `--collection` / `-c` | название коллекции | `SciAssist Queue` |
| `zotero list` | `--check-pdf` | флаг | `false` |
| `logs` | `--tail` / `-n` | число | `20` |
