# Licensing

SYLTHARAE is free software, licensed under the **GNU Affero General Public
License, version 3 or (at your option) any later version**
(SPDX: `AGPL-3.0-or-later`). The full text is in [LICENSE](../LICENSE).

The licence was chosen because the PDF reader depends on PyMuPDF, which is
licensed under the AGPL-3.0. A work that combines PyMuPDF must be
AGPL-compatible or hold an Artifex commercial licence.

## What this means for you

* **Using it** is unrestricted. That includes an organisation running it
  internally for its own staff.
* **Offering it over a network (section 13).** If you modify SYLTHARAE and
  let other people use your modified version over a network, you must offer
  those users the corresponding source code of your version. Every page,
  including the sign-in page, shows a **Source code** link for this purpose.
  It points at `SOURCE_CODE_URL`
  ([CONFIGURATION.md](CONFIGURATION.md)), which defaults to this repository.
  If you run a modified copy, set it to where your modified source is
  published. Unmodified deployments can leave the default.
* **Redistributing it**, modified or not, must be under the AGPL-3.0-or-later,
  with its source, and with [LICENSE](../LICENSE) and
  [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) intact.

This is a summary. The licence text is authoritative.

## What ships in this repository

* Everything not listed in [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)
  is covered by `AGPL-3.0-or-later`.
* Vendored front-end files (Bootstrap, Bootstrap Icons, Chart.js, jQuery,
  jsPDF, Select2) are MIT-licensed. The fonts (Inter, Noto Sans Arabic, Noto
  Sans Hebrew) are under the SIL Open Font License 1.1, whose text ships in
  `static/fonts/OFL-*.txt`.
* The package metadata declares the licence: `pyproject.toml` has
  `license = "AGPL-3.0-or-later"`, and wheels carry `License-Expression:
  AGPL-3.0-or-later` along with both files above.

## Dependencies

Python dependencies are installed by pip, not shipped here. Check them with:

    python tools/licenses/check_licenses.py            # requirements.txt closure
    python tools/licenses/check_licenses.py --extras   # plus the optional extras

The tool resolves every installed distribution that `requirements.txt` (and,
with `--extras`, the `pyproject.toml` extras) pulls in. It reads each
distribution's `License-Expression`, `License` field or licence classifiers
and fails on:

* an **incompatible** licence, such as `GPL-2.0-only` or a non-commercial one;
* an **unknown** one, meaning nothing usable in the metadata and no entry in
  the tool's `REVIEWED` table. That table records what was read upstream for
  packages whose metadata is missing or ambiguous, and where it was read.

A package that is not installed is reported as `SKIP` because it was not
checked. `--require-installed` turns that into a failure.

Copyleft dependencies in the closure, all compatible with AGPL-3.0-or-later:

* PyMuPDF and EbookLib (AGPL-3.0);
* extract-msg, mobi and pcodedmp (GPL-3.0). Section 13 of the GPL-3.0 permits
  combining them with AGPL-3.0 code;
* html2text (GPL-3.0-or-later);
* mutagen (GPL-2.0-or-later, used under version 3);
* psycopg2 (LGPL-3.0-or-later);
* the py7zr family (LGPL-2.1-or-later);
* the optional `libpff-python` (LGPL-3.0-or-later).

`tests/unit/test_licensing.py` runs the check against the test environment.
It also verifies the licence file, the package metadata, the notices and the
source link.
