"""Minimal wheel installer used because `pip install` fails in this sandbox
(pip's unpack-to-temp step hits EACCES on the metadata file).

Resolves the dependency graph from PyPI JSON metadata, downloads wheels with
requests, and unpacks them into --target. Supports pure-python and cp312
win_amd64 wheels; no sdists, no build step.

Usage:
    python tools/wheel_install.py --target pylibs panphon epitran pypinyin pykakasi
    python tools/wheel_install.py --target pylibs_torch torch transformers sentencepiece protobuf
    python tools/wheel_install.py --target pylibs_torch --index https://download.pytorch.org/whl/cpu torch

Notes:
* A few environment markers are evaluated with packaging (present in anaconda);
  extras are ignored.
* If a name has no compatible wheel the installer prints SKIP and moves on.
"""
import argparse
import json
import os
import re
import sys
import zipfile

import requests

H = {"User-Agent": "dsh-wheel-installer"}
PY_TAG = f"cp{sys.version_info.major}{sys.version_info.minor}"
PLAT = "win_amd64"

SKIP = {"pip", "setuptools", "wheel", "pkg_resources", "numpy", "pandas", "pyarrow",
        "colorama", "tqdm", "requests", "urllib3", "idna", "charset_normalizer",
        "charset-normalizer", "certifi", "six", "typing_extensions", "typing-extensions"}


def _verkey(v):
    """Sortable key for a version string, tolerant of prerelease suffixes."""
    out = []
    for part in re.split(r"[.+-]", str(v)):
        m = re.match(r"^(\d+)", part)
        if m:
            out.append((1, int(m.group(1))))
        else:
            out.append((0, part))
    return out


def _marker_ok(marker):
    if not marker:
        return True
    try:
        from packaging.markers import Marker
        return Marker(str(marker)).evaluate()
    except Exception:
        return True


def candidates(name):
    """Return [(version, [(filename, url, kind)])] newest first."""
    r = requests.get(f"https://pypi.org/pypi/{name}/json", headers=H, timeout=60)
    if r.status_code != 200:
        return []
    data = r.json()
    out = []
    for ver, files in data["releases"].items():
        wheels = []
        for f in files:
            fn = f["filename"]
            if not fn.endswith(".whl"):
                continue
            if "py3-none-any" in fn or "py2.py3-none-any" in fn:
                wheels.append((fn, f["url"], "pure"))
            elif PY_TAG in fn and (PLAT in fn or "none-win" in fn):
                wheels.append((fn, f["url"], "binary"))
            elif PY_TAG in fn and "none-any" in fn:
                wheels.append((fn, f["url"], "pure"))
        if wheels:
            out.append((ver, wheels))
    # PyPI dict order is upload order; prefer highest version number
    try:
        out.sort(key=lambda x: _verkey(x[0]), reverse=True)
    except Exception:
        pass
    return out


def pick(name):
    for ver, wheels in candidates(name):
        # prefer binary cp312 wheel, then pure
        wheels.sort(key=lambda w: 0 if w[2] == "binary" else 1)
        return ver, wheels[0]
    return None


def unpack_whl(path, target):
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        for n in names:
            if "/.data/" in n or n.startswith(".data/"):
                continue
            z.extract(n, target)
        # wheel .data/purelib|platlib -> target root
        for n in names:
            m = re.match(r"^(.+?)\.data/(purelib|platlib|scripts|data)/(.*)$", n)
            if not m:
                continue
            _, kind, rest = m.groups()
            dest = target if kind in ("purelib", "platlib") else None
            if dest is None or not rest:
                continue
            full = os.path.join(dest, rest)
            os.makedirs(os.path.dirname(full) or dest, exist_ok=True)
            with open(full, "wb") as f:
                f.write(z.read(n))


def metadata_requires(target, pkg_guess):
    """Read METADATA of the just-unpacked dist-info to list Requires-Dist."""
    for d in os.listdir(target):
        if d.endswith(".dist-info") and d.lower().startswith(pkg_guess.lower().replace("_", "-")):
            mp = os.path.join(target, d, "METADATA")
            if os.path.isfile(mp):
                reqs = []
                for line in open(mp, encoding="utf-8", errors="replace"):
                    if line.startswith("Requires-Dist:"):
                        reqs.append(line.split(":", 1)[1].strip())
                return reqs
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True)
    ap.add_argument("--index", default=None, help="extra simple index (e.g. pytorch cpu)")
    ap.add_argument("pkgs", nargs="+")
    args = ap.parse_args()
    os.makedirs(args.target, exist_ok=True)
    dl = os.path.join(args.target, "_wheels")
    os.makedirs(dl, exist_ok=True)

    queue = list(args.pkgs)
    done = set()
    installed = []
    while queue:
        spec = queue.pop(0)
        name = re.split(r"[<>=!~\[ ;]", spec, maxsplit=1)[0].strip()
        if not name or name.lower() in done or name.lower() in SKIP:
            continue
        done.add(name.lower())
        info = pick(name)
        if not info:
            print(f"SKIP {name}: no compatible wheel")
            continue
        ver, (fn, url, kind) = info
        path = os.path.join(dl, fn)
        if not os.path.exists(path) or os.path.getsize(path) < 1000:
            r = requests.get(url, headers=H, timeout=600)
            if r.status_code != 200:
                print(f"SKIP {name}: download {r.status_code}")
                continue
            open(path, "wb").write(r.content)
        unpack_whl(path, args.target)
        installed.append(f"{name}=={ver}")
        print(f"OK   {name}=={ver}  ({kind}, {os.path.getsize(path)/1e6:.1f}MB)", flush=True)
        reqs = metadata_requires(args.target, name)
        for req in reqs:
            if ";" in req:
                spec2, marker = req.split(";", 1)
                if not _marker_ok(marker):
                    continue
            else:
                spec2 = req
            spec2 = spec2.split("[")[0].strip()
            if "extra ==" in (req.split(";", 1)[1] if ";" in req else ""):
                continue
            nm = re.split(r"[<>=!~ ]", spec2)[0].strip()
            if nm and nm.lower() not in done:
                queue.append(spec2)
    print("\ninstalled:", ", ".join(installed))


if __name__ == "__main__":
    main()
