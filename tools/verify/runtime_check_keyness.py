"""Runtime check of the keyness analysis against a running server (step 16).

    python tools/verify/runtime_check_keyness.py <base-url> <state-dir>

Needs a database from ``runtime_provision.py`` and nothing else stored:
the two documents it ingests through the real ingestion service carry the
content words ``runtime`` + ``alpha`` and ``runtime`` + ``beta`` (one each),
so the expected result is known by hand:

* alpha against the rest: target 2 tokens, reference 2; only ``alpha`` is
  over-used, G2 = 2 * ln 2 = 1.386..., Log Ratio = log2(0.5 / (0.5 * 0.5 / 2))
  with the 0.5 correction; ``runtime`` is equally frequent (a tie) and belongs
  to neither direction; nothing reaches the 15.13 finding level;
* both sources: nothing is left to compare with -> not measurable,
  ``no_reference`` - never zeros.

Also checks the five voices over HTTP in English and, in the same session
switched to Arabic, the same stored record rendered in Arabic; a JSON
artifact that carries the analysis and a manifest (``report-manifest/2``)
that lists it; a CSV artifact whose manifest says it does not include it.
Evidence is written to ``<state-dir>/evidence/keyness_*.json``.
"""

import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime_check_signals import Client, check, failures  # noqa: E402

TERMINAL = {"COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED", "CANCELLED"}
VOICES = ["measure", "finding", "confidence", "consequence", "caveat"]


