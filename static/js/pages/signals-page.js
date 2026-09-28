/**
 * Horizon & Signal Explorer page.
 *
 * A view over the server contract only: every count, bucket, facet and page
 * comes from GET /api/signals/horizon, /api/signals and /api/signals/<id>
 * (services/detection/signal_query.py). The browser never loads the corpus,
 * never computes a bucket and never filters rows itself - it builds a query
 * string and renders the answer. Text from documents is inserted with
 * textContent only, and rendered with dir="auto" so Arabic, Hebrew and
 * Persian evidence keeps its direction inside a left-to-right page.
 */

const DATA = JSON.parse(document.getElementById('signals-page-data').textContent);
const L = JSON.parse(document.getElementById('signals-page-labels').textContent);
const PAGE_SIZE = Math.min(50, DATA.max_page_size);

const state = {
    tab: 'horizon',
    horizon: { buckets: [], offset: 0 },
    explorer: { offset: 0, sort: 'event_date' },
    savedLoaded: false,
};

function fmt(template, values) {
    return template.replace(/%\((\w+)\)s/g, (_, k) => (values[k] ?? ''));
}

function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
        if (v === null || v === undefined || v === false) continue;
        if (k === 'class') node.className = v;
        else if (k === 'text') node.textContent = v;
        else node.setAttribute(k, v === true ? '' : v);
    }
    for (const child of children) {
        if (child === null || child === undefined) continue;
        node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
}

/** Text from a document: direction decided by its own content. */
function docText(value, extraClass = '') {
    return el('bdi', { dir: 'auto', class: extraClass, text: value ?? '' });
}

function label(value) {
    if (value === 'unrecorded') return L.unrecorded;
    if (value === 'unspecified') return L.unspecified;
    return value;
}

// ---------------------------------------------------------------------------
// Filters -> query string (the same parameters the API documents)
// ---------------------------------------------------------------------------

function checkboxGroup(containerId, name, values) {
    const box = document.getElementById(containerId);
    for (const value of values) {
        const id = `f_${name}_${value}`;
        box.append(el('div', { class: 'form-check form-check-inline m-0' },
            el('input', { class: 'form-check-input', type: 'checkbox', id, name, value }),
            el('label', { class: 'form-check-label small', for: id, text: label(value) })));
    }
}

function fillSelect(selectId, entries) {
    const select = document.getElementById(selectId);
    for (const [value, text] of entries) select.append(el('option', { value, text }));
}

function filterParams() {
    const form = document.getElementById('signalFilters');
    const params = new URLSearchParams();
    const fd = new FormData(form);
    for (const [key, value] of fd.entries()) {
        const v = String(value).trim();
        if (!v) continue;
        if (key === 'category_id' || key === 'keyword_id') {
            v.split(/[\s,]+/).filter(Boolean).forEach((id) => params.append(key, id));
        } else {
            params.append(key, v);
        }
    }
    return params;
}

async function getJson(url) {
    const resp = await fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
    let body = null;
    try { body = await resp.json(); } catch (e) { body = null; }
    if (!resp.ok || !body || body.success === false) {
        const message = body && body.error ? body.error.message : `${L.request_failed} (${resp.status})`;
        throw new Error(message);
    }
    return body;
}

function showError(message) {
    // The panel is the shared error state (components/states.html); only its
    // message changes.
    const box = document.getElementById('signalError');
    box.querySelector('p').textContent = message || '';
    box.classList.toggle('d-none', !message);
}

function pager(containerId, body, onPage) {
    const nav = document.getElementById(containerId);
    nav.replaceChildren();
    if (!body.total) return;
    const from = body.offset + 1;
    const to = Math.min(body.offset + body.items.length, body.total);
    const prev = el('button', { type: 'button', class: 'btn btn-outline-secondary btn-sm', text: L.previous,
        disabled: body.offset === 0 });
    const next = el('button', { type: 'button', class: 'btn btn-outline-secondary btn-sm', text: L.next,
        disabled: to >= body.total });
    prev.addEventListener('click', () => onPage(Math.max(0, body.offset - body.limit)));
    next.addEventListener('click', () => onPage(body.offset + body.limit));
    nav.append(prev, el('span', { class: 'small text-muted', text: fmt(L.page, { from, to, total: body.total }) }), next);
}

