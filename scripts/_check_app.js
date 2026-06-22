// Mock DOM test for app.js — focus on renderGraph
const fs = require("fs");

function makeEl(tag) {
    return {
        tagName: (tag || "div").toUpperCase(),
        children: [],
        _attrs: {},
        _listeners: {},
        style: {},
        dataset: {},
        className: "",
        hidden: false,
        innerHTML: "",
        textContent: "",
        value: "",
        disabled: false,
        checked: false,
        selected: false,
        type: "",
        id: "",
        classList: {
            _set: new Set(),
            add(c) { this._set.add(c); },
            remove(...c) { for (const x of c) this._set.delete(x); },
            toggle(c, f) { f ? this._set.add(c) : this._set.delete(c); },
            contains(c) { return this._set.has(c); }
        },
        appendChild(c) {
            if (!(c instanceof Object) || !c.tagName) {
                throw new Error("appendChild: not a Node: " + typeof c);
            }
            this.children.push(c);
            return c;
        },
        append(...cs) { for (const c of cs) this.appendChild(c); return this; },
        prepend(...cs) { for (const c of cs) this.children.unshift(c); return this; },
        addEventListener(ev, fn) { (this._listeners[ev] = this._listeners[ev] || []).push(fn); },
        removeEventListener() {},
        setAttribute(k, v) { this._attrs[k] = v; },
        getAttribute(k) { return this._attrs[k]; },
        querySelector(sel) { return null; },
        querySelectorAll(sel) { return []; },
        get title() { return this._attrs.title || ""; },
        set title(v) { this._attrs.title = v; },
    };
}

const elements = {};
elements["#topbar"] = makeEl("header");
elements["#tabs"] = makeEl("nav");
elements["#main"] = makeEl("main");
elements["#content"] = makeEl("section");
elements["#notes-panel"] = makeEl("aside");
elements["#search-box"] = makeEl("input");
elements["#health"] = makeEl("div");

const tabs = [];
for (const name of ["ask", "search", "graph", "library", "logs"]) {
    const t = makeEl("button");
    t.dataset.tab = name;
    tabs.push(t);
}
elements["#tabs"].children = tabs;

const document = {
    addEventListener(ev, fn) {
        if (ev === "DOMContentLoaded") setImmediate(fn);
    },
    querySelector(sel) {
        if (sel === ".tab") return null;
        return elements[sel] || null;
    },
    querySelectorAll(sel) {
        if (sel === ".tab") return tabs;
        return [];
    },
    createElement(tag) { return makeEl(tag); },
    createTextNode(t) { return { tagName: "#text", nodeValue: t }; }
};

const window = {
    markdownit: () => ({ render: (s) => "<p>" + s + "</p>" }),
    cytoscape: (opts) => {
        console.log("[mock cytoscape] elements=" + opts.elements.length);
        return {
            destroy: () => {},
            on: () => {},
            elements: () => ({ unselect: () => ({}), removeClass: () => ({}) }),
            nodes: () => ({ addClass: () => ({}), removeClass: () => ({}) }),
            getElementById: () => ({ length: 0, select: () => {} }),
            style: () => ({ selector: () => ({ style: () => ({ update: () => {} }) }) }),
            fit: () => {},
        };
    },
    addEventListener() {},
};

global.fetch = async () => ({
    text: async () => JSON.stringify({ nodes: [], edges: [] }),
    status: 200,
    ok: true,
});

const code = fs.readFileSync("web/static/app.js", "utf-8");
try {
    eval(code);
    console.log("[ok] app.js loaded");
    setTimeout(() => {
        const graphTab = tabs.find(t => t.dataset.tab === "graph");
        if (graphTab) {
            console.log("[test] click Graph tab");
            const clickHandler = graphTab._listeners.click?.[0];
            if (clickHandler) clickHandler();
        }
    }, 100);
    setTimeout(() => console.log("[done]"), 1500);
} catch (e) {
    console.error("[FAIL]", e.stack);
    process.exit(1);
}