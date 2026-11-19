"""Extract an MSI without msiexec: find embedded MSCF cabinet in raw bytes,
save it, then expand via Windows expand.exe and rename using MSI tables.

Usage: python extract_msi.py <msi> <workdir>
"""
import os
import struct
import sys
import subprocess
import shutil

MSI = sys.argv[1]
WORK = sys.argv[2]
CAB = os.path.join(WORK, "embedded.cab")
RAW = os.path.join(WORK, "payload")

os.makedirs(WORK, exist_ok=True)
data = open(MSI, "rb").read()
idx = data.find(b"MSCF")
if idx < 0:
    raise SystemExit("no MSCF found")
cb = struct.unpack("<I", data[idx + 8:idx + 12])[0]
cab = data[idx: idx + cb]
open(CAB, "wb").write(cab)
print(f"cabinet offset={idx} size={cb}")

if os.path.isdir(RAW):
    shutil.rmtree(RAW)
os.makedirs(RAW, exist_ok=True)

# expand.exe is a Windows builtin that understands cabinet files (MSZIP)
r = subprocess.run(["expand.exe", CAB, "-F:*", RAW], capture_output=True, text=True)
print("expand rc=", r.returncode)
print(r.stdout[-2000:])
print(r.stderr[-2000:])

# --- map MSI File/Directory tables to long paths ---
import msilib  # noqa: E402  (Windows-only, deprecated but present in 3.12)

db = msilib.OpenDatabase(MSI, msilib.MSIDBOPEN_READONLY)


def query(sql):
    v = db.OpenView(sql)
    v.Execute(None)
    rows = []
    while True:
        rec = v.Fetch()
        if rec is None:
            break
        rows.append([rec.GetString(i) if rec.GetString(i) is not None else None
                     for i in range(1, rec.GetFieldCount() + 1)])
    v.Close()
    return rows


dirs = {r[0]: (r[1], r[2]) for r in query("SELECT Directory, Directory_Parent, DefaultDir FROM Directory")}
files = query("SELECT File, Directory_, FileName FROM File")
print(f"dirs={len(dirs)} files={len(files)}")


def build_path(key):
    parts = []
    seen = set()
    while key and key not in seen:
        seen.add(key)
        parent, default = dirs.get(key, (None, None))
        if default is None:
            break
        d = default.split(":")[-1] if ":" in default else default
        if d and d != ".":
            parts.append(d.split("|")[-1])
        key = parent
    return os.path.join(*reversed(parts)) if parts else ""


mapping = {}
for fkey, dkey, fname in files:
    target = os.path.normpath(os.path.join(build_path(dkey), fname))
    mapping[fkey.lower()] = target

# MSI cabinet entry names are uppercase of File key; match case-insensitively
renamed = 0
for root, _, fnames in os.walk(RAW):
    for fn in fnames:
        src = os.path.join(root, fn)
        tgt = mapping.get(fn.lower())
        if tgt is None:
            continue
        dst = os.path.join(WORK, "installed", tgt)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        renamed += 1
print(f"renamed/copied={renamed} of {len(files)} File-table entries")

# find exe+dll+data
for root, _, fnames in os.walk(os.path.join(WORK, "installed")):
    for fn in fnames:
        if fn.lower().endswith((".exe", ".dll")) or fn in ("phontab", "phonindex", "phondata"):
            print("  ", os.path.relpath(os.path.join(root, fn), WORK))
