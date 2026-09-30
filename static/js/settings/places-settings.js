/**
 * Geographic Locations (Places) Management (Settings → Geographic Locations).
 *
 * Provides complete visual front-end management for geographic places and
 * multilingual names (Phase 2):
 * - Search, filter by feature type, country code, and active/retired status.
 * - Add new custom locations with coordinates and multilingual aliases.
 * - Edit existing locations and add aliases.
 * - Toggle Active / Retired status.
 * - Delete user-managed locations.
 * - Strict DOM construction (no innerHTML), accessible fields and buttons.
 */
(function () {
    'use strict';

    let currentPage = 1;
    let totalPages = 1;
    const perPage = 25;

    function csrfToken() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        if (meta) return meta.getAttribute('content');
        if (window.__csrfToken) return window.__csrfToken;
        return document.cookie.split('; ').reduce(function (acc, pair) {
            return acc || (pair.indexOf('csrf_token=') === 0 ? pair.split('=')[1] : acc);
        }, '');
    }

    function setStatus(message, isError) {
        const status = document.getElementById('placesStatus');
        if (status) {
            status.textContent = message || '';
            status.className = isError ? 'text-danger small' : 'text-success small';
        }
    }

    function clearNode(node) {
        if (!node) return;
        if (typeof node.replaceChildren === 'function') {
            node.replaceChildren();
        } else {
            while (node.firstChild) node.removeChild(node.firstChild);
        }
    }

    function createIcon(cls, meClass) {
        const icon = document.createElement('i');
        icon.className = 'bi ' + cls + (meClass ? ' ' + meClass : '');
        icon.setAttribute('aria-hidden', 'true');
        return icon;
    }

    async function loadPlaces(page) {
        if (page) currentPage = page;
        const tbody = document.getElementById('placesTableBody');
        if (!tbody) return;

        const q = (document.getElementById('placesSearchInput')?.value || '').trim();
        const ftype = document.getElementById('placesFilterType')?.value || '';
        const country = (document.getElementById('placesFilterCountry')?.value || '').trim().toUpperCase();
        const status = document.getElementById('placesFilterStatus')?.value || 'all';

        let url = `/api/places?page=${currentPage}&per_page=${perPage}&status=${encodeURIComponent(status)}`;
        if (q) url += `&q=${encodeURIComponent(q)}`;
        if (ftype) url += `&feature_type=${encodeURIComponent(ftype)}`;
        if (country) url += `&country=${encodeURIComponent(country)}`;

        clearNode(tbody);
        const loadTr = document.createElement('tr');
        const loadTd = document.createElement('td');
        loadTd.setAttribute('colspan', '8');
        loadTd.className = 'text-muted text-center py-3';
        const spin = document.createElement('span');
        spin.className = 'spinner-border spinner-border-sm me-2';
        loadTd.append(spin, document.createTextNode('Loading locations…'));
        loadTr.appendChild(loadTd);
        tbody.appendChild(loadTr);

        try {
            const res = await fetch(url, { headers: { 'Accept': 'application/json' } });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            renderPlaces(data.data || [], data.pagination || {});
        } catch (err) {
            clearNode(tbody);
            const errTr = document.createElement('tr');
            const errTd = document.createElement('td');
            errTd.setAttribute('colspan', '8');
            errTd.className = 'text-danger text-center py-3';
            errTd.textContent = 'Error loading locations: ' + err.message;
            errTr.appendChild(errTd);
            tbody.appendChild(errTr);
        }
    }

    function renderPlaces(items, pagination) {
        const tbody = document.getElementById('placesTableBody');
        if (!tbody) return;
        clearNode(tbody);

        totalPages = pagination.pages || 1;
        const countSpan = document.getElementById('placesCount');
        if (countSpan) {
            countSpan.textContent = `Showing ${items.length} of ${pagination.total || items.length} locations (Page ${pagination.page || 1} of ${totalPages})`;
        }

        const prevBtn = document.getElementById('placesPrevBtn');
        const nextBtn = document.getElementById('placesNextBtn');
        if (prevBtn) prevBtn.disabled = currentPage <= 1;
        if (nextBtn) nextBtn.disabled = currentPage >= totalPages;

        if (items.length === 0) {
            const emptyTr = document.createElement('tr');
            const emptyTd = document.createElement('td');
            emptyTd.setAttribute('colspan', '8');
            emptyTd.className = 'text-muted text-center py-4';
            emptyTd.textContent = 'No geographic locations found matching your filter.';
            emptyTr.appendChild(emptyTd);
            tbody.appendChild(emptyTr);
            return;
        }

        for (const place of items) {
            const tr = document.createElement('tr');
            if (place.retired) tr.classList.add('table-secondary', 'text-muted');

            // Key
            const tdKey = document.createElement('td');
            tdKey.className = 'font-monospace small';
            tdKey.textContent = place.place_key;
            tr.appendChild(tdKey);

            // Label
            const tdLabel = document.createElement('td');
            tdLabel.className = 'fw-semibold';
            tdLabel.textContent = place.label;
            tr.appendChild(tdLabel);

            // Feature Type
            const tdType = document.createElement('td');
            const typeBadge = document.createElement('span');
            typeBadge.className = 'badge ' + (place.feature_type === 'city' ? 'bg-info' : (place.feature_type === 'country' ? 'bg-primary' : 'bg-secondary'));
            typeBadge.textContent = place.feature_type;
            tdType.appendChild(typeBadge);
            tr.appendChild(tdType);

            // Countries
            const tdCountries = document.createElement('td');
            for (const cc of (place.country_codes || [])) {
                const b = document.createElement('span');
                b.className = 'badge bg-light text-dark border me-1';
                b.textContent = cc;
                tdCountries.appendChild(b);
            }
            tr.appendChild(tdCountries);

            // Coordinates
            const tdCoords = document.createElement('td');
            tdCoords.className = 'small font-monospace';
            if (place.latitude !== null && place.longitude !== null && place.latitude !== undefined && place.longitude !== undefined) {
                tdCoords.textContent = `${Number(place.latitude).toFixed(4)}, ${Number(place.longitude).toFixed(4)}`;
            } else {
                const dash = document.createElement('span');
                dash.className = 'text-muted';
                dash.textContent = '—';
                tdCoords.appendChild(dash);
            }
            tr.appendChild(tdCoords);

            // Source
            const tdSource = document.createElement('td');
            const srcBadge = document.createElement('span');
            srcBadge.className = 'badge ' + (place.source === 'user' ? 'bg-warning text-dark' : 'bg-light text-muted border');
            srcBadge.textContent = place.source === 'user' ? 'User-Created' : 'Seed (Wikidata)';
            tdSource.appendChild(srcBadge);
            tr.appendChild(tdSource);

            // Status
            const tdStatus = document.createElement('td');
            const statusBadge = document.createElement('span');
            statusBadge.className = 'badge ' + (place.retired ? 'bg-secondary' : 'bg-success');
            statusBadge.textContent = place.retired ? 'Retired' : 'Active';
            tdStatus.appendChild(statusBadge);
            tr.appendChild(tdStatus);

            // Actions
            const tdActions = document.createElement('td');
            tdActions.className = 'text-nowrap';

            // Edit button
            const editBtn = document.createElement('button');
            editBtn.type = 'button';
            editBtn.className = 'btn btn-sm btn-outline-primary me-1';
            editBtn.append(createIcon('bi-pencil', 'me-1'), document.createTextNode('Edit'));
            editBtn.addEventListener('click', () => openEditModal(place));
            tdActions.appendChild(editBtn);

            // Retire/Activate toggle button
            const toggleBtn = document.createElement('button');
            toggleBtn.type = 'button';
            toggleBtn.className = 'btn btn-sm ' + (place.retired ? 'btn-outline-success me-1' : 'btn-outline-warning me-1');
            toggleBtn.append(
                createIcon(place.retired ? 'bi-check-circle' : 'bi-pause-circle', 'me-1'),
                document.createTextNode(place.retired ? 'Activate' : 'Retire')
            );
            toggleBtn.addEventListener('click', () => togglePlaceRetired(place.place_key, !place.retired));
            tdActions.appendChild(toggleBtn);

            // Delete button (if user created)
            if (place.source === 'user') {
                const delBtn = document.createElement('button');
                delBtn.type = 'button';
                delBtn.className = 'btn btn-sm btn-outline-danger';
                delBtn.append(createIcon('bi-trash'));
                delBtn.title = 'Delete user-created location';
                delBtn.addEventListener('click', () => deletePlace(place.place_key));
                tdActions.appendChild(delBtn);
            }

            tr.appendChild(tdActions);
            tbody.appendChild(tr);
        }
    }

    async function togglePlaceRetired(placeKey, newRetired) {
        try {
            const res = await fetch(`/api/places/${encodeURIComponent(placeKey)}`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken(),
                    'Accept': 'application/json',
                },
                body: JSON.stringify({ retired: newRetired }),
            });
            if (!res.ok) {
                const err = await res.json();
                throw new Error(err.error?.message || `HTTP ${res.status}`);
            }
            setStatus(`Place ${placeKey} marked as ${newRetired ? 'retired' : 'active'}.`, false);
            loadPlaces();
        } catch (err) {
            setStatus(`Action failed: ${err.message}`, true);
        }
    }

    async function deletePlace(placeKey) {
        if (!window.confirm(`Are you sure you want to delete custom location ${placeKey}?`)) return;
        try {
            const res = await fetch(`/api/places/${encodeURIComponent(placeKey)}`, {
                method: 'DELETE',
                headers: {
                    'X-CSRFToken': csrfToken(),
                    'Accept': 'application/json',
                },
            });
            if (!res.ok) {
                const err = await res.json();
                throw new Error(err.error?.message || `HTTP ${res.status}`);
            }
            setStatus(`Place ${placeKey} deleted successfully.`, false);
            loadPlaces();
        } catch (err) {
            setStatus(`Delete failed: ${err.message}`, true);
        }
    }

    function openAddModal() {
        document.getElementById('placeModalTitle').textContent = 'Add New Geographic Location';
        document.getElementById('placeModalKey').value = '';
        document.getElementById('placeModalKey').disabled = false;
        document.getElementById('placeModalLabel').value = '';
        document.getElementById('placeModalType').value = 'city';
        document.getElementById('placeModalCountries').value = '';
        document.getElementById('placeModalLat').value = '';
        document.getElementById('placeModalLon').value = '';
        document.getElementById('placeModalRetired').checked = false;
        document.getElementById('placeModalIsEdit').value = '0';

        const namesContainer = document.getElementById('placeModalNamesList');
        clearNode(namesContainer);
        addAliasRow('', 'en', 'endonym');

        const modalEl = document.getElementById('placeModal');
        if (window.bootstrap && bootstrap.Modal) {
            const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
            modal.show();
        }
    }

    async function openEditModal(place) {
        document.getElementById('placeModalTitle').textContent = `Edit Location: ${place.label}`;
        document.getElementById('placeModalKey').value = place.place_key;
        document.getElementById('placeModalKey').disabled = true;
        document.getElementById('placeModalLabel').value = place.label;
        document.getElementById('placeModalType').value = place.feature_type || 'city';
        document.getElementById('placeModalCountries').value = (place.country_codes || []).join(', ');
        document.getElementById('placeModalLat').value = place.latitude !== null && place.latitude !== undefined ? place.latitude : '';
        document.getElementById('placeModalLon').value = place.longitude !== null && place.longitude !== undefined ? place.longitude : '';
        document.getElementById('placeModalRetired').checked = Boolean(place.retired);
        document.getElementById('placeModalIsEdit').value = '1';

        const namesContainer = document.getElementById('placeModalNamesList');
        clearNode(namesContainer);
        const loadDiv = document.createElement('div');
        loadDiv.className = 'text-muted small py-2';
        loadDiv.textContent = 'Loading aliases…';
        namesContainer.appendChild(loadDiv);

        const modalEl = document.getElementById('placeModal');
        if (window.bootstrap && bootstrap.Modal) {
            bootstrap.Modal.getOrCreateInstance(modalEl).show();
        }

        try {
            const res = await fetch(`/api/places/${encodeURIComponent(place.place_key)}`, {
                headers: { 'Accept': 'application/json' }
            });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            clearNode(namesContainer);
            const names = data.place?.names || [];
            if (names.length === 0) {
                addAliasRow(place.label, 'en', 'endonym');
            } else {
                for (const n of names) {
                    addAliasRow(n.name, n.language, n.name_type);
                }
            }
        } catch (err) {
            clearNode(namesContainer);
            addAliasRow(place.label, 'en', 'endonym');
        }
    }

    function addAliasRow(name, lang, ntype) {
        const container = document.getElementById('placeModalNamesList');
        if (!container) return;

        const row = document.createElement('div');
        row.className = 'row g-1 mb-2 align-items-center alias-row';

        const colName = document.createElement('div');
        colName.className = 'col-5';
        const inputName = document.createElement('input');
        inputName.type = 'text';
        inputName.className = 'form-control form-control-sm alias-name';
        inputName.placeholder = 'Name or alias';
        inputName.value = name || '';
        inputName.required = true;
        colName.appendChild(inputName);

        const colLang = document.createElement('div');
        colLang.className = 'col-3';
        const selLang = document.createElement('select');
        selLang.className = 'form-select form-select-sm alias-lang';
        const langOpts = [
            ['en', 'English (en)'], ['ar', 'Arabic (ar)'], ['he', 'Hebrew (he)'],
            ['fa', 'Persian (fa)'], ['hr', 'Croatian (hr)']
        ];
        for (const [code, label] of langOpts) {
            const opt = document.createElement('option');
            opt.value = code;
            opt.textContent = label;
            if (code === lang) opt.selected = true;
            selLang.appendChild(opt);
        }
        colLang.appendChild(selLang);

        const colType = document.createElement('div');
        colType.className = 'col-3';
        const selType = document.createElement('select');
        selType.className = 'form-select form-select-sm alias-type';
        const typeOpts = [['endonym', 'Endonym'], ['exonym', 'Exonym'], ['variant', 'Variant']];
        for (const [tval, tlbl] of typeOpts) {
            const opt = document.createElement('option');
            opt.value = tval;
            opt.textContent = tlbl;
            if (tval === ntype) opt.selected = true;
            selType.appendChild(opt);
        }
        colType.appendChild(selType);

        const colBtn = document.createElement('div');
        colBtn.className = 'col-1 text-end';
        const rmBtn = document.createElement('button');
        rmBtn.type = 'button';
        rmBtn.className = 'btn btn-sm btn-outline-danger py-0 px-2 btn-remove-alias';
        rmBtn.append(createIcon('bi-x'));
        rmBtn.addEventListener('click', () => {
            if (container.querySelectorAll('.alias-row').length > 1) {
                row.remove();
            } else {
                inputName.value = '';
            }
        });
        colBtn.appendChild(rmBtn);

        row.append(colName, colLang, colType, colBtn);
        container.appendChild(row);
    }

    async function handleModalSubmit(e) {
        e.preventDefault();
        const isEdit = document.getElementById('placeModalIsEdit').value === '1';
        const key = (document.getElementById('placeModalKey').value || '').trim();
        const label = (document.getElementById('placeModalLabel').value || '').trim();
        const ftype = document.getElementById('placeModalType').value;
        const rawCountries = document.getElementById('placeModalCountries').value || '';
        const latRaw = document.getElementById('placeModalLat').value;
        const lonRaw = document.getElementById('placeModalLon').value;
        const retired = document.getElementById('placeModalRetired').checked;

        const countries = rawCountries.split(',')
            .map(c => c.trim().toUpperCase())
            .filter(c => c.length === 2 && /^[A-Z]{2}$/.test(c));

        const names = [];
        const aliasRows = document.querySelectorAll('.alias-row');
        for (const row of aliasRows) {
            const nVal = (row.querySelector('.alias-name')?.value || '').trim();
            const lang = row.querySelector('.alias-lang')?.value || 'en';
            const ntype = row.querySelector('.alias-type')?.value || 'endonym';
            if (nVal) {
                names.push({ name: nVal, language: lang, name_type: ntype });
            }
        }
        if (names.length === 0) {
            names.push({ name: label, language: 'en', name_type: 'endonym' });
        }

        const payload = {
            label: label,
            feature_type: ftype,
            country_codes: countries,
            retired: retired,
            names: names,
        };
        if (latRaw !== '') payload.latitude = parseFloat(latRaw);
        if (lonRaw !== '') payload.longitude = parseFloat(lonRaw);
        if (!isEdit && key) payload.place_key = key;

        const url = isEdit ? `/api/places/${encodeURIComponent(key)}` : '/api/places';
        const method = isEdit ? 'PUT' : 'POST';

        const errDiv = document.getElementById('placeModalError');
        errDiv.textContent = '';
        errDiv.classList.add('d-none');

        try {
            const res = await fetch(url, {
                method: method,
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken(),
                    'Accept': 'application/json',
                },
                body: JSON.stringify(payload),
            });
            const data = await res.json();
            if (!res.ok) {
                throw new Error(data.error?.message || `HTTP ${res.status}`);
            }

            const modalEl = document.getElementById('placeModal');
            if (window.bootstrap && bootstrap.Modal) {
                bootstrap.Modal.getInstance(modalEl)?.hide();
            }
            setStatus(isEdit ? `Location ${key} updated successfully.` : `Location created successfully.`, false);
            loadPlaces();
        } catch (err) {
            errDiv.textContent = err.message;
            errDiv.classList.remove('d-none');
        }
    }

    function init() {
        const placesTab = document.getElementById('places-tab');
        if (placesTab) {
            placesTab.addEventListener('shown.bs.tab', () => loadPlaces(1));
        }

        const addBtn = document.getElementById('placesAddBtn');
        if (addBtn) addBtn.addEventListener('click', openAddModal);

        const addAliasBtn = document.getElementById('btnAddAliasRow');
        if (addAliasBtn) addAliasBtn.addEventListener('click', () => addAliasRow('', 'en', 'variant'));

        const form = document.getElementById('placeForm');
        if (form) form.addEventListener('submit', handleModalSubmit);

        const searchBtn = document.getElementById('placesSearchBtn');
        if (searchBtn) searchBtn.addEventListener('click', () => loadPlaces(1));

        const searchInput = document.getElementById('placesSearchInput');
        if (searchInput) searchInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') { e.preventDefault(); loadPlaces(1); }
        });

        for (const fId of ['placesFilterType', 'placesFilterStatus']) {
            const sel = document.getElementById(fId);
            if (sel) sel.addEventListener('change', () => loadPlaces(1));
        }

        const prevBtn = document.getElementById('placesPrevBtn');
        if (prevBtn) prevBtn.addEventListener('click', () => { if (currentPage > 1) loadPlaces(currentPage - 1); });

        const nextBtn = document.getElementById('placesNextBtn');
        if (nextBtn) nextBtn.addEventListener('click', () => { if (currentPage < totalPages) loadPlaces(currentPage + 1); });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
