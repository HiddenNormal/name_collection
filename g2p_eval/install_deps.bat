@echo off
rem NOTE: plain `pip install` does NOT work in this sandbox (pip cannot unpack to
rem its temp dir: EACCES on <tmp>/pip-unpack-*/x.whl.metadata). This batch file is
rem kept only as the *intended* install list; the sandbox-safe equivalent that was
rem actually used is `tools/wheel_install.py` (resolves deps from PyPI JSON and
rem unzips wheels into a --target folder). See INSTALL_NOTES.md.
setlocal
set PY=%1
%PY% -m pip install panphon epitran pypinyin pykakasi
%PY% -m pip install torch --index-url https://download.pytorch.org/whl/cpu
%PY% -m pip install transformers sentencepiece
rem sandbox-safe alternative:
rem   %PY% tools\wheel_install.py --target pylibs panphon epitran pypinyin pykakasi
rem   %PY% tools\wheel_install.py --target pylibs_torch torch transformers sentencepiece
