# Component Library

The shared UI components live in `templates/components/`. Each file starts
with a `{# component: ... #}` declaration that names the component, the
states it implements and the CSS classes it owns. The generated section below
is read out of those declarations and out of a scan of every template, so it
cannot drift from the code:

    python3 -m core.frontend.component_audit docs/COMPONENT_LIBRARY.md

`tests/unit/test_component_library.py` fails when it is out of date.

## Rules

1. **Presentation only.** A component renders what it is given. It never
   queries the database, never decides whether the current user may do
   something, and never hard-codes an interface id. Access decisions belong
   to the route and the registry.
2. **Accessible by default.** Errors render with `role="alert"`, status and
   toast messages with `role="status"` and `aria-live="polite"`, and
   pagination with an `aria-label` and chevron icons.
3. **No inline behaviour.** Components contain no `<script>` and no `onclick`;
   behaviour is attached by the page modules in `static/js/`.
4. **Icons come from the bundle.** Every `bi-*` class a template names must
   exist in `static/icons/bootstrap-icons.css`, because that is what ships to
   an offline installation.
5. **This is not a redesign.** The library standardises markup the product
   already had; adopting a component must not change what a page looks like
   or does.

## States that are not built yet

A component declares only the states it really implements. A state the
design calls for but that no component implements yet is listed in
`PLANNED_STATES` (`core/frontend/component_audit.py`) and shown as *not yet*
in the generated table, rather than implied to be covered. The list is empty
today.

## Adoption

The generated section also counts the templates that still hand-roll markup
a component now provides (empty states, alerts, pagination). The trend of
those counts is the measure of adoption; they are not typed here.

## Generated reference

<!-- BEGIN GENERATED COMPONENT AUDIT -->

### Components

Read from the `{# component: … #}` declaration at the top of each file in `templates/components/`. A file without one has no declared purpose or states, and is listed as such.

