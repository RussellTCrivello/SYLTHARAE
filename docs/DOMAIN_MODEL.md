# Domain model

Two models organise SYLTHARAE, and they are deliberately different:

* the **information model** - the data substrate: what is stored and how it
  relates (this page, and [DATABASE.md](DATABASE.md) for the tables);
* the **work domains** - the operator's grouping of screens: what each part
  of the product is *for* (`core/interfaces/domains.py`, used by navigation,
  help and permissions).

## 1. Information model

```
Source ─┐
        ├─► Context (Hash + Source + Side) ─► Occurrence (Path) ─► child Paths
Side ───┘            │
                     ▼
                  Content (Hash) ─► Extraction ─► Words, Keywords, Title, Raw text
                                                        │
                                                        ▼
                                            Analysis: categories, similarity,
                                            relationships, alerts
                                                        │
                                                        ▼
                                            Analyst layer: analyst categories,
                                            assignments, decision log
```

| Concept | Meaning | Stored in |
| --- | --- | --- |
| **Source** | Who or where material came from: a person, organisation, device or collection, with job, importance, country, city, accounts, ownership and access status. | `sources` |
| **Side** | A party the material belongs to (the registry's words: "the parties the material belongs to"), with an importance weight. | `sides` |
| **Content (Hash)** | A unique sequence of bytes, identified by its SHA-256. Text, words and keywords are extracted from it once. | `hashs` |
| **Context** | A content as it belongs to one source and side. The same bytes from two sources are one content with two contexts. | `hash_contexts` |
| **Occurrence (Path)** | One physical encounter with a content: a file, an archive member, an e-mail attachment. Carries the name, location, size, dates, coordinates, extraction provenance, processing status and, for extracted members, the parent occurrence and hierarchy. | `paths` |
| **Extraction** | What the readers recovered from a content: indexed words (`contents`, `words_hashs`), faithful structured text (`contents_raw`), titles. | `contents`, `contents_raw`, `titles_content` |
| **Word / Keyword** | Normalised terms with per-content counts and positions; keywords are the curated terms of the taxonomy. | `words`, `words_hashs`, `keywords`, `keywords_hashs` |
| **System category** | The automatic ("smart") taxonomy: categories of words and keywords, used for classification and analytics. | `categorys`, `words_categorys` |
| **Analyst category** | A human decision, separate from the system taxonomy: an analyst files occurrences under a category, and every decision is logged with the query that produced it. | `analyst_categories`, `analyst_file_categories`, `analyst_categorization_log` |
| **Alert** | A notification about an occurrence (a match, a processing problem, a scan result). | `alerts` |
| **Job** | A unit of long-running work (ingestion, import) with state, progress and events. | `jobs`, `job_events` |

Invariants the system maintains:

1. A content is stored and extracted **once**; duplicates add contexts or
   occurrences, never copies of the text.
2. Every occurrence belongs to exactly one context; every context to one
   content, one source and one side.
3. An archive member points at its container (`parent_path_id`), so any file
   can be traced back to the item that was originally ingested.
4. System classification and analyst decisions never overwrite each other.
5. Provenance is explicit: `extraction_provenance` records which extractor
   (including OCR) produced the text, so recognised text is never presented
   as if it were native text.

## 2. Work domains

Every screen belongs to exactly one domain; the set is closed and validated.

| Domain | Purpose |
| --- | --- |
| Work | The working overview - where a session starts and what needs attention. |
| Discover | Finding and reading what is stored: files, sources, sides, words, categories, notifications. |
| Ingest | Bringing material in: input, imports and the validation that precedes them. |
| Processing | Reading, extracting and storing: what happens to material once it is accepted. |
| Analyze | Working the material: archives, paths, batches, classification and relationships. |
| Classify | Deciding what material is: analyst categories and classification outcomes. |
| Report | Summarising and presenting findings. |
| Operate | Running the system: jobs, recovery and operational tooling. |
| Administration | People, roles, audit and system health. |
| Settings | Configuration of the product itself. |
| Security | Authentication, authorization and the controls around them. |
| Cross-cutting and internal | Surfaces the product needs but does not advertise. |

Which screen belongs to which domain is generated in
[INTERFACE_REGISTRY.md](INTERFACE_REGISTRY.md).

## 3. Typical flow

1. An administrator or analyst creates the **sources** and **sides** of a case.
2. An analyst ingests a folder or upload on the **Input** page, choosing the
   source and side; a **job** runs the pipeline.
3. Each file becomes an **occurrence**; its bytes become (or match) a
   **content**; archives expand into child occurrences.
4. Text is extracted and indexed; the system taxonomy classifies words and
   keywords; alerts are raised.
5. Analysts search, read and compare, file occurrences under **analyst
   categories**, and export reports.
