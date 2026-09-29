# Assessment: proposed "Narrative Intelligence Layer" (owner proposal, 2026-09-28)

**Proposal:** the owner supplied a Narrative Intelligence Layer made of six
tables:

* `narrative_strategies`
* `narrative_components`
* `narrative_rules`
* `evidence_relationships`
* `narrative_cache`
* `report_blueprints`

It also includes two PL/pgSQL functions: `analyze_evidence_relationships()`
and `generate_narrative_report()`.

**Method:** the SQL was checked against the migrated schema (0001-0026 on
PostgreSQL 16, provisioned with `tools/verify/runtime_provision.py`). It was
also checked against the governing directive (§22, §23, §24, §27 and §31) and
against the step-16 narrative engine (`core/analytics/narrative.py`,
[ANALYTICS.md](ANALYTICS.md)).

**Verdict:** the *ideas* are valuable and are adopted below. The SQL cannot be
adopted as written: it does not run against this schema, and several of its
sentences state conclusions the evidence does not support, which the
directive prohibits.

## What it gets right, and what is adopted

| Idea | Why it is worth having | How it is adopted |
| --- | --- | --- |
| Administrators manage phrasing without code | the owner's standing requirement: everything manageable from the interface | **phrasing variants** managed in the UI (see "Adopted design") |
| Several phrasings per statement | reports read less mechanically | variants of the *same* reviewed statement, with an identical placeholder set, chosen deterministically |
| Components chosen by conditions (file/source counts, recency) | a narrative should say what the evidence supports, and nothing more | conditions stay in code next to the measure (sourced thresholds). Administrators choose wording, not what is claimed |
| Sections (opening / main / supporting / conclusion) | structure for a multi-analysis report | the report layout of steps 17/23 (Comprehensive report); each analysis keeps its five voices |
| Cross-source, chronology and geography facets | they answer "where from, when, where" | analysis kinds over declared datasets in step 17 (Keyword Intelligence, Entity/Place, Horizon), with neutral wording |
| Structured facts under the prose (`semantic_facts`) | traceability | already done: runs store template references and parameters, never prose |
| Evidence citations | "every statement links back to source data" | the per-run datasets are the citations; the narrative names the dataset it came from |

## Why the SQL is not adopted as written

### It does not run on this schema (verified)

| Location | Problem | Evidence |
| --- | --- | --- |
| `generate_narrative_report`, keyword branch | `JOIN words w ON k.id = p_subject_id` joins **every** word to the keyword (a cross join). The keyword's text is `keywords.keyword` (BYTEA), not a row of `words` | `information_schema`: `keywords(id, keyword bytea, category_id)` |
| Usage example 2 | `JOIN words w ON k.id = w.id` compares unrelated identifiers | same |
| Evidence fingerprint | `digest(...)` requires the `pgcrypto` extension, which is **not available** in the bundled PostgreSQL. The built-in `sha256()` (PG ≥ 11, already used by m0023) needs no extension | `pg_available_extensions` has no pgcrypto; only `plpgsql` is installed |
| `analyze_evidence_relationships`, co-occurrence | `JOIN keywords_hashs kh2 ON ... AND k1.id < k2.id` references `k2` before it is joined, which is a PostgreSQL error | SQL semantics |
| Related locations | `STRING_AGG(DISTINCT gp.label, ', ' ORDER BY COUNT(*) DESC)` nests an aggregate inside an aggregate, and with DISTINCT the ORDER BY must be the argument, so it is rejected | SQL semantics |
| Part 4 | `\gset` and `:strategy_id` are psql client commands. The migration runner (psycopg2) cannot execute them | `database/migration_runner.py` |
| Keyword `evidence_count` | `COUNT(DISTINCT kh.keyword_id)` for a single keyword is always 1 | query shape |

### It states conclusions the evidence does not support (§22, §31 "fabricate analytical conclusions")

* **"Corroborated across N independent sources."** Independence is set to
  `v_source_count >= 2`. Two sources can share an origin: forwarded mail and
  duplicate files are ingested under different sources. The data cannot
  establish independence, so the sentence would assert something unmeasured.