function documentCell(item) {
    const doc = item.document;
    if (!doc) return el('td', { text: `#${item.hash_id}` });
    const link = el('a', { href: `/file/${doc.path_id}`, class: 'text-decoration-none' }, docText(doc.file_name));
    const extra = doc.occurrences > 1 ? el('div', { class: 'small text-muted', text: fmt(L.occurrences, { n: doc.occurrences }) }) : null;
    return el('td', {}, link, extra);
}

function confidenceBadge(value) {
    const tone = { high: 'success', medium: 'warning', low: 'secondary' }[value] || 'light text-dark';
    return el('span', { class: `badge bg-${tone}`, text: label(value || 'unrecorded') });
}

function evidenceCell(item) {
    return el('td', { class: 'small' }, docText(item.sentence || item.surface));
}

function detailButton(item, content) {
    const btn = el('button', { type: 'button', class: 'btn btn-link btn-sm p-0 text-start' }, content);
    btn.addEventListener('click', () => openDetail(item.signal_id));
    return btn;
}

// ---------------------------------------------------------------------------
// Horizon
// ---------------------------------------------------------------------------

async function loadHorizon() {
    const params = filterParams();
    params.delete('detector');
    state.horizon.buckets.forEach((b) => params.append('bucket', b));
    params.set('limit', PAGE_SIZE);
    params.set('offset', state.horizon.offset);
    const body = await getJson(`/api/signals/horizon?${params}`);
    renderBuckets(body);
    renderHorizonNotices(body);
    const tbody = document.getElementById('horizonRows');
    tbody.replaceChildren();
    if (!body.items.length) {
        tbody.append(el('tr', {}, el('td', { colspan: 6, class: 'text-muted', text: L.no_signals })));
    }
    for (const item of body.items) {
        const when = item.date_from === item.date_to ? item.date_from : `${item.date_from} – ${item.date_to}`;
        tbody.append(el('tr', {},
            el('td', { class: 'text-nowrap' }, detailButton(item, when)),
            el('td', { text: L[`bucket_${item.bucket}`] || item.bucket }),
            el('td', {}, docText(item.surface), el('div', { class: 'small text-muted', text: [item.calendar, item.language].filter(Boolean).join(' · ') })),
            evidenceCell(item),
            el('td', {}, confidenceBadge(item.confidence)),
            documentCell(item)));
    }
    pager('horizonPager', body, (offset) => { state.horizon.offset = offset; run(); });
}

function renderBuckets(body) {
    const row = document.getElementById('horizonBuckets');
    row.replaceChildren();
    for (const b of body.buckets) {
        const active = body.listed_buckets.includes(b.key);
        const range = [b.from, b.to].filter(Boolean).join(' → ');
        const card = el('button', {
            type: 'button',
            class: `card w-100 text-start ${active ? 'border-primary' : 'text-muted'}`,
            'aria-pressed': active ? 'true' : 'false',
        }, el('div', { class: 'card-body p-2' },
            el('div', { class: 'small fw-semibold', text: L[`bucket_${b.key}`] || b.key }),
            el('div', { class: 'fs-4', text: b.signals }),
            el('div', { class: 'small', text: `${b.contents} ${L.documents}` }),
            el('div', { class: 'small text-muted', text: range })));
        card.addEventListener('click', () => {
            state.horizon.buckets = state.horizon.buckets.length === 1 && state.horizon.buckets[0] === b.key ? [] : [b.key];
            state.horizon.offset = 0;
            run();
        });
        row.append(el('div', { class: 'col-6 col-md-2' }, card));
    }
}

function renderHorizonNotices(body) {
    const box = document.getElementById('horizonNotices');
    box.replaceChildren();
    const u = body.undated;
    if (u.ambiguous + u.unresolved > 0) {
        box.append(el('div', { class: 'alert alert-info py-2 mb-2 small',
            text: fmt(L.undated, { n: u.ambiguous + u.unresolved, a: u.ambiguous, u: u.unresolved }) }));
    }
    const c = body.coverage;
    if (c.not_measured > 0) {
        box.append(el('div', { class: 'alert alert-warning py-2 mb-2 small', text: fmt(L.not_measured, {
            n: c.not_measured, t: c.matching_contents, never: c.never_analysed, stale: c.stale_version, failed: c.failed }) }));
    } else if (c.matching_contents > 0) {
        box.append(el('div', { class: 'small text-muted mb-2', text: fmt(L.all_measured, { t: c.matching_contents }) }));
    }
    box.append(el('div', { class: 'small text-muted', text: `${L.fingerprint}: ${body.query_fingerprint.slice(0, 16)}` }));
}

