# -*- coding: utf-8 -*-
"""g2p.py -- espeak-ng pronunciation engine (IPA + espeak phoneme string + WAV).

Why this file exists on top of ``g2p_eval/engines.py``
-----------------------------------------------------
* ``engines.EspeakEngine`` calls ``libespeak-ng.dll`` through ctypes (UTF-8 safe, no
  subprocess per token; the Windows console cannot pass non-ASCII argv, so CLI
  invocation is unusable for Cyrillic/Arabic/CJK).  We reuse it read-only.
* The pipeline additionally needs espeak's own **phoneme string** (``-x`` / phoneme
  mode 0).  That is what a Piper-style TTS consumes directly, while the IPA is the
  human-readable deliverable (docs/METHOD.md §3, §5-1).  We get it from the same
  DLL handle (``_phonemize``), with a CLI ``-x -f`` fallback.
* Batched by voice: espeak does ~90k calls/s when the voice does not change, but
  only ~4k/s with a voice switch per token, so callers must group by language.
* WAV synthesis for the demo goes through the CLI because the DLL audio path would
  need an audio device; CLI ``-w`` + ``-f utf8file`` was measured at ~50 ms/name.

Everything is lazy: importing this module only loads the DLL when a G2P object is
constructed, and each failure raises ``G2PUnavailable`` with an actionable message.
"""
from __future__ import annotations

import ctypes
import os
import re
import subprocess
import sys
import tempfile
import time

if __package__:
    from .common import G2P_EVAL_DIR, PYLIBS_DIR, WORK_DIR, package_paths
else:  # `python src/g2p.py` convenience
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from common import G2P_EVAL_DIR, PYLIBS_DIR, WORK_DIR, package_paths

_VOICE_MARKER = re.compile(r"\([a-z]{2,4}(?:-[a-z0-9]+)?\)")

DEFAULT_FALLBACK = "en-us"


class G2PUnavailable(RuntimeError):
    pass


def _strip_markers(s: str) -> str:
    return _VOICE_MARKER.sub("", s or "").strip()