def main(base, state_dir):
    state = Path(state_dir)
    info = json.loads((state / "env.json").read_text())
    ids = info["ids"]
    evidence = state / "evidence"
    evidence.mkdir(exist_ok=True)

    def save(name, body):
        (evidence / f"keyness_{name}.json").write_text(
            json.dumps(body, indent=2, ensure_ascii=False))

    owner = Client(base)
    username, password = info["users"]["analyst"]
    status, _, _ = owner.login(username, password)
    check(status == 200 and owner.cookie, f"analyst login -> {status}")
    _, raw, _ = owner.request("GET", "/api/csrf-token")
    owner.csrf = json.loads(raw)["csrf_token"]

    def call(method, path, body=None, **params):
        status, raw, _ = owner.request(method, path, body, params or None)
        try:
            return status, json.loads(raw)
        except ValueError:
            return status, {"raw": raw[:200].decode(errors="replace")}

    def wait(job_id):
        deadline = time.time() + 90
        while time.time() < deadline:
            _, job = call("GET", f"/api/jobs/{job_id}")
            if job.get("job", {}).get("status") in TERMINAL:
                return job["job"]
            time.sleep(0.5)
        return {}

    def run(sources, version=None):
        req = {"report_id": "term_keyness", "parameters": {"criteria": {"sources": sources}}}
        if version is not None:
            req["version"] = version
        status, body = call("POST", "/api/reports/runs", req)
        check(status == 202, f"POST keyness run -> {status} (202, job)")
        job = wait(body["job"]["job_id"])
        check(job.get("status") == "COMPLETED", f"report_run job -> {job.get('status')}")
        return body["run"]["id"]

    call("GET", "/set_language/en")
    status, defs = call("GET", "/api/reports/definitions")
    keyness = [d for d in defs.get("items", []) if d["key"] == "term_keyness@2"]
    check(status == 200 and keyness and keyness[0]["analyses"]
          and keyness[0]["analyses"][0]["key"] == "term_keyness@2"
          and not [d for d in defs.get("items", []) if d["key"] == "term_keyness@1"],
          "definitions list term_keyness@2 with its analysis (v1 superseded, not offered)")

    rid = run([ids["source_alpha"]])
    _, got = call("GET", f"/api/reports/runs/{rid}")
    run_en = got["run"]
    save("run_en", run_en)
    check(run_en["status"] == "completed" and run_en["generator_version"] == "report-runner/2",
          f"run completed by report-runner/2 ({run_en['status']}, {run_en['generator_version']})")
    [analysis] = run_en["analyses"]
    m = analysis["measures"]
    check(analysis["state"] == "measured" and analysis["reason"] is None, "state measured")
    check(m["target_tokens"] == 2 and m["reference_tokens"] == 2 and m["target_contents"] == 1
          and m["reference_contents"] == 1 and m["unknown_count_rows"] == 0,
          f"corpora 2/2 tokens, 1/1 contents, no unknown rows: {m}")
    rows = analysis["rows"]
    check([r["term"] for r in rows] == ["alpha"],
          f"only 'alpha' is over-used; the tie 'runtime' is in neither direction: {rows}")
    expected_lr = math.log2((1 / 2) / (0.5 / 2))
    check(rows and abs(rows[0]["g2"] - 2 * math.log(2)) < 1e-9
          and abs(rows[0]["log_ratio"] - expected_lr) < 1e-9,
          f"G2 = 2 ln 2 and Log Ratio = {expected_lr} (0.5 correction): {rows[:1]}")
    check(m["significant_terms"] == 0, "nothing reaches the p < 0.0001 finding level")
    text_en = analysis["text"]
    check([v["voice"] for v in text_en] == VOICES and all(v["text"].strip() for v in text_en),
          "five voices in order, each with text")
    check("15.13" in text_en[2]["text"] or "15.13" in json.dumps(analysis["narrative"]),
          "the confidence voice cites the 15.13 threshold")
    # NARR-01: one listed term is "1 term", never "1 terms".
    check(analysis["narrative"].get("format") == 2
          and "Listed: 1 term used more often in the selection." in text_en[0]["text"]
          and "1 terms" not in json.dumps(text_en),
          f"plural-aware English measure voice: {text_en[0]['text']!r}")

    call("GET", "/set_language/ar")
    try:
        _, got_ar = call("GET", f"/api/reports/runs/{rid}")
    finally:
        call("GET", "/set_language/en")
    [analysis_ar] = got_ar["run"]["analyses"]
    save("run_ar", got_ar["run"])
    check(analysis_ar["narrative"] == analysis["narrative"],
          "Arabic view reads the same stored narrative record")
    check(all(a["text"] != b["text"] for a, b in zip(analysis_ar["text"], text_en))
          and any("\u0600" <= ch <= "\u06ff" for ch in analysis_ar["text"][1]["text"]),
          "every voice is rendered differently, in Arabic script")
    check("مصطلح واحد" in analysis_ar["text"][0]["text"],
          "Arabic uses its own 'one term' form (catalog Plural-Forms, nplurals=6)")

    old = run([ids["source_alpha"]], version=1)
    _, got_old = call("GET", f"/api/reports/runs/{old}")
    [analysis_v1] = got_old["run"]["analyses"]
    save("run_v1", got_old["run"])
    check(analysis_v1["analysis_key"] == "term_keyness@1"
          and "format" not in analysis_v1["narrative"]
          and analysis_v1["measures"] == analysis["measures"]
          and [v["voice"] for v in analysis_v1["text"]] == VOICES,
          "superseded term_keyness@1 still runs on request and renders its format-1 record")

    both = run([ids["source_alpha"], ids["source_beta"]])
    _, got = call("GET", f"/api/reports/runs/{both}")
    [nm] = got["run"]["analyses"]
    save("run_no_reference", got["run"])
    check(nm["state"] == "not_measurable" and nm["reason"] == "no_reference" and nm["rows"] == [],
          f"whole collection selected -> not_measurable/no_reference ({nm['state']}, {nm['reason']})")
    check([v["voice"] for v in nm["text"]] == VOICES, "...still speaking in five voices")

    manifests = {}
    for fmt in ("json", "csv"):
        req = {"format": fmt}
        if fmt == "csv":
            req["dataset_key"] = "term_keyness.ranked@1"
        status, body = call("POST", f"/api/reports/runs/{rid}/artifacts", req)
        check(status == 202, f"POST {fmt} artifact -> {status}")
        wait(body["job"]["job_id"])
    _, listed = call("GET", f"/api/reports/runs/{rid}/artifacts")
    for item in listed.get("items", []):
        status, mraw, _ = owner.request("GET", f"/api/reports/artifacts/{item['id']}/manifest")
        manifests[item["format"]] = json.loads(mraw)
        if item["format"] == "json":
            status, raw, _ = owner.request("GET", f"/api/reports/artifacts/{item['id']}/download")
            doc = json.loads(raw)
            save("artifact_json", doc)
            check(status == 200 and doc["analyses"][0]["analysis_key"] == "term_keyness@2"
                  and [v["voice"] for v in doc["analyses"][0]["text"]] == VOICES,
                  "the JSON artifact carries the analysis and its five voices")
    save("manifests", manifests)
    mj, mc = manifests.get("json", {}), manifests.get("csv", {})
    check(mj.get("manifest_version") == "report-manifest/2"
          and mj["analyses"][0]["analysis_fingerprint"] == analysis["analysis_fingerprint"]
          and mj["analyses"][0]["included"] is True,
          "JSON manifest (report-manifest/2) lists the analysis as included")
    check(mc.get("analyses") and mc["analyses"][0]["included"] is False,
          "CSV manifest lists the analysis as not included")

    print(f"\n{'FAILED' if failures else 'OK'}: {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