// ---------------------------------------------------------------------------
// Explorer
// ---------------------------------------------------------------------------

async function loadExplorer() {
    const params = filterParams();
    params.set('sort', state.explorer.sort);
    params.set('limit', PAGE_SIZE);
    params.set('offset', state.explorer.offset);
    const body = await getJson(`/api/signals?${params}`);
    document.getElementById('explorerSummary').textContent = fmt(L.total, { total: body.total, contents: body.contents });
    renderFacets(body.facets);
    const tbody = document.getElementById('explorerRows');
    tbody.replaceChildren();
    if (!body.items.length) {
        tbody.append(el('tr', {}, el('td', { colspan: 6, class: 'text-muted', text: L.no_signals })));
    }
    for (const item of body.items) {
        const value = item.detector === 'places' && item.places
            ? item.places.map((p) => p.label).join(' / ')
            : (item.date_from ? (item.date_from === item.date_to ? item.date_from : `${item.date_from} – ${item.date_to}`) : item.value);
        tbody.append(el('tr', {},
            el('td', {}, detailButton(item, docText(item.surface)), el('div', { class: 'small text-muted', text: `${item.detector} · ${item.signal_type}` })),
            el('td', { class: 'small' }, docText(value), el('div', { class: 'small text-muted', text: item.resolution })),
            evidenceCell(item),
            el('td', {}, confidenceBadge(item.confidence)),
            el('td', { class: 'small', text: item.method || L.unrecorded }),
            documentCell(item)));
    }
    pager('explorerPager', body, (offset) => { state.explorer.offset = offset; run(); });
}

function renderFacets(facets) {
    const aside = document.getElementById('explorerFacets');
    aside.replaceChildren();
    for (const [dim, entries] of Object.entries(facets)) {
        if (!entries.length) continue;
        const list = el('ul', { class: 'list-unstyled small mb-3' });
        for (const e of entries) {
            list.append(el('li', { class: 'd-flex justify-content-between' },
                docText(label(e.value)), el('span', { class: 'text-muted', text: e.count })));
        }
        aside.append(el('div', { class: 'fw-semibold small', text: L[`facet_${dim}`] || dim }), list);
    }
}

// ---------------------------------------------------------------------------
// Detail
// ---------------------------------------------------------------------------

function dl(pairs) {
    const list = el('dl', { class: 'row small mb-2' });
    for (const [k, v] of pairs) {
        if (v === null || v === undefined || v === '') continue;
        list.append(el('dt', { class: 'col-5', text: k }), el('dd', { class: 'col-7 mb-1' }, v instanceof Node ? v : docText(String(v))));
    }
    return list;
}

