/* SciAssist Web — frontend logic.
   Vanilla JS, no build. Talks to /api/*. */
(() => {
    "use strict";

    // ── State ──
    const state = {
        activeTab: "ask",
        selectedCitekeys: [], // multi-select in graph
        lastAskResult: null,
        lastSearchResult: null,
        cy: null,
        graphData: { nodes: [], edges: [] },
        graphMode: "overlay",
        threshold: 0.55,
        topK: 5,
        showLabels: true,
        // loaded notes (cache) citekey → note
        notes: {},
        // multi mode
        multiMode: "compare", // compare | read
    };

    // ── Markdown ──
    const md = window.markdownit({ html: false, linkify: true, breaks: false });
    // Pre-process: turn [[target]] → [target](#wiki/target)
    function preprocessWikiLinks(text) {
        return text.replace(/\[\[([^\]\n]+?)\]\]/g, (_, p1) => {
            const target = p1.split("|", 1)[0].split("#", 1)[0].trim();
            return `[${p1}](#wiki/${encodeURIComponent(target)})`;
        });
    }

    // ── API helpers ──
    async function api(path, opts = {}) {
        const r = await fetch(path, {
            headers: { "Content-Type": "application/json" },
            ...opts,
        });
        const body = await r.text();
        let data;
        try { data = JSON.parse(body); } catch { data = { detail: body }; }
        if (!r.ok) {
            const msg = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
            throw new Error(msg || `HTTP ${r.status}`);
        }
        return data;
    }

    // ── DOM helpers ──
    const $ = (sel, root = document) => root.querySelector(sel);
    const el = (tag, props = {}, ...children) => {
        const n = document.createElement(tag);
        if (props) {
            for (const k of Object.keys(props)) {
                if (k === "className") n.className = props[k];
                else if (k === "style" && typeof props[k] === "string") n.setAttribute("style", props[k]);
                else n[k] = props[k];
            }
        }
        for (const c of children) {
            if (c == null) continue;
            if (typeof c === "string") {
                n.appendChild(document.createTextNode(c));
            } else if (c instanceof Node) {
                n.appendChild(c);
            } else {
                const err = new Error(`el(${tag}): child is ${typeof c}, not a Node. value=${String(c).slice(0,80)}`);
                console.error("[el]", err, "child:", c, "props:", props);
                throw err;
            }
        }
        return n;
    };

    // ── Error reporting: показываем в UI, не молчим ──
    function showError(scope, err) {
        console.error(`[${scope}]`, err);
        const node = $("#content");
        if (!node) return;
        node.innerHTML = "";
        node.appendChild(el("div", { className: "error-box" },
            `[${scope}] ${err?.message || err}`));
    }
    window.addEventListener("error", (ev) => {
        showError("window.error", ev.error || ev.message);
    });
    window.addEventListener("unhandledrejection", (ev) => {
        showError("unhandledrejection", ev.reason);
    });

    // ── Health ping ──
    async function pingHealth() {
        try {
            const h = await api("/api/health");
            for (const k of ["lm_studio", "chroma"]) {
                const node = $(`#health .h-item[data-key="${k}"]`);
                if (!node) continue;
                const v = h[k];
                node.classList.remove("ok", "warn", "err");
                if (v === true) { node.classList.add("ok"); node.querySelector("i").textContent = "✓"; }
                else if (v === false) { node.classList.add("err"); node.querySelector("i").textContent = "✗"; }
                else { node.classList.add("warn"); node.querySelector("i").textContent = "…"; }
            }
            const z = $("#health .h-item[data-key=zotero]");
            const v = h.zotero ? h.zotero.ok : null;
            z.classList.remove("ok", "warn", "err");
            if (v === true) { z.classList.add(h.zotero.write ? "ok" : "warn"); z.querySelector("i").textContent = h.zotero.write ? "✓" : "R/O"; }
            else if (v === false) { z.classList.add("err"); z.querySelector("i").textContent = "✗"; }
            else { z.classList.add("warn"); z.querySelector("i").textContent = "…"; }
        } catch (e) {
            console.warn("health:", e);
        }
    }

    // ── Tabs ──
    const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
    function setTab(name) {
        state.activeTab = name;
        for (const t of $$(".tab")) t.classList.toggle("active", t.dataset.tab === name);
        const content = $("#content");
        content.innerHTML = "";
        try {
            if (name === "ask") renderAsk();
            else if (name === "search") renderSearch();
            else if (name === "graph") renderGraph();
            else if (name === "library") renderLibrary();
            else if (name === "process") renderProcess();
            else if (name === "logs") renderLogs();
        } catch (e) {
            showError(`render:${name}`, e);
        }
    }

    // ════════════════════════════════════════════════════════════════
    // ASK TAB
    // ════════════════════════════════════════════════════════════════
    function renderAsk() {
        const content = $("#content");
        const form = el("div", { className: "form-block" });

        const q = el("textarea", { id: "ask-q", placeholder: "Вопрос к библиотеке…" });
        const opts = el("div", { className: "row" },
            opt("top_k", "top", "5"),
            opt("min_score", "min", "0.4"),
            opt("max_per_paper", "max/paper", "3"),
            chk("rerank", "rerank", true),
            chk("hybrid", "hybrid", true),
            chk("hyde", "hyde", false),
            sel("collection", "col", ["papers_full", "papers_notes", "both"], "papers_full"),
        );
        const btn = el("button", { type: "button" }, "Спросить");
        btn.onclick = doAsk;
        const out = el("div", { id: "ask-out" });
        form.append(el("h2", {}, "💬 Ask — RAG-вопрос по библиотеке"),
            q, opts, btn, out);
        content.appendChild(form);
        if (state.lastAskResult) showAnswer(state.lastAskResult);
    }

    function opt(id, label, val) {
        return el("label", {}, `${label} `, el("input", { id: `ask-${id}`, type: "number", value: val, style: "width:60px" }));
    }
    function chk(id, label, checked) {
        return el("label", {}, el("input", { id: `ask-${id}`, type: "checkbox", checked }), " " + label);
    }
    function sel(id, label, options, val) {
        const s = el("select", { id: `ask-${id}` });
        for (const o of options) {
            const opt = el("option", { value: o }, o);
            if (o === val) opt.selected = true;
            s.appendChild(opt);
        }
        return el("label", {}, `${label} `, s);
    }

    async function doAsk() {
        const q = $("#ask-q").value.trim();
        if (!q) { alert("Введите вопрос"); return; }
        const out = $("#ask-out");
        out.innerHTML = '<div class="spinner"></div> Думаю…';
        try {
            const body = {
                question: q,
                top_k: +$("#ask-top_k").value,
                min_score: +$("#ask-min_score").value,
                max_per_paper: +$("#ask-max_per_paper").value,
                rerank: $("#ask-rerank").checked,
                hybrid: $("#ask-hybrid").checked,
                hyde: $("#ask-hyde").checked,
                collection: $("#ask-collection").value,
            };
            const r = await api("/api/ask", { method: "POST", body: JSON.stringify(body) });
            state.lastAskResult = r;
            showAnswer(r);
        } catch (e) {
            out.innerHTML = "";
            out.appendChild(el("div", { className: "error-box" }, "Ошибка: " + e.message));
        }
    }

    function showAnswer(r) {
        const out = $("#ask-out");
        if (!out) return;
        out.innerHTML = "";
        out.appendChild(el("div", { className: "answer-box" }, r.answer || "(пустой ответ)"));
        if (r.sources && r.sources.length) {
            const t = el("table");
            t.appendChild(el("thead", {}, el("tr", {},
                el("th", {}, "Citekey"),
                el("th", {}, "Section"),
                el("th", {}, "Score"),
                el("th", {}, ""))));
            const tbody = el("tbody");
            for (const s of r.sources) {
                tbody.appendChild(el("tr", {},
                    el("td", {}, s.citekey || "?"),
                    el("td", {}, s.section || ""),
                    el("td", {}, String(s.score)),
                    el("td", {}, el("button", { className: "jump-btn",
                        onclick: () => jumpToGraph(s.citekey) }, "▶ граф"))));
            }
            t.appendChild(tbody);
            out.appendChild(t);
        }
        out.appendChild(el("div", { className: "muted" }, "Модель: " + (r.model || "?")));
    }

    function jumpToGraph(ck) {
        if (!ck) return;
        setTab("graph");
        setTimeout(() => {
            if (state.cy) {
                const node = state.cy.getElementById(ck);
                if (node.length) {
                    state.cy.elements().unselect();
                    node.select();
                    state.cy.fit(state.cy.elements(), 80);
                }
            }
            openNote(ck);
        }, 250);
    }

    // ════════════════════════════════════════════════════════════════
    // SEARCH TAB
    // ════════════════════════════════════════════════════════════════
    function renderSearch() {
        const content = $("#content");
        const q = el("input", { id: "search-q", type: "search", placeholder: "Запрос…", style: "width:300px" });
        const top = el("input", { id: "search-top", type: "number", value: "10", style: "width:60px" });
        const col = el("select", { id: "search-col" });
        for (const o of ["papers_full", "papers_notes"]) {
            col.appendChild(el("option", { value: o }, o));
        }
        const btn = el("button", { type: "button" }, "Искать");
        btn.onclick = doSearch;
        const out = el("div", { id: "search-out" });
        content.append(
            el("h2", {}, "🔍 Search — семантический поиск (без LLM)"),
            el("form", { className: "row", onsubmit: (e) => { e.preventDefault(); doSearch(); } },
                el("label", {}, "q ", q),
                el("label", {}, "top ", top),
                el("label", {}, "col ", col),
                btn,
            ),
            out,
        );
        $("#search-q").addEventListener("keydown", (e) => { if (e.key === "Enter") doSearch(); });
        if (state.lastSearchResult) showSearchResults(state.lastSearchResult);
    }

    async function doSearch() {
        const q = $("#search-q").value.trim();
        if (!q) return;
        const out = $("#search-out");
        out.innerHTML = '<div class="spinner"></div> Ищу…';
        try {
            const r = await api(`/api/search?q=${encodeURIComponent(q)}&top=${$("#search-top").value}&col=${$("#search-col").value}`);
            state.lastSearchResult = r;
            showSearchResults(r);
        } catch (e) {
            out.innerHTML = "";
            out.appendChild(el("div", { className: "error-box" }, "Ошибка: " + e.message));
        }
    }

    function showSearchResults(rows) {
        const out = $("#search-out");
        out.innerHTML = "";
        if (!rows.length) { out.appendChild(el("div", { className: "empty-state" }, "Ничего не найдено.")); return; }
        const t = el("table");
        t.appendChild(el("thead", {}, el("tr", {},
            el("th", {}, "Citekey"), el("th", {}, "Section"), el("th", {}, "Score"), el("th", {}, "Preview"), el("th", {}, ""))));
        const tb = el("tbody");
        for (const r of rows) {
            const score = r.score;
            const cls = score >= 0.7 ? "score-high" : score >= 0.4 ? "score-mid" : "score-low";
            tb.appendChild(el("tr", {},
                el("td", {}, r.citekey || "?"),
                el("td", {}, r.section || ""),
                el("td", { className: cls }, score.toFixed(3)),
                el("td", { className: "muted" }, r.preview || ""),
                el("td", {}, el("button", { className: "jump-btn", onclick: () => jumpToGraph(r.citekey) }, "▶ граф")),
            ));
        }
        t.appendChild(tb);
        out.appendChild(t);
    }

    // ════════════════════════════════════════════════════════════════
    // GRAPH TAB
    // ════════════════════════════════════════════════════════════════
    function renderGraph() {
        const content = $("#content");
        content.innerHTML = "";

        const h2 = el("h2", {}, "🕸 Graph — клик по узлу открывает заметку, Ctrl-клик для мультивыбора");
        content.appendChild(h2);

        const controls = el("div", { className: "graph-controls" });
        const modeSel = el("select", { id: "g-mode" });
        for (const o of ["links", "semantic", "overlay"]) {
            const opt = el("option", { value: o }, o);
            if (o === state.graphMode) opt.selected = true;
            modeSel.appendChild(opt);
        }
        const modeLabel = el("label", {}, "Режим ", modeSel);

        const thr = el("input", { id: "g-thr", type: "range", min: "0", max: "1", step: "0.05", value: String(state.threshold) });
        const thrV = el("span", { id: "g-thr-v" }, String(state.threshold));
        const thrLabel = el("label", {}, "Порог ", thr, thrV);

        const kInput = el("input", { id: "g-k", type: "number", value: String(state.topK), min: "1", max: "20", style: "width:50px" });
        const kLabel = el("label", {}, "top_k ", kInput);

        const lbl = el("input", { id: "g-lbl", type: "checkbox", checked: state.showLabels });
        const lblLabel = el("label", {}, lbl, " подписи");

        const refresh = el("button", { id: "g-refresh" }, "⟳ обновить");

        controls.append(modeLabel, thrLabel, kLabel, lblLabel, refresh);
        content.appendChild(controls);

        const cyBox = el("div", { id: "cy" });
        content.appendChild(cyBox);

        modeSel.addEventListener("change", (e) => { state.graphMode = e.target.value; loadGraph(); });
        thr.addEventListener("input", (e) => { state.threshold = +e.target.value;
            thrV.textContent = state.threshold.toFixed(2);
            debouncedLoadGraph();
        });

        kInput.addEventListener("change", (e) => { state.topK = +e.target.value; loadGraph(); });
        lbl.addEventListener("change", (e) => { state.showLabels = e.target.checked; applyLabelVisibility(); });
        refresh.addEventListener("click", () => loadGraph(true));

        loadGraph();
    }
    let _graphTimer = null;
    function debouncedLoadGraph() {
        clearTimeout(_graphTimer);
        _graphTimer = setTimeout(() => loadGraph(), 250);
    }
    async function loadGraph(refresh = false) {
        const url = `/api/graph?mode=${state.graphMode}&threshold=${state.threshold}&top_k=${state.topK}${refresh ? "&refresh=1" : ""}`;
        try {
            const data = await api(url);
            state.graphData = data;
            drawGraph(data);
        } catch (e) {
            const cy = $("#cy");
            if (cy) cy.innerHTML = `<div class="error-box">Ошибка: ${e.message}</div>`;
            showError("loadGraph", e);
        }
    }

    function applyLabelVisibility() {
        if (!state.cy) return;
        state.cy.style().selector("node").style("label", state.showLabels ? "data(label)" : "").update();
    }

    // палитра по тегу — детерминированно (одинаковый тег = один цвет)
    const TAG_COLORS = ["#5ea9ff","#7c5cff","#46d39a","#f0b84a","#ff6b9d",
                        "#4ec8d4","#b07cff","#ff8c5e","#9ad34e","#5e8cff"];
    function colorForNode(n) {
        const tag = (n.tags && n.tags[0]) || (n.year ? String(n.year) : "");
        if (!tag) return "#5b6477";
        let h = 0; for (const c of tag) h = (h * 31 + c.charCodeAt(0)) >>> 0;
        return TAG_COLORS[h % TAG_COLORS.length];
    }

    function drawGraph(data) {
        const nodes = data.nodes.map(n => ({
            group: "nodes",
            data: { id: n.id, label: n.id, title: n.title, year: n.year,
                    tags: n.tags, degree: n.degree || 0, col: colorForNode(n) },
        }));
        const edges = data.edges.map(e => {
            const d = { id: `${e.source}->${e.target}`, source: e.source,
                        target: e.target, kind: e.kind };
            if (e.score != null) d.score = e.score;
            return { group: "edges", data: d };
        });

        if (state.cy) {
            try { state.cy.stop(); state.cy.destroy(); } catch (_) {}
            state.cy = null;
        }


        state.cy = cytoscape({
            container: document.getElementById("cy"),
            elements: [...nodes, ...edges],
            wheelSensitivity: 0.25,
            minZoom: 0.2, maxZoom: 3,
            layout: layoutOpts(nodes.length),
            style: [
                { selector: "node", style: {
                    "background-color": "data(col)",
                    "background-opacity": 0.95,
                    "label": "data(label)",
                    "color": "#e6e9f0",
                    "font-size": "9px",
                    "font-weight": 500,
                    "text-valign": "bottom",
                    "text-margin-y": 5,
                    "text-outline-width": 2,
                    "text-outline-color": "#0f1117",
                    "text-outline-opacity": 1,
                    "width":  "mapData(degree, 0, 12, 16, 56)",
                    "height": "mapData(degree, 0, 12, 16, 56)",
                    "border-width": 2,
                    "border-color": "#0f1117",
                    "transition-property": "background-color, border-color, opacity, width, height",
                    "transition-duration": "150ms",
                } },
                // выделенный узел — свечение
                { selector: "node:selected", style: {
                    "border-color": "#fff", "border-width": 3,
                    "shadow-blur": 24, "shadow-color": "data(col)",
                    "shadow-opacity": 0.9, "shadow-offset-x": 0, "shadow-offset-y": 0,
                    "color": "#fff", "font-weight": 700, "z-index": 99,
                } },
                { selector: "node.hl",  style: { "border-color": "#fff" } },
                { selector: "node.dim", style: { "opacity": 0.12 } },

                { selector: "edge", style: {
                    "curve-style": "straight",
                    "width": 1.4, "line-color": "#2c3344",
                    "opacity": 0.7,
                    "transition-property": "opacity, line-color, width",
                    "transition-duration": "150ms",
                } },
                { selector: 'edge[kind = "link"]', style: {
                    "line-style": "solid", "line-color": "#5ea9ff", "width": 2, "opacity": 0.85,
                } },
                { selector: 'edge[kind = "semantic"]', style: {
                    "line-style": "dashed", "line-dash-pattern": [4, 4],
                    "line-color": "#f0b84a",
                    // толщина и яркость по score
                    "width": "mapData(score, 0.5, 1, 0.8, 3)",
                    "opacity": "mapData(score, 0.5, 1, 0.25, 0.7)",
                } },
                { selector: "edge.hl",  style: { "opacity": 1, "width": 3, "z-index": 90 } },
                { selector: "edge.dim", style: { "opacity": 0.05 } },
            ],
        });

        const cy = state.cy;
        cy.ready(() => cy.fit(cy.elements(), 40));

        // ── live-подсветка соседей при наведении ──
        cy.on("mouseover", "node", (e) => { if (!state.selectedCitekeys.length) highlightNeighbours(e.target, true); });
        cy.on("mouseout", "node", () => { if (!state.selectedCitekeys.length) clearHighlight(); });

        // ── клик: открыть заметку + зафиксировать подсветку ──
        cy.on("tap", "node", (evt) => {
            const node = evt.target, ck = node.id();
            const multi = evt.originalEvent.ctrlKey || evt.originalEvent.metaKey;
            if (multi) { toggleMultiSelect(ck); return; }
            state.selectedCitekeys = [ck];
            cy.elements().unselect(); node.select();
            highlightNeighbours(node);
            openNote(ck);
        });
        cy.on("tap", (evt) => {
            if (evt.target === cy) {
                clearHighlight();
                state.selectedCitekeys = [];
                cy.elements().unselect();
                $("#notes-panel").hidden = true;
            }
        });
        cy.on("tap", "edge", (evt) => {
            const e = evt.target;
            if (e.data("kind") === "semantic") {
                const ok = confirm(`Семантическая связь (score=${e.data("score")}).\nДобавить [[${e.data("target")}]] в заметку ${e.data("source")}?`);
                if (ok) linkNotes(e.data("source"), e.data("target"));
            }
        });
        cy.on("mouseover", "node", (evt) => {
            const n = evt.target;
            $("#cy").title = n.data("title") ? `${n.data("title")} (${n.data("year") || "?"})` : n.id();
        });

        applyLabelVisibility();
    }

    // выбор раскладки в зависимости от размера графа
    function layoutOpts(n) {
        return {
            name: "cose",
            animate: true, animationDuration: 600,
            randomize: true,
            padding: 40,
            componentSpacing: 90,
            nodeRepulsion: () => 12000,
            idealEdgeLength: (e) => e.data("kind") === "link" ? 80 : 150,
            edgeElasticity: () => 120,
            gravity: 0.3,
            numIter: 1500,
            nodeOverlap: 16,
            nestingFactor: 1.1,
            coolingFactor: 0.95,
            initialTemp: 220,
        };
    }


    function highlightNeighbours(node, soft = false) {
        const cy = state.cy; if (!cy) return;
        const nhd  = node.neighborhood().nodes().union(node);
        const edges = node.connectedEdges();
        cy.batch(() => {
            cy.elements().removeClass("hl dim");
            cy.nodes().difference(nhd).addClass("dim");
            cy.edges().difference(edges).addClass("dim");
            nhd.addClass("hl");
            edges.addClass("hl");
        });
    }

    function clearHighlight() {
        if (state.cy) state.cy.batch(() => state.cy.elements().removeClass("hl dim"));
    }


    function toggleMultiSelect(ck) {
        const i = state.selectedCitekeys.indexOf(ck);
        if (i >= 0) {
            state.selectedCitekeys.splice(i, 1);
        } else {
            if (state.selectedCitekeys.length >= 5) { alert("Лимит 5 статей."); return; }
            state.selectedCitekeys.push(ck);
        }
        state.cy.elements().removeClass("dim");
        if (state.selectedCitekeys.length > 0) {
            const nodes = state.cy.collection();
            for (const id of state.selectedCitekeys) nodes.union(state.cy.getElementById(id));
            const others = state.cy.nodes().difference(nodes);
            others.addClass("dim");
        }
        if (state.selectedCitekeys.length >= 2) {
            openMultiNotes(state.selectedCitekeys);
        } else if (state.selectedCitekeys.length === 1) {
            openNote(state.selectedCitekeys[0]);
        } else {
            $("#notes-panel").hidden = true;
        }
    }

    async function linkNotes(source, target) {
        try {
            const r = await api(`/api/note/${encodeURIComponent(source)}/link`, {
                method: "POST", body: JSON.stringify({ target }),
            });
            if (!r.ok) {
                alert("Не удалось связать: " + (r.error || "unknown"));
                return;
            }
            // Локально меняем ребро semantic → link (быстрее, чем полный refresh)
            const cy = state.cy;
            if (cy) {
                const eid = `${source}->${target}`;
                const eid2 = `${target}->${source}`;
                const edge = cy.getElementById(eid).length ? cy.getElementById(eid)
                            : cy.getElementById(eid2).length ? cy.getElementById(eid2) : null;
                if (edge && edge.length) {
                    edge.remove();
                }
                cy.add({ group: "edges",
                    data: { id: `${source}->${target}`, source, target, kind: "link" } });
            }
            // invalidate кэш semantic-графа
            const r2 = await api(`/api/graph?mode=semantic&refresh=1`);
            showToast(`✓ Связь [[${target}]] добавлена в ${source}${r.changed ? "" : " (уже было)"}`);
        } catch (e) {
            alert("Не удалось связать: " + e.message);
        }
    }

    function showToast(msg) {
        const t = el("div", {
            style: "position:fixed;top:60px;right:20px;background:var(--accent);color:#000;"
                + "padding:8px 14px;border-radius:4px;z-index:1000;font-weight:500;"
        }, msg);
        document.body.appendChild(t);
        setTimeout(() => t.remove(), 2500);
    }

    // ════════════════════════════════════════════════════════════════
    // NOTE PANEL
    // ════════════════════════════════════════════════════════════════
    async function openNote(ck) {
        const panel = $("#notes-panel");
        panel.hidden = false;
        panel.innerHTML = '<div class="spinner"></div>';
        try {
            let n = state.notes[ck];
            if (!n) {
                n = await api(`/api/note/${encodeURIComponent(ck)}`);
                state.notes[ck] = n;
            }
            renderNote(panel, n);
        } catch (e) {
            panel.innerHTML = `<div class="error-box">${e.message}</div>`;
        }
    }

    function renderNote(panel, n) {
        panel.innerHTML = "";
        const header = el("div", { className: "note-header" },
            el("h3", {}, n.title || n.citekey),
            el("div", { className: "actions" },
                el("button", { onclick: () => {
                    navigator.clipboard?.writeText(n.citekey);
                } }, "📋 citekey"),
                el("button", { onclick: () => { panel.hidden = true; } }, "✕"),
            ),
        );
        const body = el("div", { className: "note-body" });
        body.innerHTML = md.render(preprocessWikiLinks(n.markdown));
        // wire wiki-links to jump
        for (const a of body.querySelectorAll("a")) {
            if (a.getAttribute("href")?.startsWith("#wiki/")) {
                a.onclick = (e) => {
                    e.preventDefault();
                    const target = decodeURIComponent(a.getAttribute("href").slice(6));
                    jumpToGraph(target);
                };
            }
        }
        panel.append(header, body);
    }

    async function openMultiNotes(citekeys) {
        const panel = $("#notes-panel");
        panel.hidden = false;
        panel.innerHTML = '<div class="spinner"></div>';
        try {
            // fetch missing
            const need = citekeys.filter(ck => !state.notes[ck]);
            if (need.length) {
                const r = await api(`/api/notes?keys=${encodeURIComponent(need.join(","))}`);
                for (const n of r.notes) state.notes[n.citekey] = n;
            }
            const notes = citekeys.map(ck => state.notes[ck]).filter(Boolean);
            renderMultiNotes(panel, notes);
        } catch (e) {
            panel.innerHTML = `<div class="error-box">${e.message}</div>`;
        }
    }

    function renderMultiNotes(panel, notes) {
        panel.innerHTML = "";

        const toolbar = el("div", { className: "multi-toolbar" },
            el("strong", {}, `${notes.length} статей:`),
            el("button", { id: "btn-compare", className: state.multiMode === "compare" ? "active" : "",
                onclick: () => { state.multiMode = "compare"; renderMultiNotes(panel, notes); } }, "⊞ Сравнить"),
            el("button", { id: "btn-read", className: state.multiMode === "read" ? "active" : "",
                onclick: () => { state.multiMode = "read"; renderMultiNotes(panel, notes); } }, "☰ Читать"),
            el("span", { className: "muted", style: "margin-left:auto" },
                "лимит 5"),
        );
        panel.appendChild(toolbar);

        if (state.multiMode === "compare") {
            renderCompare(panel, notes);
        } else {
            renderRead(panel, notes);
        }
    }

    function sectionNames(notes) {
        // union of all section names
        const set = new Set();
        for (const n of notes) for (const k of Object.keys(n.sections || {})) set.add(k);
        return Array.from(set);
    }

    function renderCompare(panel, notes) {
        const sects = sectionNames(notes);
        const sel = el("select", { id: "cmp-section" });
        for (const s of sects) {
            const o = el("option", { value: s }, s);
            sel.appendChild(o);
        }
        sel.onchange = () => updateCompareGrid(notes, sel.value);
        panel.appendChild(el("div", { className: "row" },
            el("label", {}, "Секция: ", sel),
        ));
        const grid = el("div", { id: "cmp-grid", className: "compare-mode" });
        panel.appendChild(grid);
        updateCompareGrid(notes, sects[0] || "_header");
    }

    function updateCompareGrid(notes, section) {
        const grid = $("#cmp-grid");
        if (!grid) return;
        grid.innerHTML = "";
        const cols = el("div", { className: "compare-cols",
            style: `grid-template-columns: repeat(${notes.length}, 1fr);` });
        for (const n of notes) {
            const text = n.sections?.[section];
            const col = el("div", { className: "compare-col" },
                el("h4", {}, n.citekey));
            if (text) {
                const inner = el("div");
                inner.innerHTML = md.render(preprocessWikiLinks(text));
                col.appendChild(inner);
            } else {
                col.appendChild(el("div", { className: "empty" }, "— нет секции —"));
            }
            cols.appendChild(col);
        }
        grid.appendChild(cols);
    }

    function renderRead(panel, notes) {
        const wrap = el("div", { className: "read-mode" });
        for (const n of notes) {
            const h = el("h2", {}, n.citekey);
            const body = el("div", { className: "note-body" });
            body.innerHTML = md.render(preprocessWikiLinks(n.markdown));
            for (const a of body.querySelectorAll("a")) {
                if (a.getAttribute("href")?.startsWith("#wiki/")) {
                    a.onclick = (e) => {
                        e.preventDefault();
                        const target = decodeURIComponent(a.getAttribute("href").slice(6));
                        jumpToGraph(target);
                    };
                }
            }
            wrap.append(h, body, el("hr", {}));
        }
        panel.appendChild(wrap);
    }

    // ════════════════════════════════════════════════════════════════
    // LIBRARY TAB
    // ════════════════════════════════════════════════════════════════
    async function renderLibrary() {
        const content = $("#content");
        content.appendChild(el("h2", {}, "📚 Library"));
        try {
            const stats = await api("/api/stats");
            const grid = el("div", { className: "stats-grid" });
            grid.appendChild(statCard("Чанков (full)", stats.papers_full_chunks || 0));
            grid.appendChild(statCard("Чанков (notes)", stats.papers_notes_chunks || 0));
            grid.appendChild(statCard("Индексировано", stats.indexed_papers || 0));
            grid.appendChild(statCard("Embed model", stats.embed_model || "?", "", true));
            content.appendChild(grid);

            // Zotero health (для баннера)
            const h = await api("/api/health").catch(() => ({ zotero: { ok: null } }));
            const z = (h && h.zotero) || {};
            const isSqlite = z.backend === "sqlite";
            const noZotero = !z.ok;

            content.appendChild(el("h3", {}, "Коллекция Zotero"));
            if (noZotero) {
                content.appendChild(el("div", { className: "error-box" },
                    "Zotero недоступен. Запусти Zotero для полного функционала."));
            } else if (isSqlite) {
                content.appendChild(el("div", { className: "error-box" },
                    "Zotero запущен в read-only режиме (SQLite fallback) — метаданные статей недоступны. Запусти Zotero для получения citekey/title."));
            }

            const items = await api("/api/zotero/list");
            const t = el("table");
            t.appendChild(el("thead", {}, el("tr", {},
                el("th", {}, "Citekey / Item Key"), el("th", {}, "Title"), el("th", {}, "Year"), el("th", {}, ""))));
            const tb = el("tbody");
            const checked = new Set();
            for (const it of items) {
                // Эвристика: настоящий citekey = в нижнем регистре, без пробелов, может быть с годом.
                // Zotero item key = 8 алфавитно-цифровых символов (типа II3NZPU8).
                const ck = it.citekey || "";
                const looksLikeItemKey = /^[A-Z0-9]{6,10}$/.test(ck);
                const hasMeta = it.title && it.title.length > 0;
                const selectable = !looksLikeItemKey && hasMeta;

                const cb = el("input", { type: "checkbox" });
                if (!selectable) cb.disabled = true;
                cb.addEventListener("change", () => {
                    if (cb.checked) checked.add(it.citekey); else checked.delete(it.citekey);
                });
                const label = looksLikeItemKey ? `${ck} [item key]` : ck;
                tb.appendChild(el("tr", {},
                    el("td", {}, label),
                    el("td", {}, it.title || (looksLikeItemKey ? "(нет метаданных)" : "")),
                    el("td", {}, String(it.year || "")),
                    el("td", {}, cb),
                ));
            }
            t.appendChild(tb);
            content.appendChild(t);

            const btn = el("button", { type: "button" }, "Показать выбранные");
            btn.onclick = () => {
                if (checked.size === 0) { alert("Ничего не выбрано."); return; }
                if (checked.size > 5) { alert("Лимит 5."); return; }
                openMultiNotes(Array.from(checked));
            };
            content.appendChild(el("div", { style: "margin-top:12px" }, btn));
        } catch (e) {
            content.appendChild(el("div", { className: "error-box" }, "Ошибка: " + e.message));
        }
    }

    function statCard(label, value, suffix = "", small = false) {
        return el("div", { className: "stat-card" },
            el("div", { className: "label" }, label),
            el("div", { className: "value", style: small ? "font-size:13px" : "" },
                value + (suffix || "")),
        );
    }

    // ════════════════════════════════════════════════════════════════
    // PROCESS TAB
    // ════════════════════════════════════════════════════════════════
    let _processPollTimer = null;

    function renderProcess() {
        const content = $("#content");
        content.appendChild(el("h2", {}, "⚙️ Process — запуск долгих операций"));
        const hint = el("div", { className: "muted", style: "margin-bottom:10px;font-size:12px" },
            "Все операции идут через одну очередь (LM Studio однопоточный). Прогресс обновляется каждые ~2с.");
        content.appendChild(hint);

        const grid = el("div", { className: "process-grid" });

        // Process
        const pcProc = el("div", { className: "process-card" });
        pcProc.append(
            el("h4", {}, "📄 Process — обработка статьи"),
            el("div", { className: "hint" }, "OCR → фигуры → индексация → заметка (2 мин)"),
        );
        const inProc = el("input", { id: "in-process", placeholder: "citekey (например, vaswani2023attention)" });
        const btnProc = el("button", { type: "button" }, "Запустить");
        const btnQueue = el("button", { type: "button", className: "alt" }, "Обработать Queue");
        pcProc.append(inProc, el("div", { className: "row-btns" }, btnProc, btnQueue));
        grid.appendChild(pcProc);

        // Gaps
        const pcGaps = el("div", { className: "process-card" });
        pcGaps.append(
            el("h4", {}, "🔍 Gaps — gap analysis по теме"),
            el("div", { className: "hint" }, "Топ-N статей → LLM → структурированный список"),
        );
        const inGaps = el("input", { id: "in-gaps", placeholder: "тема (например, retrieval augmented generation)" });
        const btnGaps = el("button", { type: "button" }, "Найти gaps");
        pcGaps.append(inGaps, el("div", { className: "row-btns" }, btnGaps));
        grid.appendChild(pcGaps);

        // Draft
        const pcDraft = el("div", { className: "process-card" });
        pcDraft.append(
            el("h4", {}, "✍️ Draft — Related Work"),
            el("div", { className: "hint" }, "Сгенерировать раздел Related Work с \\cite{}"),
        );
        const inDraft = el("input", { id: "in-draft", placeholder: "тема раздела" });
        const btnDraft = el("button", { type: "button" }, "Сгенерировать");
        pcDraft.append(inDraft, el("div", { className: "row-btns" }, btnDraft));
        grid.appendChild(pcDraft);

        // Analyze
        const pcAna = el("div", { className: "process-card" });
        pcAna.append(
            el("h4", {}, "🔬 Analyze — критический разбор"),
            el("div", { className: "hint" }, "Требуется markdown статьи (сначала process)"),
        );
        const inAna = el("input", { id: "in-analyze", placeholder: "citekey" });
        const btnAna = el("button", { type: "button" }, "Анализ");
        pcAna.append(inAna, el("div", { className: "row-btns" }, btnAna));
        grid.appendChild(pcAna);

        content.appendChild(grid);

        // Jobs table
        const jobsTitle = el("h3", { style: "margin-top:8px" }, "📋 Задачи");
        content.appendChild(jobsTitle);
        const jobsBox = el("div", { id: "jobs-box" });
        content.appendChild(jobsBox);

        // Handlers
        btnProc.onclick = async () => {
            const ck = inProc.value.trim();
            if (!ck) { alert("Введи citekey"); return; }
            try {
                await api("/api/process", { method: "POST",
                    body: JSON.stringify({ citekey: ck, only: "full" }) });
                inProc.value = "";
                refreshJobs();
            } catch (e) { alert("Ошибка: " + e.message); }
        };
        btnQueue.onclick = async () => {
            if (!confirm("Обработать всю очередь Zotero? Это займёт много времени.")) return;
            try {
                await api("/api/process-queue", { method: "POST", body: "{}" });
                refreshJobs();
            } catch (e) { alert("Ошибка: " + e.message); }
        };
        btnGaps.onclick = async () => {
            const t = inGaps.value.trim();
            if (!t) { alert("Введи тему"); return; }
            try {
                await api("/api/gaps", { method: "POST",
                    body: JSON.stringify({ topic: t, papers: 10 }) });
                inGaps.value = "";
                refreshJobs();
            } catch (e) { alert("Ошибка: " + e.message); }
        };
        btnDraft.onclick = async () => {
            const t = inDraft.value.trim();
            if (!t) { alert("Введи тему"); return; }
            try {
                await api("/api/draft/related-work", { method: "POST",
                    body: JSON.stringify({ topic: t, papers: 10 }) });
                inDraft.value = "";
                refreshJobs();
            } catch (e) { alert("Ошибка: " + e.message); }
        };
        btnAna.onclick = async () => {
            const ck = inAna.value.trim();
            if (!ck) { alert("Введи citekey"); return; }
            try {
                await api("/api/analyze", { method: "POST",
                    body: JSON.stringify({ citekey: ck, mode: "critique" }) });
                inAna.value = "";
                refreshJobs();
            } catch (e) { alert("Ошибка: " + e.message); }
        };

        refreshJobs();
        // Поллинг каждые 2с пока вкладка активна
        if (_processPollTimer) clearInterval(_processPollTimer);
        _processPollTimer = setInterval(() => {
            if (state.activeTab !== "process") return;
            refreshJobs();
        }, 2000);
    }

    async function refreshJobs() {
        const box = $("#jobs-box");
        if (!box) return;
        try {
            const data = await api("/api/jobs?limit=20");
            renderJobs(box, data.jobs || []);
        } catch (e) {
            box.innerHTML = `<div class="error-box">${e.message}</div>`;
        }
    }

    function renderJobs(box, jobs) {
        box.innerHTML = "";
        if (!jobs.length) {
            box.appendChild(el("div", { className: "empty-state" }, "Нет задач. Запусти одну выше ↑"));
            return;
        }
        const t = el("table", { className: "jobs-table" });
        t.appendChild(el("thead", {}, el("tr", {},
            el("th", {}, "Job"), el("th", {}, "Kind"), el("th", {}, "Status"),
            el("th", {}, "Step"), el("th", {}, "Args"), el("th", {}, ""))));
        const tbody = el("tbody");
        for (const j of jobs) {
            const status = j.status || "queued";
            const step = j.step || (status === "queued" ? "в очереди…" : "");
            const args = JSON.stringify(j.args || {});
            const argsShort = args.length > 60 ? args.slice(0, 60) + "…" : args;
            const tr = el("tr", {},
                el("td", { className: "muted", style: "font-family:monospace;font-size:11px" }, j.id),
                el("td", {}, j.kind),
                el("td", {}, el("span", { className: `status-badge status-${status}` }, status)),
                el("td", { className: "step-cell", title: step }, step || "—"),
                el("td", { className: "step-cell", title: args }, argsShort),
                el("td", {}, makeJobActions(j)),
            );
            tbody.appendChild(tr);
        }
        t.appendChild(tbody);
        box.appendChild(t);
    }

    function makeJobActions(j) {
        const wrap = el("div", { className: "row-btns" });
        if (j.status === "queued" || j.status === "running") {
            const cancel = el("button", { className: "alt",
                onclick: async () => {
                    try {
                        await api(`/api/jobs/${j.id}`, { method: "DELETE" });
                        refreshJobs();
                    } catch (e) { alert(e.message); }
                } }, "✕ отменить");
            wrap.appendChild(cancel);
        }
        if (j.status === "done" && j.result && j.result.ok) {
            const show = el("button", { className: "alt",
                onclick: () => showJobResult(j) }, "результат");
            wrap.appendChild(show);
        }
        if (j.status === "error") {
            const show = el("button", { className: "alt",
                onclick: () => alert(j.error || "unknown error") }, "ошибка");
            wrap.appendChild(show);
        }
        return wrap;
    }

    function showJobResult(j) {
        const d = j.result.data || {};
        let body = "";
        if (j.kind === "process") {
            body = `Markdown: ${d.md}\nЗаметка: ${d.note}\nФигур: ${d.figures}\nЧанков full: ${d.chunks_full}\nЧанков notes: ${d.chunks_notes}`;
        } else if (j.kind === "gaps") {
            body = JSON.stringify(d.parsed || d.raw || "", null, 2);
        } else if (j.kind === "draft") {
            body = d.draft || "";
        } else if (j.kind === "analyze") {
            body = JSON.stringify(d.parsed || d.raw || "", null, 2);
        } else {
            body = JSON.stringify(d, null, 2);
        }
        const out = $("#content");
        out.appendChild(el("div", { className: "answer-box", style: "white-space:pre-wrap;font-size:12px;max-height:50vh;overflow:auto" }, body));
    }

    // ════════════════════════════════════════════════════════════════
    // LOGS TAB
    // ════════════════════════════════════════════════════════════════
    async function renderLogs() {
        const content = $("#content");
        content.appendChild(el("h2", {}, "📊 Logs — последние LLM-вызовы"));
        const tail = el("input", { id: "logs-tail", type: "number", value: "30", style: "width:70px" });
        const btn = el("button", { type: "button" }, "Обновить");
        btn.onclick = doLogs;
        content.appendChild(el("form", { className: "row", onsubmit: (e) => { e.preventDefault(); doLogs(); } },
            el("label", {}, "tail ", tail), btn));
        const out = el("div", { id: "logs-out" });
        content.appendChild(out);
        doLogs();
    }

    async function doLogs() {
        const out = $("#logs-out");
        out.innerHTML = '<div class="spinner"></div>';
        try {
            const rows = await api(`/api/logs?tail=${$("#logs-tail").value}`);
            if (!rows.length) { out.innerHTML = '<div class="empty-state">Лог пуст.</div>'; return; }
            const t = el("table");
            t.appendChild(el("thead", {}, el("tr", {},
                el("th", {}, "Time"), el("th", {}, "Model"),
                el("th", {}, "Task"), el("th", {}, "↑"), el("th", {}, "↓"),
                el("th", {}, "ms"), el("th", {}, "Err"))));
            const tb = el("tbody");
            for (const r of rows) {
                const ts = r.ts ? r.ts.slice(11, 19) : "";
                tb.appendChild(el("tr", {},
                    el("td", { className: "muted" }, ts),
                    el("td", {}, (r.model || "").slice(0, 30)),
                    el("td", {}, r.task || ""),
                    el("td", {}, String(r.prompt_tokens ?? "")),
                    el("td", {}, String(r.completion_tokens ?? "")),
                    el("td", {}, String(r.duration_ms ?? "")),
                    el("td", { className: r.error ? "score-low" : "" }, r.error ? r.error.slice(0, 50) : ""),
                ));
            }
            t.appendChild(tb);
            out.innerHTML = "";
            out.appendChild(t);
        } catch (e) {
            out.innerHTML = "";
            out.appendChild(el("div", { className: "error-box" }, e.message));
        }
    }

    // ════════════════════════════════════════════════════════════════
    // INIT
    // ════════════════════════════════════════════════════════════════
    function init() {
        try {
            // Проверка: cytoscape загружен?
            if (typeof window.cytoscape !== "function") {
                const banner = el("div", { className: "error-box",
                    style: "margin:10px 14px;" },
                    "Не удалось загрузить Cytoscape.js — проверь /vendor/cytoscape.min.js");
                $("#main")?.prepend(banner);
            }
            for (const t of $$(".tab")) {
                t.addEventListener("click", () => setTab(t.dataset.tab));
            }
            $("#search-box").addEventListener("keydown", (e) => {
                if (e.key === "Enter") {
                    setTab("search");
                    setTimeout(() => {
                        const q = $("#search-q");
                        if (q) { q.value = e.target.value; doSearch(); }
                    }, 50);
                }
            });
            pingHealth();
            setInterval(pingHealth, 10_000);
            setTab("ask");
        } catch (e) {
            showError("init", e);
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();