"""Classify the licences of every distribution shipped in the release.

Input: JSON from ``pip-licenses --format=json --with-license-file --no-license-path``
run inside the clean build environment. Output: a JSON report and a non-zero exit
when any shipped distribution has a disallowed or unknown licence.

Policy (redistribution of a binary desktop build):
* allowed:   permissive licences (MIT, BSD, Apache-2.0, ISC, PSF, Unlicense, ...)
* review:    weak copyleft that permits dynamic linking (LGPL, MPL) - shipped with
             licence texts and unmodified, replaceable shared libraries
* forbidden: strong copyleft (GPL without exception, AGPL) and unknown licences
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ALLOWED = re.compile(r"\b(MIT|BSD|Apache|ISC|PSF|Python Software Foundation|Unlicense|"
                     r"Zlib|HPND|CC0|0BSD|Public Domain)\b", re.IGNORECASE)
REVIEW = re.compile(r"\b(LGPL|Lesser General Public|MPL|Mozilla Public)\b", re.IGNORECASE)
FORBIDDEN = re.compile(r"\b(AGPL|Affero|GPL(?!.*exception)|General Public License)\b",
                       re.IGNORECASE)
# Build-time only tools that are never copied into the frozen application.
BUILD_ONLY = {"pyinstaller", "pyinstaller-hooks-contrib", "pip-licenses", "cyclonedx-bom",
              "cyclonedx-python-lib", "pip", "setuptools", "wheel", "altgraph", "pefile",
              "pywin32-ctypes", "prettytable", "wcwidth", "packageurl-python",
              "py-serializable", "sortedcontainers", "license-expression", "boolean.py",
              "defusedxml", "lxml", "chardet", "tomli", "packaging"}


def classify(name: str, licence: str) -> str:
    if name.lower() in BUILD_ONLY:
        return "build_only"
    text = licence or ""
    if not text.strip() or text.strip().upper() in {"UNKNOWN", "UNKNOWN UNKNOWN"}:
        return "unknown"
    if REVIEW.search(text):
        return "review"
    if FORBIDDEN.search(text) and not ALLOWED.search(text):
        return "forbidden"
    if ALLOWED.search(text):
        return "allowed"
    return "unknown"


def main(source: Path, destination: Path, overrides: Path | None = None) -> int:
    rows = json.loads(source.read_text(encoding="utf-8-sig"))  # PowerShell 5 writes a BOM
    manual: dict[str, dict[str, str]] = {}
    if overrides is not None and overrides.is_file():
        manual = json.loads(overrides.read_text(encoding="utf-8-sig"))
    report = []
    for row in rows:
        name, licence = str(row.get("Name")), str(row.get("License") or "")
        verdict = classify(name, licence)
        note = ""
        if verdict in {"unknown", "forbidden"} and name.lower() in manual:
            verdict = manual[name.lower()]["verdict"]
            note = manual[name.lower()]["reason"]
        report.append({"name": name, "version": row.get("Version"), "license": licence,
                       "verdict": verdict, "note": note,
                       "has_license_text": bool(row.get("LicenseText", "").strip()
                                                not in {"", "UNKNOWN"})})
    report.sort(key=lambda item: (item["verdict"], item["name"].lower()))
    blocking = [item for item in report if item["verdict"] in {"forbidden", "unknown"}]
    destination.write_text(json.dumps({"policy": __doc__.strip(), "packages": report,
                                       "blocking": blocking}, indent=2), encoding="utf-8")
    for item in report:
        print(f"{item['verdict']:<10} {item['name']:<32} {item['license'][:60]}")
    if blocking:
        print(f"\n{len(blocking)} distribution(s) block release:", file=sys.stderr)
        for item in blocking:
            print(f"  {item['name']}: {item['license'] or 'no licence metadata'}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]), Path(sys.argv[2]),
                          Path(sys.argv[3]) if len(sys.argv) > 3 else None))