class G2P:
    """espeak-ng wrapper: IPA, espeak phoneme strings, WAV synthesis, batched."""

    PHONEMES_MNEMONIC = 0x00   # espeak_TextToPhonemes mode 0 -> "l'Or"
    PHONEMES_IPA = 0x02        # -> "lˈɔʁ"

    def __init__(self):
        package_paths()
        os.makedirs(WORK_DIR, exist_ok=True)
        try:
            import engines  # g2p_eval/engines.py (read-only dependency)
        except Exception as e:  # pragma: no cover
            raise G2PUnavailable(f"cannot import g2p_eval/engines.py: {e}") from e
        self.engines = engines
        try:
            self.es = engines.EspeakEngine()
        except Exception as e:  # pragma: no cover
            raise G2PUnavailable(f"cannot initialise espeak-ng: {e}") from e
        self.data_dir = self.es.data_dir
        self.exe = os.path.join(self.data_dir, "espeak-ng.exe")
        self._env = dict(os.environ, ESPEAK_DATA_PATH=self.data_dir)
        self._voice_ok: dict[str, bool] = {}
        self._has_lib = hasattr(self.es, "_lib")

    # ------------------------------------------------------------------ voices
    def has_voice(self, lang: str) -> bool:
        if not lang:
            return False
        if lang in self._voice_ok:
            return self._voice_ok[lang]
        try:
            ok = bool(self.es.has_voice(lang))
        except Exception:
            ok = False
        self._voice_ok[lang] = ok
        return ok

    # --------------------------------------------------------------------- IPA
    def ipa(self, text: str, lang: str) -> str:
        """IPA for one (already normalized) token; '' on failure."""
        if not text or not lang:
            return ""
        try:
            return (self.es.ipa(text, lang) or "").strip()
        except Exception:
            return ""

    # ---------------------------------------------------------------- phonemes
    def phonemes(self, text: str, lang: str) -> str:
        """espeak's own phoneme string (the format Piper/Coqui consume)."""
        if not text or not lang:
            return ""
        if not self._has_lib:
            return ""
        try:
            self.es.set_voice(lang)
        except Exception:
            return ""
        words = []
        for word in text.split(" "):
            if not word:
                continue
            raw = word.encode("utf-8") + b"\x00"
            sbuf = ctypes.create_string_buffer(raw)
            ptr = ctypes.c_void_p(ctypes.addressof(sbuf))
            pieces, guard = [], 0
            lib = self.es._lib
            while ptr.value and guard < 64:
                guard += 1
                try:
                    res = lib.espeak_TextToPhonemes(
                        ctypes.byref(ptr), self.es.CHARS_UTF8, self.PHONEMES_MNEMONIC)
                except Exception:
                    return ""
                if not res:
                    break
                pieces.append(res.decode("utf-8", errors="replace"))
            w = _strip_markers("".join(pieces))
            if w:
                words.append(w)
        return " ".join(words)

    # ------------------------------------------------------------- batch entry
    def batch(self, items, progress_every: int = 0, progress_cb=None) -> list[dict]:
        """Resolve many (text, lang, fallback_lang) items, grouped by voice.

        ``items``: iterable of tuples ``(text, lang, fallback_lang)`` (fallback may
        be None).  Returns one dict per item::

            {"ipa":..., "phonemes":..., "lang_used":..., "source":...,
             "note":..., "fallback_used": bool}

        Voice switches are the expensive part (~250 us each), so we sort by lang
        once and never switch inside a group.
        """
        items = list(items)
        out: list[dict | None] = [None] * len(items)
        groups: dict[str, list[int]] = {}
        for i, it in enumerate(items):
            lang = it[1] or ""
            groups.setdefault(lang, []).append(i)

        done = 0
        t0 = time.time()
        for lang, idxs in groups.items():
            if not lang:
                for i in idxs:
                    out[i] = {"ipa": "", "phonemes": "", "lang_used": "",
                              "source": "unresolved:no-voice", "note": "no language assigned",
                              "fallback_used": False}
                continue
            voice_ok = True
            try:
                self.es.set_voice(lang)
            except Exception:
                voice_ok = False
            for i in idxs:
                text, _lang, fb = (items[i] + (None,))[:3] if len(items[i]) < 3 else items[i]
                if voice_ok:
                    ipa = self.ipa(text, lang)
                    phon = self.phonemes(text, lang) if ipa else ""
                    if ipa:
                        out[i] = {"ipa": ipa, "phonemes": phon, "lang_used": lang,
                                  "source": "g2p:espeak", "note": "", "fallback_used": False}
                        continue
                # voice missing or returned nothing -> fallback voice
                fb = fb or (DEFAULT_FALLBACK if lang != DEFAULT_FALLBACK else None)
                if fb and self.has_voice(fb):
                    ipa = self.ipa(text, fb)
                    phon = self.phonemes(text, fb) if ipa else ""
                    if ipa:
                        out[i] = {"ipa": ipa, "phonemes": phon, "lang_used": fb,
                                  "source": "g2p:espeak-fallback",
                                  "note": f"voice {lang!r} failed; used {fb!r}",
                                  "fallback_used": True}
                        continue
                out[i] = {"ipa": "", "phonemes": "", "lang_used": lang,
                          "source": "unresolved:g2p-failed",
                          "note": f"espeak returned nothing for voice {lang!r}",
                          "fallback_used": False}
            done += len(idxs)
            if progress_cb:
                progress_cb(done, len(items), lang, time.time() - t0)
            elif progress_every and done % progress_every < len(idxs):
                print(f"    g2p {done}/{len(items)} ({time.time()-t0:.0f}s)", flush=True)
        return out  # type: ignore[return-value]

    # --------------------------------------------------------------------- WAV
    def wav(self, text: str, lang: str, path: str, speed: int = 150,
            timeout: float = 20.0) -> dict:
        """Synthesize a WAV of ``text`` with espeak-ng CLI; UTF-8-safe via -f file."""
        res = {"ok": False, "path": path, "size": 0, "seconds": 0.0, "note": ""}
        if not text or not lang or not os.path.isfile(self.exe):
            res["note"] = "missing text/lang/exe"
            return res
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        wavtmp = os.path.join(WORK_DIR, "wavtmp")
        os.makedirs(wavtmp, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix="espeak_in_", suffix=".txt", dir=wavtmp)
        try:
            os.close(fd)
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                f.write(text + "\n")
            t0 = time.time()
            proc = subprocess.run(
                [self.exe, "-v", lang, "-s", str(speed), "-w", path, "-f", tmp],
                capture_output=True, env=self._env, timeout=timeout)
            res["seconds"] = round(time.time() - t0, 3)
            if proc.returncode == 0 and os.path.isfile(path) and os.path.getsize(path) > 44:
                res["ok"] = True
                res["size"] = os.path.getsize(path)
            else:
                res["note"] = (proc.stderr or b"")[:200].decode("utf-8", "replace")
        except Exception as e:  # pragma: no cover
            res["note"] = f"{type(e).__name__}: {e}"
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        return res


_G2P: G2P | None = None


def get_g2p() -> G2P:
    """Process-wide singleton (espeak init is cheap but the DLL handle is shared)."""
    global _G2P
    if _G2P is None:
        _G2P = G2P()
    return _G2P


if __name__ == "__main__":  # pragma: no cover - smoke test
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    g = get_g2p()
    print("espeak dir:", g.data_dir)
    cases = [("Laure", "fr-fr"), ("Laure", "en-us"), ("Jean", "fr-fr"),
             ("Jean", "en-us"), ("\u041d\u0430\u0442\u0430\u043b\u044c\u044f", "ru"),
             ("\u0645\u062d\u0645\u062f", "ar"), ("\uae40", "ko"),
             ("\u738b", "cmn"), ("Nguy\u1ec5n", "vi"), ("Tanaka", "en-us")]
    t0 = time.time()
    outs = g.batch([(t, lang, "en-us") for t, lang in cases])
    for (t, lang), o in zip(cases, outs):
        print(f"{lang:8s} {t!r:14s} ipa={o['ipa']!r:24s} phon={o['phonemes']!r:20s} src={o['source']}")
    print(f"batch: {len(cases)} in {time.time()-t0:.3f}s")
    r = g.wav("Jean Laure", "fr-fr", os.path.join(WORK_DIR, "smoke.wav"))
    print("wav:", r)