| Component | File | States | Macros | Purpose |
| --- | --- | --- | --- | --- |
| `action_toolbar` | `templates/components/action_toolbar.html` | `selected`, `loading` | `action_toolbar`, `action_group`, `action_button`, `bulk_action_button`, `selection_summary` | The bar of actions on a list: the groups, the buttons, and the one state the server can know for certain - nothing is selected yet. |
| `analyst_classify` | `templates/components/analyst_classify.html` | `normal`, `editing`, `saving`, `success`, `error`, `unauthorized` | `analyst_classify` | The analyst-category control for the record being read, usable from wherever that record is displayed. |
| `breadcrumbs` | `templates/components/breadcrumbs.html` | `normal` | `breadcrumbs` | Where the reader is, rendered from the interface registry: a page says what it is about, the registry supplies the words and icons. |
| `confirm_dialog` | `templates/components/confirm_dialog.html` | `normal`, `saving`, `error`, `unauthorized` | `confirm_dialog` | One way to ask "are you sure?" before something irreversible. The component renders the question; it never performs the operation and never knows what the operation is. The page decides - and it says which action it is asking about, by id, so the dialog and the action registry agree on what is happening. |
| `file_nav` | `templates/components/file_nav.html` | `normal`, `empty` | `file_nav` | Previous/next through the records the reader is working through, and where this one sits in that set. |
| `filter_bar` | `templates/components/filter_bar.html` | `normal`, `filtered`, `empty` | `filter_section`, `filter_grid`, `filter_bar`, `filter_group`, `search_group`, `hidden_filter` | The controls above a list that decide which records it shows: a search box and the filters that narrow it, in the arrangement fifteen pages currently rebuild by hand. |
| `operations_widget` | `templates/components/operations_widget.html` | `normal`, `loading`, `empty`, `error` | — | What the ingestion and processing system is doing right now: active jobs, throughput, and the shortcuts into Operations. |
| `page_data` | `templates/components/analyst_classify_page_data.html` | `normal` | `analyst_classify_page_data` | The translated strings and the write permission the analyst classification module needs, so nothing is hard-coded in JavaScript and a language switch reaches it. |
| `page_tips` | `templates/components/page_tips.html` | `normal`, `empty` | `page_tips` | The explanatory tips at the top of a page: what this page is for, what the elements on it do, how to add data here. |
| `pagination` | `templates/components/unified_pagination.html` | `normal`, `filtered` | `unified_pagination` | Moving through a long list: where you are, how much there is, and how to get to a page you know the number of. |
| `pagination_cursor` | `templates/components/cursor_pagination.html` | `normal`, `filtered` | `cursor_pagination` | Moving through a list that has no page numbers - only "next" and "back" - and saying honestly what is known about how much is left. |
| `record_actions` | `templates/components/record_actions.html` | `normal`, `loading`, `success`, `error`, `unauthorized` | `record_action_button`, `record_action_surface` | The actions a single record offers. The buttons come from the Action Definition layer - id, label, icon, scope, destructiveness, confirmation key - joined with the screen's own bindings by core/experience/presentation.py, so a page cannot invent an action and two pages cannot render one action two ways. |
| `record_header` | `templates/components/record_header.html` | `normal`, `empty`, `loading`, `unavailable`, `archived` | `record_header` | The top of a record: what this record is, what state it is in, and the one action that matters most. The same header for a file, a source, a side, a category, an analysis or a report - it knows nothing about what kind of record it is describing. |
| `screen_inspector` | `templates/components/screen_inspector.html` | `normal`, `loading`, `empty`, `error`, `unavailable` | `inspector_field`, `screen_inspector` | The panel that answers what an element on this screen is: which interface owns it, which component renders it, which action it presents, what scope and permission metadata that action declares, where it is bound, and what it currently is. A diagnostic tool for a developer or an operator, not a configuration editor. |
| `search_input` | `templates/components/search_input.html` | `normal`, `loading`, `filtered`, `unavailable` | `search_input` | The one search box: label, icon, placeholder, value, clear action, a place for the loading state, and the ARIA that makes it a search box rather than an empty text field. |
| `sidebar_nav` | `templates/components/sidebar_nav.html` | `normal`, `empty` | — | The product navigation, grouped by domain, rendered from the navigation model the application prepares. |
| `states` | `templates/components/states.html` | `loading`, `empty`, `filtered`, `success`, `warning`, `error`, `unauthorized`, `unavailable`, `archived` | `state_panel`, `empty_state`, `filtered_state`, `loading_state`, `success_state`, `warning_state`, `error_state`, `unauthorized_state`, `unavailable_state`, `archived_state` | The states a region can be in, in one place, so a page never invents its own wording for "nothing here yet" or its own markup for "this failed". |
| `status_badge` | `templates/components/status_badge.html` | `success`, `warning`, `error`, `unavailable`, `archived` | `status_badge`, `_chip`, `status_badge_with_icon` | One way to show a status word, so the same state is not green on one page and grey on the next. An application status is looked up in the vocabulary - `core/frontend/status_vocabulary.py` - which maps it to one of a few presentation states; the component only turns that state into classes. |
| `table` | `templates/components/table.html` | `normal`, `empty`, `filtered`, `selected`, `loading`, `error` | `data_table`, `table_empty_row`, `table_loading_row`, `table_error_row`, `select_all_checkbox`, `sort_header` | The frame a list of records is read in, and the rows that stand in for a list that is empty, still loading or failed. Ten tables in this application were written with ten different class combinations; this is the one they become. |
| `toast` | `templates/components/toast.html` | `success`, `warning`, `error`, `loading`, `unavailable` | `toast_region` | One place where the application tells the reader that something happened. Every action ends visibly - success, information, warning, failure - and every message passes through here, so no page invents its own notification and no failure goes silent. |

### States (§63)

Every state in the vocabulary is answered by at least one component; a state nobody implements does not exist in the product, however often it is referred to.

