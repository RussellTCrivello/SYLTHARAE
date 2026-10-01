/** Route-lifecycle smoke for the page-scoped operations poller. */
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const source = await readFile(new URL('../../static/js/modules/core/operations-widget.js', import.meta.url), 'utf8');
const elements = new Map();
const listeners = new Map();
const intervals = new Map();
const cleared = [];
const requests = [];
let nextTimer = 1;

const window = {
    __operationsWidgetState: null,
    addEventListener(name, callback) {
        if (!listeners.has(name)) listeners.set(name, new Set());
        listeners.get(name).add(callback);
    },
    removeEventListener(name, callback) { listeners.get(name)?.delete(callback); },
    dispatchEvent(event) {
        for (const listener of [...(listeners.get(event.type) || [])]) listener(event);
    },
    setInterval(callback, delay) {
        const id = nextTimer++;
        intervals.set(id, { callback, delay });
        return id;
    },
    clearInterval(id) { cleared.push(id); intervals.delete(id); },
};
const document = { getElementById: (id) => elements.get(id) || null };
const AbortController = class {
    constructor() { this.signal = { aborted: false }; }
    abort() { this.signal.aborted = true; }
};
const fetch = async (url, options = {}) => {
    requests.push({ url: String(url), signal: options.signal });
    if (String(url).includes('/summary')) {
        return { ok: true, json: async () => ({ success: true, active: 0, counts: {} }) };
    }
    return { ok: true, json: async () => ({ jobs: [] }) };
};
const context = vm.createContext({ window, document, AbortController, fetch, console });

function installWidget(label) {
    const active = { textContent: '' };
    const rows = { innerHTML: '' };
    const widget = {
        label,
        isConnected: true,
        querySelector(selector) { return selector === '#opsActive' ? active : rows; },
    };
    elements.set('opsWidget', widget);
    elements.set('operations-widget-data', {
        textContent: JSON.stringify({ active: 'active', noOperationsYetStartAnIngestion: 'empty' }),
    });
    return { widget, active, rows };
}

async function settle() {
    await new Promise((resolve) => setImmediate(resolve));
    await new Promise((resolve) => setImmediate(resolve));
}

const first = installWidget('first');
vm.runInContext(source, context, { filename: 'operations-widget.js' });
await settle();
assert.equal(requests.length, 2, 'initial refresh issues one summary and one jobs request');
const firstState = window.__operationsWidgetState;
assert.ok(firstState, 'poller state is registered');
const firstTimer = intervals.get(firstState.timer);
assert.equal(firstTimer.delay, 3000, 'existing poll cadence is retained');

// The navigation shell tells page-scoped modules to stop before replacing the
// current content. A later dashboard visit gets one fresh timer, not a leak.
window.dispatchEvent({ type: 'syltharae:before-page-swap' });
assert.equal(firstState.disposed, true);
assert.equal(firstState.controller.signal.aborted, true);
assert.ok(cleared.includes(firstState.timer));
const firstRequestCount = requests.length;
await firstTimer.callback();
assert.equal(requests.length, firstRequestCount, 'disposed poller cannot issue another request');

const second = installWidget('second');
vm.runInContext(source, context, { filename: 'operations-widget.js' });
await settle();
const secondState = window.__operationsWidgetState;
assert.ok(secondState && secondState !== firstState);
assert.equal(intervals.size, 1, 'only the current page owns a polling timer');
assert.equal(requests.length, 4, 'revisited dashboard performs only its one initial refresh');
assert.equal(second.widget.isConnected, true);

window.dispatchEvent({ type: 'syltharae:before-page-swap' });
assert.equal(intervals.size, 0, 'leaving the dashboard clears its poller');
console.log('Operations widget lifecycle smoke: 12 checks passed');
