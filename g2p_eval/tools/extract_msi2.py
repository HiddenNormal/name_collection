"""Extract an MSI without msiexec.

MSI = OLE compound file. Embedded cabinet lives in a stream named "<cab>.cab"
(Media table Cabinet column starts with '#'). We read the stream with olefile
(pure python), write it as a real .cab, expand with Windows expand.exe, then
map short names back to long paths using the MSI File/Directory tables.
"""
import os
import sys
import json
import struct
import shutil
import subprocess
import warnings

warnings.filterwarnings("ignore")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "olefile_pkg"))
import olefile  # noqa: E402

import msilib  # noqa: E402

MSI = sys.argv[1]
WORK = sys.argv[2]
os.makedirs(WORK, exist_ok=True)

ole = olefile.OleFileIO(MSI)
streams = ole.listdir()
print("streams:", [s[-1] for s in streams][:20])

# --- read MSI tables ---
db = msilib.OpenDatabase(MSI, msilib.MSIDBOPEN_READONLY)


def query(sql):
    v = db.OpenView(sql)
    v.Execute(None)
    rows = []
    while True:
        rec = v.Fetch()
        if rec is None:
            break
        rows.append([rec.GetString(i) for i in range(1, rec.GetFieldCount() + 1)])
    v.Close()
    return rows


media = query("SELECT Cabinet FROM Media")
print("media cabinets:", media)
cab_stream = None
for row in media:
    cab = row[0] or ""
    if cab.startswith("#"):
        cab_stream = cab[1:]
        break
if not cab_stream:
    raise SystemExit("no embedded cabinet")
print("embedded cab stream target:", cab_stream)
# MSI stores directory-entry names with its own 3-characters-per-2-bytes
# compression, so olefile shows garbled UTF-16. Identify the cabinet by size:
# it is by far the largest stream in the compound file.
cands = []
for entry in ole.listdir(streams=True, storages=False):
    try:
        size = ole.get_size(entry)
    except Exception:
        continue
    cands.append(("/".join(entry), size))
cands.sort(key=lambda x: -x[1])
print("largest streams:", cands[:5])
cab_stream = cands[0][0]
cab_bytes = ole.openstream(cab_stream).read()
print("cabinet bytes:", len(cab_bytes))
CAB = os.path.join(WORK, "embedded.cab")
open(CAB, "wb").write(cab_bytes)

RAW = os.path.join(WORK, "payload")
if os.path.isdir(RAW):
    shutil.rmtree(RAW)
os.makedirs(RAW, exist_ok=True)
r = subprocess.run(["expand.exe", CAB, "-F:*", RAW], capture_output=True, text=True)
print("expand rc=", r.returncode, r.stdout[-500:], r.stderr[-500:])

# --- reconstruct long names ---
# NOTE: msilib rejects explicit column lists (MSIError 2235) on this MSI, so
# use SELECT * and rely on documented MSI column order.
# Directory:  Directory, Directory_Parent, DefaultDir
# Component:  Component, ComponentId, Directory_, Attributes, Condition, KeyPath
# File:       File, Component_, FileName, FileSize, Version, Language, Attributes, Sequence
dirs = {r0[0]: (r0[1], r0[2]) for r0 in query("SELECT * FROM Directory")}
comp_dir = {r0[0]: r0[2] for r0 in query("SELECT * FROM Component")}
files = [(r0[0], r0[1], r0[2]) for r0 in query("SELECT * FROM File")]
print(f"dirs={len(dirs)} comps={len(comp_dir)} files={len(files)}")


def build_path(key):
    parts = []
    seen = set()
    while key and key not in seen:
        seen.add(key)
        parent, default = dirs.get(key, (None, None))
        if default is None:
            break
        d = default.split("|")[-1]
        if d and d != ".":
            parts.append(d)
        key = parent
    return os.path.join(*reversed(parts)) if parts else ""


mapping = {}
for fkey, comp, fname in files:
    long_name = fname.split("|")[-1] if fname else fkey
    dkey = comp_dir.get(comp, "INSTALLDIR")
    rel = os.path.join(build_path(dkey), long_name)
    mapping[fkey.lower()] = rel
out = os.path.join(WORK, "installed")
renamed = 0
for root, _, fnames in os.walk(RAW):
    for fn in fnames:
        src = os.path.join(root, fn)
        tgt = mapping.get(fn.lower())
        if tgt is None:
            print("  unmapped:", fn)
            continue
        try:
            dst = os.path.join(out, tgt)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            renamed += 1
        except Exception as e:
            print("  COPY FAIL:", fn, "->", tgt, "|", type(e).__name__, e)
print(f"copied {renamed}/{len(files)} files to {out}")

for root, _, fnames in os.walk(out):
    for fn in fnames:
        if fn.lower().endswith((".exe", ".dll")):
            print("  BIN", os.path.relpath(os.path.join(root, fn), out))
data_dir = None
for root, dnames, _ in os.walk(out):
    for d in dnames:
        if d.lower() == "espeak-ng-data":
            data_dir = os.path.join(root, d)
print("DATA_DIR:", data_dir)
json.dump({"data_dir": data_dir, "root": out}, open(os.path.join(WORK, "layout.json"), "w"))