async function openDetail(signalId) {
    const params = new URLSearchParams();
    const ref = document.getElementById('fReferenceDate').value;
    if (ref) params.set('reference_date', ref);
    const body = document.getElementById('signalDetailBody');
    body.replaceChildren();
    try {
        const s = await getJson(`/api/signals/${signalId}?${params}`);
        document.getElementById('signalDetailTitle').replaceChildren(docText(s.surface));
        body.append(el('h3', { class: 'fs-6', text: L.evidence }),
            el('blockquote', { class: 'border-start ps-2 small' }, docText(s.sentence || s.surface)));
        if (s.places && s.places.length > 1) {
            body.append(el('div', { class: 'small fw-semibold', text: L.ambiguous_places }),
                el('ul', { class: 'small' }, ...s.places.map((p) => el('li', {}, docText(`${p.label} (${p.feature_type}, ${p.country_codes.join(',')})`)))));
        }
        const version = el('span', {}, s.detector_ver, ' ',
            el('span', { class: `badge ${s.detector_version_current ? 'bg-success' : 'bg-warning text-dark'}`,
                text: s.detector_version_current ? L.current : L.outdated }));
        body.append(dl([
            [L.method, s.method || L.unrecorded], [L.confidence, label(s.confidence || 'unrecorded')],
            [L.basis, s.confidence_basis], [L.resolution, s.resolution], [L.language, s.language],
            [L.calendar, s.calendar], [L.orientation, s.text_orientation],
            [L.offsets, `${s.char_start}–${s.char_end}`], [L.detector_version, version],
            [L.detected_at, s.detected_at],
        ]));
        if (s.run) body.append(el('h3', { class: 'fs-6', text: L.run }), dl([[L.run, `${s.run.status} · ${s.run.trigger} · ${s.run.ran_at || ''}`]]));
        body.append(el('h3', { class: 'fs-6', text: L.document }));
        const occ = el('ul', { class: 'small' });
        for (const o of s.occurrences.items) {
            occ.append(el('li', {}, el('a', { href: `/file/${o.path_id}` }, docText(o.file_path))));
        }
        body.append(occ, el('div', { class: 'small text-muted', text: fmt(L.occurrences, { n: s.occurrences.total }) }));
        body.append(el('h3', { class: 'fs-6 mt-2', text: L.structured_evidence }),
            el('pre', { class: 'small bg-light p-2', dir: 'auto', text: JSON.stringify(s.evidence, null, 2) }));
    } catch (err) {
        body.append(el('div', { class: 'alert alert-danger', text: err.message }));
    }
    const panel = document.getElementById('signalDetail');
    if (window.bootstrap && window.bootstrap.Offcanvas) window.bootstrap.Offcanvas.getOrCreateInstance(panel).show();
}

// ---------------------------------------------------------------------------

async function loadSavedSearches() {
    if (state.savedLoaded) return;
    state.savedLoaded = true;
    try {
        const body = await fetch('/api/search/saved', { credentials: 'same-origin' }).then((r) => r.json());
        fillSelect('fSavedSearch', (body.searches || []).map((s) => [s.id, s.name]));
    } catch (e) {
        /* The saved-search list is optional for this page; the filter stays empty. */
    }
}

async function run() {
    showError('');
    try {
        if (state.tab === 'horizon') await loadHorizon();
        else await loadExplorer();
    } catch (err) {
        showError(err.message);
    }
}

function selectTab(tab) {
    state.tab = tab;
    for (const [name, tabId, panelId] of [['horizon', 'tabHorizon', 'panelHorizon'], ['explorer', 'tabExplorer', 'panelExplorer']]) {
        const on = name === tab;
        document.getElementById(tabId).classList.toggle('active', on);
        document.getElementById(tabId).setAttribute('aria-selected', on ? 'true' : 'false');
        document.getElementById(panelId).classList.toggle('d-none', !on);
    }
    run();
}

function init() {
    fillSelect('fSource', Object.entries(DATA.sources || {}).sort((a, b) => String(a[1]).localeCompare(String(b[1]))));
    fillSelect('fSide', Object.entries(DATA.sides || {}).sort((a, b) => String(a[1]).localeCompare(String(b[1]))));
    checkboxGroup('fConfidence', 'confidence', DATA.confidence);
    checkboxGroup('fLanguage', 'language', DATA.languages);
    checkboxGroup('fCalendar', 'calendar', DATA.calendars);
    checkboxGroup('fDetector', 'detector', DATA.detectors);
    fillSelect('explorerSort', DATA.sorts.map((s) => [s, L[`sort_${s}`] || s]));
    document.getElementById('explorerSort').addEventListener('change', (e) => {
        state.explorer.sort = e.target.value;
        state.explorer.offset = 0;
        run();
    });
    const form = document.getElementById('signalFilters');
    form.addEventListener('submit', (e) => {
        e.preventDefault();
        state.horizon.offset = 0;
        state.explorer.offset = 0;
        run();
    });
    form.addEventListener('reset', () => setTimeout(() => form.requestSubmit(), 0));
    document.getElementById('tabHorizon').addEventListener('click', () => selectTab('horizon'));
    document.getElementById('tabExplorer').addEventListener('click', () => selectTab('explorer'));
    loadSavedSearches();
    run();
}

init();