* **"Significant intelligence value", "priority analytical attention",
  "genuine operational significance".** These are triggered by `3 sources and
  10 files`, thresholds with no stated source (§22: "any threshold must have
  an identified source or empirical basis"). Our Consequence voice is
  deliberately generic for this reason.
* **"Recent heightened activity".** This is computed from
  `paths.date_creation`, which is the *ingestion* date. It measures when files
  were collected, not when anything happened. Event dates are the durable
  temporal signals (`content_signals.date_from`).
* **`relationship_strength = count / 10` (or /20, /15).** An arbitrary scale
  presented as strength. Where association strength is needed, logDice
  (Rychlý 2008) is implemented and oracle-tested.
* **Random or unstated choice among `template_variations`.** This is
  non-deterministic and violates §23 ("reproducible from inputs and template
  version").

### It breaks invariants the system already guarantees

* **Authorization (§14).** The functions read every source, but role scope is
  compiled into SQL *before* retrieval. A PL/pgSQL function outside the
  criteria compiler bypasses `AccessScope`.
* **Snapshot and reproducibility (§19).** `narrative_cache` is updated during
  generation (`UPDATE ... use_count`). Report runs execute in a `REPEATABLE
  READ, READ ONLY` snapshot, where this write fails. The cache key covers only
  the counts, so a changed source list returns stale text. Stored runs already
  reproduce their narrative exactly, which makes a cache unnecessary.
* **`evidence_relationships` as a global, periodically refreshed table.** It
  goes stale between refreshes, has no snapshot or provenance, and duplicates
  the Relationship report of step 17. Relationship identity there is
  `(hash_id, source_id, side_id)` with distinct contexts counted (§26), not
  raw keyword pairs.
* **Performance (§27).** The keyword co-occurrence self-join grows with the
  square of the keywords per content, across the whole corpus, in one
  statement with no bound.
* **A second narrative architecture (§31).** A parallel engine in PL/pgSQL
  next to `core/analytics/narrative.py` would duplicate business logic.

### Multilingual phrasing: its weakest point

The proposal builds sentences by concatenation:

```
'Several files address ' || v_subject_name || ', with ' || v_file_count || ' documents...'
```

and stores the result as `narrative_text`. Such text cannot be translated
correctly:

* **Word order.** Arabic, Hebrew and Persian place the parts of a sentence
  differently. A translator needs the *whole sentence* with named
  placeholders, not fragments.
* **Plural forms.** English has 2, Croatian 3 (1 / 2-4 / 5+), and Arabic 6
  (zero, one, two, few, many, other). "`v_file_count` documents" is wrong in
  both.
* **Grammatical gender and agreement** (Hebrew, Arabic, Croatian): the verb and
  adjectives depend on the noun.
* **Lists.** "X, Y and Z" joins differently per language; Arabic uses «و» and
  the Arabic comma «،».
* **Numerals and dates.** `TO_CHAR(..., 'DD Mon YYYY')` always produces
  English month names and Latin digits.
* **Bidirectional text.** A Latin source name inside an Arabic sentence must be
  isolated, or punctuation moves.
* **Storing prose fixes the language at write time.** A reader in another
  language cannot re-read it.

The step-16 engine already avoids most of this: it stores references, renders
whole-sentence msgids with named placeholders in the reader's language, and
formats numbers per locale. **It has a real gap of its own** (see below).

## Defect found in our own engine during this review

**NARR-01.** `core/analytics/narrative.py` / `kinds.py` do not use plural
forms. For example, `"%(significant)s terms are used significantly more
often..."` reads "1 terms" in English and is wrong for Arabic and Croatian.
The catalogs declare the correct `Plural-Forms` (ar `nplurals=6`, hr 3, en/he/fa
2), but the templates never use `ngettext`. Fixing it changes the meaning pins
of `term_keyness@1` templates, so it requires a **new template-set version**
(released versions are not edited). Scheduled as the first item of the
narrative extension below, and recorded in the ledger.

## Adopted design (extension of the existing engine; no second architecture)

1. **Claims stay in reviewed code.** Measures, eligibility conditions and
   thresholds (each with its source) choose *which* statement applies.
   Administrators cannot make the system claim more than the measure shows.
2. **Phrasing is manageable in the interface.** A new table (next free
   migration number) holds *phrasing variants* for an existing statement key
   (`voice.variant` of a template set). Each variant has:
   - one text per language (en/ar/he/fa/hr), with plural forms where the
     statement has a count;
   - **the same placeholder set** as the reviewed statement (enforced on save);
   - a status *draft → approved → retired*. Approving requires a second
     administrator, the creator cannot approve their own variant, and both
     actions are audited;
   - an immutable version once approved. An edit creates a new version, and
     earlier runs keep theirs.

   A missing translation falls back to the reviewed catalog text for that
   language and is **labelled** as a fallback. It is never left blank or
   silently substituted.
3. **Deterministic choice.** A run uses the administrator-selected default
   variant, or the reviewed text when none is approved. It records the variant
   id, version and SHA-256 in the stored narrative reference, so any artifact
   renders exactly the same words later.
4. **Preview before approval.** Any variant can be rendered against a stored
   run, in every language, side by side with the reviewed text.
5. **New facets as analysis kinds in step 17** (cross-source presence,
   chronology over signal event dates, place association via logDice over
   stored place signals). They run inside the run's snapshot and scope, with
   neutral wording: "appears in 3 sources", never "independently
   corroborated".
6. **Not adopted:**
   - `narrative_cache`: runs are stored, so it is unnecessary.
   - The global `evidence_relationships` table: it goes stale; the step-17
     Relationship report replaces it.
   - `report_blueprints` as free JSON: report definitions are versioned code
     with lock-pinned fingerprints. Administrators manage *which* reports run,
     their parameters, schedules (step 20) and phrasing, not the SQL.

## Where it sits in the plan

* **NARR-01** (plural forms, template set v2): part of step 17, before new
  kinds add more counted sentences.
* **Phrasing variants plus their management page:** manageability item **M6**,
  after step 17 (it needs the statements of the catalog reports to exist).
* **Facets:** within the step-17 catalog reports.