| State | Meaning | Implemented by |
| --- | --- | --- |
| `loading` | Work is in progress; the reader is told what is being waited for. | `action_toolbar`, `operations_widget`, `record_actions`, `record_header`, `screen_inspector`, `search_input`, `states`, `table`, `toast` |
| `empty` | Nothing exists here yet, and the reader is told how to start. | `file_nav`, `filter_bar`, `operations_widget`, `page_tips`, `record_header`, `screen_inspector`, `sidebar_nav`, `states`, `table` |
| `normal` | The ordinary case: content is present and usable. | `analyst_classify`, `page_data`, `breadcrumbs`, `confirm_dialog`, `pagination_cursor`, `file_nav`, `filter_bar`, `operations_widget`, `page_tips`, `record_actions`, `record_header`, `screen_inspector`, `search_input`, `sidebar_nav`, `table`, `pagination` |
| `filtered` | Something exists, but not under the filters applied. | `pagination_cursor`, `filter_bar`, `search_input`, `states`, `table`, `pagination` |
| `selected` | A row or record is chosen; actions that need a choice appear. | `action_toolbar`, `table` |
| `editing` | A value is being changed, and the change is not saved yet. | `analyst_classify` |
| `saving` | A change is on its way to the server. | `analyst_classify`, `confirm_dialog` |
| `success` | The last action worked. | `analyst_classify`, `record_actions`, `states`, `status_badge`, `toast` |
| `warning` | Something needs attention but is not broken. | `states`, `status_badge`, `toast` |
| `error` | Something failed, with a next step rather than a stack trace. | `analyst_classify`, `confirm_dialog`, `operations_widget`, `record_actions`, `screen_inspector`, `states`, `status_badge`, `table`, `toast` |
| `unauthorized` | The server refused this for this account. | `analyst_classify`, `confirm_dialog`, `record_actions`, `states` |
| `unavailable` | It needs something this installation does not have. | `record_header`, `screen_inspector`, `search_input`, `states`, `status_badge`, `toast` |
| `archived` | Kept and readable, but out of the working set. | `record_header`, `states`, `status_badge` |

### Markup pages still write by hand

Counted by scanning `templates/**`. These are the places a shared component has not reached yet - the number goes down as components are adopted, and it is the measure of this phase rather than an impression of it.

| Markup | Component that replaces it | Templates |
| --- | --- | --- |
| Hand-written empty state | `states` | 4 templates |
| Hand-written loading indicator | `states` | 9 templates |
| Hand-written inline error | `states` | 6 templates |
| Hand-written table | `table` | 15 templates |
| Hand-written pagination markup | `pagination` | 0 templates |
| Pagination mount (filled by the shared renderer) | `pagination` | 4 templates |
| Hand-written search input | `search_input` | 7 templates |
| Hand-written filter control | `filter_bar` | 14 templates |
| Browser confirm() dialog | `confirm_dialog` | 0 templates |
| Hand-written status badge | `status_badge` | 0 badges, in 0 templates |
| Hand-written badge chip (count, id, method) | — | 68 badges |
| Hand-written action bar | `action_toolbar` | 2 templates |

### Adoption

How much of the repeated markup has moved onto its component. Standardized counts the templates that read through the component; hand-written counts what is still done by hand; the rate is the completion criterion for this phase, not an impression of it.

| Markup | Standardized | Hand-written | Adoption |
| --- | --- | --- | --- |
| Hand-written empty state | 4 | 4 | 50% |
| Hand-written table | 3 | 15 | 17% |
| Hand-written pagination markup | 10 | 0 | 100% |
| Hand-written search input | 8 | 7 | 53% |
| Hand-written filter control | 1 | 14 | 7% |
| Browser confirm() dialog | 2 | 0 | 100% |
| Hand-written status badge | 7 | 0 | 100% |
| Hand-written action bar | 5 | 2 | 71% |

**Declared exceptions.** Not everything that looks similar is the same thing, and overloaded components stop being usable. An exception is a decision with an owner:

| Area | Reason | Owner |
| --- | --- | --- |
| analysis relationship matrix | A matrix of relationships between records is not a list of records: its rows and columns are both entities, and its cells are computed pairs. Forcing it into the table component would give the component a second meaning. | analysis workspace |

### CSS ownership of the classes components render

Every class a component renders has exactly one owner. **OWNED** means an SYLTHARAE stylesheet defines it, or the component declares it in its own `classes:` header. **THIRD_PARTY** means a bundled dependency defines it - Bootstrap and Bootstrap Icons are expected dependencies, and using them is not a finding. **UNKNOWN** means nobody does, and an unknown class is how a component invents a style: it fails the guardrail test rather than being reported and forgotten.

| Ownership | Classes |
| --- | --- |
| OWNED (SYLTHARAE) | 81 |
| THIRD_PARTY (Bootstrap, Bootstrap Icons) | 197 |
| UNKNOWN | 0 |

Third-party stylesheets bundled with the application: `static/css/bootstrap.min.css`, `static/icons/bootstrap-icons.css`.

Owned by declaration rather than by a stylesheet - the component states these are its own hooks, and no rule styles them (which is a decision, not an accident):

* `cursor-pagination-container` (cursor_pagination.html)
* `file-nav__text` (file_nav.html)
* `search-input-spinner` (search_input.html)
* `sidebar-nav-badge` (sidebar_nav.html)

<!-- END GENERATED COMPONENT AUDIT -->
