Извлеки из текста научной статьи все упомянутые методы, датасеты и концепции.

## Правила нормализации имён
- kebab-case: "Multi-Head Attention" → "multi-head-attention"
- Без версий и размеров: "BERT-large" → "bert", "GPT-4" → "gpt"
- Развернуть аббревиатуры если очевидно: "MHA" → "multi-head-attention"
- Включать общие: "dropout", "layer-normalization", "residual-connection"

## Пример входа
"We use BERT fine-tuned on ImageNet with dropout=0.1 and layer normalization..."

## Пример выхода
{"methods": ["bert", "fine-tuning", "dropout", "layer-normalization"], "datasets": ["imagenet"], "concepts": ["transfer-learning", "regularization"]}

## Текст статьи
{{ paper_text }}

Верни JSON (только JSON, без пояснений):
{"methods": [...], "datasets": [...], "concepts": [...]}
