---
title: "{{ title }}"
authors: [{{ authors | join(', ') }}]
year: {{ year }}
doi: {{ doi or '' }}
zotero_key: {{ item_key }}
citekey: {{ citekey }}
tags: [paper{% for t in auto_tags %}, {{ t }}{% endfor %}]
created: {{ date }}
status: to_read
---

# {{ title }}

> [!abstract] TL;DR
> {{ tldr }}

## 🎯 Проблема и мотивация
{{ problem }}

## 🔬 Метод
{{ method }}

### Ключевые идеи
{% for idea in key_ideas %}
- {{ idea }}
{% endfor %}

## 📊 Эксперименты и результаты
**Эксперименты:** {{ experiments }}

**Результаты:** {{ results }}

{% if figures %}
### Фигуры
{% for fig in figures %}
**{{ fig.caption_detected or fig.figure_id }}**
*Тип:* {{ fig.type }}. {{ fig.vlm_description }}
> 🔍 {{ fig.main_finding }}

{% endfor %}
{% endif %}

## ⚠️ Ограничения (от авторов)
{{ limitations }}

## 🔍 Критический разбор
**Сильные стороны:**
{% for s in strengths %}- {{ s }}
{% endfor %}
**Слабые стороны:**
{% for w in weaknesses %}- {{ w }}
{% endfor %}
{{ overall }}

## 🔗 Связи
**Методы:** {% for m in methods %}[[{{ m }}]] {% endfor %}

**Датасеты:** {% for d in datasets %}[[{{ d }}]] {% endfor %}

**Концепции:** {% for c in concepts %}[[{{ c }}]] {% endfor %}

## 💡 Мои мысли
{{ my_thoughts or '<!-- заполняется вручную -->' }}

## 📝 Заметки при чтении
{{ reading_notes or '<!-- заполняется вручную -->' }}