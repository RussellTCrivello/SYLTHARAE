# Third-party notices

SYLTHARAE is licensed under the GNU Affero General Public License, version 3
or later ([LICENSE](LICENSE), [docs/LICENSING.md](docs/LICENSING.md)). It
ships the third-party files below under their own licences, all of which
permit redistribution in an AGPL-3.0 work. Each file keeps its upstream
copyright header. `tests/unit/test_licensing.py` fails when a vendored file
under `static/` is not listed here.

Python dependencies are installed by pip, not shipped in this repository.
`python tools/licenses/check_licenses.py` checks their licences
([docs/LICENSING.md](docs/LICENSING.md#dependencies)).

## JavaScript and CSS

| File | Project | Version | Licence | Copyright |
|---|---|---|---|---|
| `static/css/bootstrap.min.css`, `static/js/bootstrap.bundle.min.js` | [Bootstrap](https://getbootstrap.com/) | 5.3.0 | MIT | 2011-2023 The Bootstrap Authors |
| `static/icons/bootstrap-icons.min.css`, `static/icons/fonts/bootstrap-icons.woff`, `static/icons/fonts/bootstrap-icons.woff2` | [Bootstrap Icons](https://icons.getbootstrap.com/) | 1.13.1 | MIT | 2019-2024 The Bootstrap Authors |
| `static/js/chart.umd.js` | [Chart.js](https://www.chartjs.org/) | 4.4.0 | MIT | 2023 Chart.js Contributors |
| `static/js/jquery.min.js` | [jQuery](https://jquery.com/) | 3.6.0 | MIT | OpenJS Foundation and other contributors |
| `static/js/jspdf.umd.min.js` | [jsPDF](https://github.com/parallax/jsPDF) | 2.5.1 | MIT | 2010-2021 James Hall, yWorks GmbH and contributors |
| `static/dist/js/select2.min.js`, `static/dist/css/select2.min.css` | [Select2](https://select2.org/) | 4.0.13 | MIT | 2012-2017 Kevin Brown, Igor Vaynberg and Select2 contributors |

## Fonts

Fonts are licensed under the SIL Open Font License 1.1. The full licence text,
with each font's copyright line, ships beside the font files.

| Files | Font | Licence text | Copyright |
|---|---|---|---|
| `static/fonts/inter-*.ttf` | [Inter](https://github.com/rsms/inter) 4.001 | `static/fonts/OFL-Inter.txt` | 2016 The Inter Project Authors |
| `static/fonts/NotoSansArabic*.ttf` | [Noto Sans Arabic](https://github.com/notofonts/arabic) 2.012 | `static/fonts/OFL-NotoSansArabic.txt` | 2022 The Noto Project Authors |
| `static/fonts/NotoSansHebrew-VF.ttf` | [Noto Sans Hebrew](https://github.com/notofonts/hebrew) 3.001 | `static/fonts/OFL-NotoSansHebrew.txt` | 2022-2024 The Noto Project Authors |
