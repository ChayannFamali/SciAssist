# SciAssist: стек LLM по машинам

Источник истины — yaml-роутеры (`configs/model_router*.yaml`);
этот файл — читаемая проекция стека + обоснования + LM Studio-настройки.
Все замеры — реальный промпт Card («With Little Power…», ~8k ток, если не указано иное).

## Как устроено (двухмашинная механика)

- **Ноутбук** (Core Ultra 7 255H, 32 ГБ RAM, без dGPU) — **CPU-only**, LM Studio `localhost:1234`
- **ПК** (Windows + CUDA) — GPU LM Studio
- Репо общий (git). Выбор конфига — через `.env` (gitignored):
  `SCIASSIST_MODEL_ROUTER=configs/model_router.laptop.yaml` → `Settings.model_router` → `ModelRouter`
- Роль, отсутствующая в yaml машины, валит пайплайн на `select(...)` — поэтому **ПК-yaml
  содержит все роли**, включая ноутбучные `structure`/`translation` (зеркала `deep_analysis`).
- **embed одинаков на обеих машинах: `text-embedding-bge-m3` (dim 1024) — НЕ МЕНЯТЬ**:
  индекс ChromaDB портативен ПК ↔ ноут только при одинаковых эмбеддингах.

---

## Ноутбук — `configs/model_router.laptop.yaml` (v2.3)

Конвейер статьи: 5 LLM-вызовов + vision. **Call 2 / 2b / 3 — все на `gemma-3n-e4b`**
(одна модель в RAM, без reload между вызовами).

| Роль (вызов) | Primary | Замер | Fallback |
|---|---|---|---|
| summary (Call 1/4) | `google/gemma-4-e4b` | 268с, 3 ключа, факты чистые | 26b-qat, 12b-qat |
| structure (Call 2/4) | `google/gemma-3n-e4b` | 163–177с, JSON 7/7, 8–9/12 чисел | gemma-4-e4b, gemma-3-12b |
| translation (Call 2b) | `google/gemma-3n-e4b` | 128с, RU, числа переживают (сверяется в коде) | gemma-4-e2b (69с) |
| entity_extraction (Call 3/4) | `google/gemma-3n-e4b` | 35с горячая / 177с холодная, 24/26 grounded | gemma-4-e4b |
| reasoning / critique (Call 4/4) | `google/gemma-4-26b-a4b-qat` | 232–352с, 9/15 spec-термов — лучшая критика | ministral-3-14b-reasoning, gpt-oss-20b |
| chat (`ask`, RAG) | `google/gemma-3n-e4b` | 76с — быстрейший из замеренных | gemma-4-e2b, gemma-3-12b |
| deep_analysis (gaps/related-work) | `google/gemma-4-26b-a4b-qat` | синтез по нескольким статьям | gpt-oss-20b |
| rerank (RAG) | `liquid/lfm2.5-1.2b` | 2.4с, listwise | granite-4-h-tiny, qwen3-4b |
| vision (фигуры) | `qwen/qwen3-vl-4b` | ~2.5–3.5 мин / 4 фиг | qwen/qwen3-vl-8b |
| ocr (сканы, `--ocr`) | `allenai/olmocr-2-7b` | 2–5 мин/стр на CPU | qwen3-vl-4b |
| **embed** | `text-embedding-bge-m3` | <1с/запрос | **НЕ МЕНЯТЬ** |

Полный цикл статьи: **~25–27 мин** (LLM ≈ 18–19 мин из них, фигуры ~3 мин, индексация ~5 мин).

### Почему так (ключевые решения, всё по замерам)

- **gemma-3n-e4b — рабочая лошадка extraction+chat**: no-reasoning (thinking-проблем нет как
  класса), JSON стабилен, decode быстрый. Отдаёт EN-JSON на structure (зеркалит язык
  system+статьи, RU-инструкция игнорируется) — **язык чинится не борьбой с моделью, а отдельным
  вызовом `translation`**; EN→RU с полной сверкой чисел в коде.
- **`gemma-4-e4b` снята с structure**: JSON-флейл 1/3 живых прогонов (проза вместо JSON → пустые
  секции). Осталась на summary (268с, чисто) и в fallback.
- **entity на 3n + grounded-постфильтр в коде** (`_filter_grounded`): терм выживает, только если
  его слова встречаются в тексте статьи. Закрыт весь класс few-shot-отравлений (bert/dropout/
  imagenet из примера промпта; ловил lfm2, 12b, 3n — после фильтра мусор = 0).
- **`gemma-4-26b-a4b-qat` — heavy-аналитика**: reasoning/critique 9/15 spec-термов (McNemar,
  Type-M/S, MDE, Delta-Laplace) + deep_analysis. Заняла бы 3 роли, если дать ей ещё summary.
- **`gpt-oss-20b` снят с primary critique**: peg-native-флейк ~50% (harmony-токены как literal),
  НЕ лечится ретраями и НЕ связан с enable_thinking (проверено замером). Только fallback.
- **Отвергнуты**: mistral-7b (галлюцинация + 0/12, удалена с диска), granite-4-h-tiny на structure
  (числа плавают 4–6/12), bonsai-27b Q1_0 (префилл 1109с > таймаута роли), qwen3.5-4b
  (thinking-burn 912с, тумблера нет), lfm2-24b (проза в JSON, few-shot-отравление), e2b на
  entity (стабильно мусорит: mrcp/mupdf).

