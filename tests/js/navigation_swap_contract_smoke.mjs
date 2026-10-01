/** Compatibility contract smoke for navigation arriving via content swaps. */
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const root = new URL('../../', import.meta.url);
const navigatorPath = new URL('static/js/modules/navigation/navigator.js', root);
let navigatorSource = await readFile(navigatorPath, 'utf8');
navigatorSource = navigatorSource
    .replace(/^import .*;\s*$/gm, '')
    .replace(/^export function /gm, 'function ');

const calls = [];
const window = {};
const context = vm.createContext({
    window,
    console,
    navigationState: { sectionPagination: { currentPage: 5 }, history: [], currentIndex: -1 },
    sectionCursorState: {},
    translations: { home: 'Home', items: 'Items' },
    sectionLabels: { category: 'Categories' },
    addToHistory: (state) => calls.push(['history', state]),
    updateNavButtons: () => calls.push(['nav-buttons']),
    updateBreadcrumb: (items) => calls.push(['breadcrumb', items]),
    loadRootView: () => calls.push(['root']),
    loadSectionView: (...args) => calls.push(['section', ...args]),
    loadItemView: () => calls.push(['item']),
    loadItemFilesWithFilters: () => calls.push(['filtered-files']),
    updateSidebarActiveState: (section) => calls.push(['active', section]),
    initEventDelegation: () => {},
});
vm.runInContext(navigatorSource, context, { filename: 'navigator.js' });

assert.equal(typeof window.navigateToSection, 'function');
assert.equal(typeof window.fms?.navigation?.navigateToSection, 'function');
window.fms.navigation.navigateToSection('category');
assert.ok(calls.some((call) => call[0] === 'section' && call[1] === 'category' && call[2] === 1));

const swap = await readFile(new URL('static/js/modules/core/navigation-swap.js', root), 'utf8');
assert.match(swap, /syncRouteJsonData\(parsed, nextMain\)/);
assert.match(swap, /data-navigation-shell/);
assert.match(swap, /syltharae:before-page-swap/);
assert.match(swap, /application\/json/);

const base = await readFile(new URL('templates/base.html', root), 'utf8');
for (const asset of [
    'js/jquery.min.js', 'js/bootstrap.bundle.min.js', 'js/chart.umd.js',
    'js/jspdf.umd.min.js', 'js/modules/charts/chart-customizer.js',
    'js/modules/ui/file-actions.js', 'js/modules/ui/documents-panel.js',
    'js/modules/core/language-init.js', 'js/user-menu.js',
]) {
    const line = base.split('\n').find((candidate) => candidate.includes(asset));
    assert.ok(line?.includes('data-navigation-shell'), `shell marker missing: ${asset}`);
}
console.log('Navigation swap contract smoke: 16 checks passed');
