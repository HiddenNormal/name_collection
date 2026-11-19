"""Inspect PE imports of espeak-ng.exe / libespeak_ng.dll to find missing DLLs."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pefile_pkg"))
import requests  # noqa: E402

PKG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pefile_pkg")
if not os.path.isdir(os.path.join(PKG, "pefile")):
    import io
    import zipfile

    r = requests.get("https://pypi.org/pypi/pefile/json", timeout=60, headers={"User-Agent": "dsh"}).json()
    ver = r["info"]["version"]
    url = [f["url"] for f in r["releases"][ver] if f["filename"].endswith(".whl")][0]
    data = requests.get(url, timeout=120, headers={"User-Agent": "dsh"}).content
    os.makedirs(PKG, exist_ok=True)
    zipfile.ZipFile(io.BytesIO(data)).extractall(PKG)

import pefile  # noqa: E402

for path in sys.argv[1:]:
    print("###", path)
    pe = pefile.PE(path, fast_load=False)
    apis = {}
    for e in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []):
        apis[e.dll.decode(errors="replace").lower()] = len(e.imports)
    for dll, n in sorted(apis.items()):
        print(f"   {dll}  ({n} imports)")
    for e in getattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT", []):
        print(f"   [delay] {e.dll.decode(errors='replace')}")