## ПК — `configs/model_router.yaml`

Замеров на ПК не проводили (GPU-стек; выбирался по назначению ролей).
Id сверены по `GET http://localhost:1234/v1/models` на этой машине.

| Роль | Primary | Fallback |
|---|---|---|
| structure (Call 2/4) | `qwen/qwen3.6-35b-a3b` | magistral-small-2509, qwen3.5-9b |
| translation (Call 2b) | `qwen/qwen3.6-35b-a3b` | magistral-small-2509 |
| deep_analysis | `qwen/qwen3.6-35b-a3b` | magistral-small-2509, qwen3.5-9b |
| reasoning / critique | `mistralai/magistral-small-2509` | qwen3.6-35b-a3b |
| summary | `qwen/qwen3.5-9b` | magistral-small-2509 |
| chat (`ask`) | `qwen/qwen3.5-9b` | — |
| entity_extraction | `qwen/qwen3.5-9b` | magistral-small-2509 |
| rerank | `qwen/qwen3.5-9b` | magistral-small-2509 |
| vision | `qwen/qwen3-vl-8b` | — |
| ocr | `allenai/olmocr-2-7b` | — |
| **embed** | `text-embedding-bge-m3` | **НЕ МЕНЯТЬ** |

На ПК reasoning несёт `magistral-small` (reasoning-модель), heavy-синтез — `qwen3.6-35b-a3b`,
быстрые роли — `qwen3.5-9b` (на GPU Hybrid-режим не нужен: модель и так не «думает» лишнего,
нативного тумблера thinking у 9b нет — см. ноутбуочные уроки ниже).

---

## LM Studio — настройки моделей (ноут)

Путь: `~/.lmstudio/.internal/user-concrete-model-default-config/<pub>/<model>.json`,
поля `load.fields` вступают при следующей загрузке модели.

Стандарт для ноутбучных моделей:

- `contextLength: 16384` (cap! дефолты бывают 262k/1M — патология)
- `cpuThreadPoolSize: 14` (255H = 6P+8E+2LPE)
- `acceleration.offloadRatio: 0` (Arc iGPU-offload = трэш)
- `flashAttention: true`
- **`enableThinking: false`** — всем gemma-4 и qwen-hybridам: нативный тумблер
  `ext.virtualModel.customField.<группа>.enableThinking` (группа — из
  `model-index-cache.json`, например `google.gemma426bA4bQat`, `qwen.qwen3.59b`).
  **`extra_body: {"enable_thinking": false}` из API LM Studio игнорирует.**

Правила-грабли (проверено):

- **id моделей сверять с `lms ls` / `GET /v1/models`**, не по памяти: `qwen3.5-4b` в списке
  **без** префикса `qwen/`; `lms load` с несуществующим id виснет (интерактивный промпт без TTY).
- **Q-кванты (Q1_0 и т.п.) на CPU — не ставьте**: bonsai-27b Q1_0 давал префилл ~7 tok/s
  (1109с на 8k промпт) — де-квант побитовый. Квант ускоряет decode, но не префилл.
- KV-квант (`k/vCacheQuantizationType`) требует FA: `«V Cache Quantization requires flash
  attention»` → либо FA on, либо KV f16.
- Прогон/бенч — только на пустом LM Studio (иначе тайминги врут); kill процессов — по точным
  PID из `/proc/<pid>/cmdline`, не по pgrep-паттерну (паттерн матчит сам kill-шелл).
- Тайминги запросов — server-лог `~/.lmstudio/server-logs/YYYY-MM/*.log` (не ps: `%CPU` —
  среднее за жизнь процесса).

## Анти-галлюцинационная обвязка (в коде пайплайна)

- **entity**: grounded-постфильтр — термы обязаны встречаться в тексте статьи
  (отсев + WARNING в лог).
- **structure→translation**: числа обязаны пережить перевод нетронутыми; расхождение = откат
  на EN-JSON.
- **summary/structure**: числа вывода сверяются с числами статьи; не найденные — маркер
  `> [!warning]` в заметке.
- **critique**: strengths/weaknesses = `{claim, quote}` с дословной цитатой; цитата проверяется
  substring-ом по статье, неподтверждённое помечается «(цитата не подтверждена текстом)».
- **chat (`ask`)**: cite-чек — `[citekey]` не из выдачи RAG помечается в ответе.
- Пустые секции заметки помечаются явно («Секция не извлечена»), VLM-описания фигур —
  дисклеймером (не проверяются).

## RAG (`ask`)

- Гибрид: dense (bge-m3) + sparse (BM25, токенайзер `[A-Za-zА-Яа-яЁё0-9]+`) → RRF-fusion
- RU-вопросы к EN-статьям: sparse-запрос расширяется EN-переводом вопроса (роль `rerank`,
  lfm2.5) — BM25 RU-лексики в EN-корпусе даёт 0 хитов
- Rerank: listwise LLM (lfm2.5-1.2b), пул кандидатов 10 → топ-5
- Коллекции: `papers_full` (статьи, чанк 1200 слов), `papers_notes` (заметки, 800),
  `textbooks` (учебники, 400); `--col both|all` — интерливинг
- Оценка качества: `scripts/rag_eval.py` (hit@K-метрика; матчинг по полному тексту чанка,
  не по preview)
