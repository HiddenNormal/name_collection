"""Uniform G2P / IPA engine wrapper for the name-pronunciation evaluation.

Single entry point::

    from engines import ipa
    ipa("Laure", "fr")      # -> 'lˈɔʁ'
    ipa("Smith", "en-us")   # -> 'smˈɪθ'

Design
------
* ``EspeakEngine`` is the backbone: ctypes -> libespeak-ng.dll, no subprocess,
  UTF-8 native (the Windows console mangles non-ASCII argv, so CLI invocation is
  NOT usable for Cyrillic/Arabic/CJK).  ~6.5k names/s single core, IPA with
  stress marks, and it is the exact phoneme front-end used by Piper/Coqui/
  StyleTTS2, so output is TTS-ready.
* ``WikiPronLexiconEngine``: exact-match dictionary from WikiPron broad TSVs.
* ``EpitranEngine``: rule/lexicon G2P (pip ``epitran``), ISO 639-3 codes.
* ``CharsiuEngine``: byT5-small multilingual neural G2P (HF).
* ``EspakPlusEngine``: script-normalising front-ends for cmn (pypinyin -> espeak
  pinyin) and jpn (pykakasi kanji->kana -> espeak ja), plus raw espeak.

Engines are lazy: importing this module needs nothing but the stdlib; each
engine raises a one-line ``EngineUnavailable`` with the install hint when its
backend is missing.

Windows notes
-------------
Set ``G2P_ESPEAK_DIR`` to the directory *containing* ``espeak-ng-data`` and
``libespeak-ng.dll``.  Default: ``g2p_eval/espeak/installed/SourceDir/eSpeak NG``.
Rebuild that folder with ``tools/extract_msi2.py`` (see INSTALL_NOTES.md).
"""
from __future__ import annotations

import ctypes
import glob
import os
import re
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))


class EngineUnavailable(RuntimeError):
    """Raised when an engine's backend is not installed/importable."""


# --------------------------------------------------------------------------
# shared text cleaning
# --------------------------------------------------------------------------
_TATWEEL = "\u0640"
_ARABIC_DIACRITICS = re.compile("[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06dc\u06df-\u06e8\u06ea-\u06ed]")
_ZERO_WIDTH = re.compile("[\u200b-\u200f\u202a-\u202e\ufeff\u00ad]")


def clean_name(name: str, language: str | None = None) -> str:
    """Normalise a raw name token before G2P.

    - NFC unicode, strip zero-width/control junk
    - strip Arabic tatweel (U+0640 decoration) and Arabic diacritics
      (dirty-data normalisation; the espeak ar dictionary does fine without them)
    - collapse whitespace, strip punctuation at the edges
    """
    if not name:
        return ""
    s = unicodedata.normalize("NFC", name)
    s = _ZERO_WIDTH.sub("", s)
    s = s.replace(_TATWEEL, "")
    s = _ARABIC_DIACRITICS.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    # trim obvious junk punctuation at the edges but keep hyphens/apostrophes
    s = re.sub(r"^[\s,;:.\"'`~!@#$%^&*()\[\]{}=+<>/\\|]+", "", s)
    s = re.sub(r"[\s,;:.\"'`~!@#$%^&*()\[\]{}=+<>/\\|]+$", "", s)
    return s


# espeak-ng voice-switch markers like "(en)" / "(cmn)" are an internal
# language-detection artefact; drop them from the IPA output.
_VOICE_MARKER = re.compile(r"\([a-z]{2,4}(?:-[a-z0-9]+)?\)")


def _strip_voice_markers(ipa: str) -> str:
    return _VOICE_MARKER.sub("", ipa).strip()


# --------------------------------------------------------------------------
# espeak-ng
# --------------------------------------------------------------------------
def find_espeak_dir() -> str:
    cands = []
    env = os.environ.get("G2P_ESPEAK_DIR") or os.environ.get("ESPEAK_DATA_PATH")
    if env:
        cands.append(env)
    cands += [
        os.path.join(HERE, "espeak", "installed", "SourceDir", "eSpeak NG"),
        os.path.join(HERE, "espeak", "dist"),
    ]
    for d in cands:
        if d and os.path.isfile(os.path.join(d, "libespeak-ng.dll")):
            return d
        if d and os.path.isfile(os.path.join(d, "libespeak_ng.dll")):
            return d
    raise EngineUnavailable(
        "espeak-ng not found. Build the portable folder with "
        "`python tools/extract_msi2.py dl/espeak-ng-1.52.0.msi espeak` or set "
        "G2P_ESPEAK_DIR to the folder holding espeak-ng-data/ + libespeak-ng.dll"
    )


class EspeakEngine:
    """espeak-ng via its C API (libespeak-ng.dll), synchronous, no audio."""

    name = "espeak-ng"
    # audio output modes (speak_lib.h)
    AUDIO_OUTPUT_SYNCHRONOUS = 2
    CHARS_UTF8 = 1
    PHONEMES_IPA = 0x02

    def __init__(self, data_dir: str | None = None):
        self.data_dir = data_dir or find_espeak_dir()
        self._load_lib()
        self._lib.espeak_Initialize.restype = ctypes.c_int
        self._lib.espeak_Initialize.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        self._lib.espeak_SetVoiceByName.restype = ctypes.c_int
        self._lib.espeak_SetVoiceByName.argtypes = [ctypes.c_char_p]
        self._lib.espeak_TextToPhonemes.restype = ctypes.c_char_p
        self._lib.espeak_TextToPhonemes.argtypes = [
            ctypes.POINTER(ctypes.c_void_p), ctypes.c_int, ctypes.c_int]
        buf = self._lib.espeak_Initialize(self.AUDIO_OUTPUT_SYNCHRONOUS, 0,
                                          self.data_dir.encode("utf-8"), 0)
        if buf <= 0:
            raise EngineUnavailable(f"espeak_Initialize failed ({buf}); bad data dir {self.data_dir}")
        self._voice = None
        self.set_voice("en-us")

    def _load_lib(self):
        d = self.data_dir
        dll = None
        for nm in ("libespeak-ng.dll", "libespeak_ng.dll", "espeak-ng.dll"):
            p = os.path.join(d, nm)
            if os.path.isfile(p):
                dll = p
                break
        if dll is None:
            raise EngineUnavailable(f"no libespeak-ng.dll in {d}")
        if hasattr(os, "add_dll_directory"):  # Windows: let the DLL loader find its siblings
            try:
                os.add_dll_directory(d)
            except OSError:
                pass
        self._lib = ctypes.CDLL(dll)

    # -- voice handling ---------------------------------------------------
    def set_voice(self, lang: str) -> None:
        if lang == self._voice:
            return
        rc = self._lib.espeak_SetVoiceByName(lang.encode("utf-8"))
        if rc != 0:
            # try language-only fallback ("en-us" -> "en") and espeak "name" variants
            base = lang.split("-")[0].split("_")[0]
            if base != lang and self._lib.espeak_SetVoiceByName(base.encode("utf-8")) == 0:
                lang = base
                rc = 0
        if rc != 0:
            raise EngineUnavailable(f"espeak-ng has no voice {lang!r}")
        self._voice = lang

    def has_voice(self, lang: str) -> bool:
        try:
            self.set_voice(lang)
            return True
        except EngineUnavailable:
            return False

    # -- main entry -------------------------------------------------------
    def ipa(self, text: str, lang: str) -> str:
        text = clean_name(text)
        if not text:
            return ""
        self.set_voice(lang)
        words = []
        for word in text.split(" "):
            raw = word.encode("utf-8") + b"\x00"
            sbuf = ctypes.create_string_buffer(raw)
            ptr = ctypes.c_void_p(ctypes.addressof(sbuf))
            pieces = []
            guard = 0
            while ptr.value and guard < 64:
                guard += 1
                res = self._lib.espeak_TextToPhonemes(
                    ctypes.byref(ptr), self.CHARS_UTF8, self.PHONEMES_IPA)
                if not res:
                    break
                pieces.append(res.decode("utf-8", errors="replace"))
            w = _strip_voice_markers("".join(pieces))
            if w:
                words.append(w)
        return " ".join(words)

    def ipa_batch(self, texts, lang: str):
        return [self.ipa(t, lang) for t in texts]

    def available_languages(self):
        """Parse `espeak-ng --voices` style output is CLI-only; use the lang dir."""
        out = set()
        for p in glob.glob(os.path.join(self.data_dir, "espeak-ng-data", "lang", "*", "*")):
            out.add(os.path.basename(p))
        for p in glob.glob(os.path.join(self.data_dir, "espeak-ng-data", "lang", "*")):  # top-level files
            if os.path.isfile(p):
                out.add(os.path.basename(p))
        return sorted(out)


# --------------------------------------------------------------------------
# WikiPron lexicon
# --------------------------------------------------------------------------
_LEXICON_DIR = os.path.join(HERE, "lexicons")
# language -> wikipron tsv basename (without _broad.tsv)
_WIKIPRON_FILES = {}


def _discover_wikipron():
    if _WIKIPRON_FILES:
        return _WIKIPRON_FILES
    for p in glob.glob(os.path.join(_LEXICON_DIR, "*_broad.tsv")):
        base = os.path.basename(p)[:-len("_broad.tsv")]
        parts = base.split("_")
        if len(parts) >= 2:
            _WIKIPRON_FILES.setdefault(parts[0], p)
            _WIKIPRON_FILES["_".join(parts)] = p
    return _WIKIPRON_FILES


def _norm_key(word: str) -> str:
    return unicodedata.normalize("NFC", word).strip().lower()


class WikiPronLexiconEngine:
    """Exact-match lookup in WikiPron broad TSVs. Returns '' for OOV."""

    name = "wikipron-lexicon"

    def __init__(self, lexicon_dir: str | None = None):
        self.dir = lexicon_dir or _LEXICON_DIR
        self._cache = {}

    def _load(self, lang: str):
        if lang in self._cache:
            return self._cache[lang]
        files = _discover_wikipron()
        path = files.get(lang)
        if not path:
            base = lang.split("-")[0]
            path = files.get(base)
        if not path:
            raise EngineUnavailable(f"no wikipron lexicon for {lang!r} (looked in {self.dir})")
        table = {}
        with open(path, encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 2 or not parts[0] or not parts[1]:
                    continue
                k = _norm_key(parts[0])
                ipa = parts[1].strip()
                if not ipa:
                    continue
                # keep the most frequent pronunciation if the tsv has counts
                if k not in table:
                    table[k] = ipa
                elif len(parts) > 2:
                    try:
                        if float(parts[2]) > table.get((k, "_freq"), 0):
                            table[k] = ipa
                    except ValueError:
                        pass
        self._cache[lang] = table
        return table

    def ipa(self, text: str, lang: str) -> str:
        text = clean_name(text)
        if not text:
            return ""
        table = self._load(lang)
        parts = [_norm_key(w) for w in text.split()]
        got = [table.get(w, "") for w in parts]
        return " ".join(g for g in got if g)

    def lookup(self, text: str, lang: str) -> str | None:
        table = self._load(lang)
        key = _norm_key(clean_name(text))
        return table.get(key)

    def coverage(self, tokens, lang: str) -> float:
        table = self._load(lang)
        toks = [_norm_key(t) for t in tokens if t and clean_name(t)]
        if not toks:
            return 0.0
        hits = sum(1 for t in toks if t in table)
        return hits / len(toks)

    def size(self, lang: str) -> int:
        return len(self._load(lang))


# --------------------------------------------------------------------------
# Epitran
# --------------------------------------------------------------------------
# language -> most specific epitran code that exists
_EPITRAN_CODES = {
    "eng": "eng-Latn", "en": "eng-Latn", "en-us": "eng-Latn", "en-gb": "eng-Latn",
    "fra": "fra-Latn", "fr": "fra-Latn", "spa": "spa-Latn", "es": "spa-Latn",
    "deu": "deu-Latn", "de": "deu-Latn", "ita": "ita-Latn", "it": "ita-Latn",
    "por": "por-Latn", "pt": "por-Latn", "nld": "nld-Latn", "nl": "nld-Latn",
    "rus": "rus-Cyrl", "ru": "rus-Cyrl", "pol": "pol-Latn", "pl": "pol-Latn",
    "tur": "tur-Latn", "tr": "tur-Latn", "swe": "swe-Latn", "sv": "swe-Latn",
    "ara": "ara-Arab", "ar": "ara-Arab", "cmn": "cmn-Hans", "zh": "cmn-Hans",
    "kor": "kor-Hang", "ko": "kor-Hang", "jpn": "jpn-Jpan", "ja": "jpn-Jpan",
    "hin": "hin-Deva", "hi": "hin-Deva", "vie": "vie-Latn", "tha": "tha-Thai",
    "heb": "heb-Hebr", "ell": "ell-Grek", "dan": "dan-Latn", "fin": "fin-Latn",
    "nor": "nor-Latn", "cat": "cat-Latn", "ces": "ces-Latn", "hun": "hun-Latn",
    "ron": "ron-Latn", "ukr": "ukr-Cyrl", "fas": "fas-Arab", "urd": "urd-Arab",
    "ben": "ben-Beng", "tam": "tam-Taml", "tel": "tel-Telu",
}


class EpitranEngine:
    name = "epitran"

    def __init__(self):
        try:
            import epitran  # noqa: F401
        except Exception as e:  # pragma: no cover
            raise EngineUnavailable(
                f"epitran not importable ({e}); install: pip install epitran panphon") from e
        self._ep = {}
        self._epitran = epitran

    def _get(self, lang: str):
        code = _EPITRAN_CODES.get(lang, lang)
        if code in self._ep:
            return self._ep[code]
        try:
            ep = self._epitran.Epitran(code)
        except Exception as e:
            # try the base language with any script
            base = code.split("-")[0]
            try:
                ep = self._epitran.Epitran(base)
                code = base
            except Exception:
                raise EngineUnavailable(f"epitran has no mapping for {lang!r} ({code}): {e}") from e
        self._ep[code] = ep
        return ep

    def ipa(self, text: str, lang: str) -> str:
        text = clean_name(text)
        if not text:
            return ""
        ep = self._get(lang)
        try:
            return ep.transliterate(text)
        except Exception:
            return ""

    def available(self):
        """Probe every epitran mapping; returns {code: bool}."""
        import pkgutil
        out = {}
        try:
            import epitran
            from epitran import data as edata
            base = os.path.dirname(epitran.__file__)
            for p in glob.glob(os.path.join(base, "data", "*.json")):
                code = os.path.basename(p)[:-5]
                out[code] = True
        except Exception:
            pass
        return out


# --------------------------------------------------------------------------
# CharsiuG2P (byT5-small multilingual, 100 languages)
# --------------------------------------------------------------------------
# CharsiuG2P language tags; see https://huggingface.co/charsiu/g2p_multilingual_byT5_small_100
_CHARSIU_TAGS = {
    "eng": "<eng-us>", "en": "<eng-us>", "en-us": "<eng-us>", "en-gb": "<eng-uk>",
    "fra": "<fra>", "fr": "<fra>", "spa": "<spa>", "es": "<spa>",
    "deu": "<deu>", "de": "<deu>", "ita": "<ita>", "it": "<ita>",
    "por": "<por>", "pt": "<por>", "nld": "<nld>", "nl": "<nld>",
    "rus": "<rus>", "ru": "<rus>", "pol": "<pol>", "pl": "<pol>",
    "tur": "<tur>", "tr": "<tur>", "swe": "<swe>", "sv": "<swe>",
    "ara": "<ara>", "ar": "<ara>", "cmn": "<cmn>", "zh": "<cmn>",
    "kor": "<kor>", "ko": "<kor>", "jpn": "<jpn>", "ja": "<jpn>",
    "hin": "<hin>", "hi": "<hin>", "vie": "<vie>", "heb": "<heb>",
    "ell": "<ell>", "dan": "<dan>", "fin": "<fin>", "nor": "<nor>",
    "cat": "<cat>", "ces": "<ces>", "hun": "<hun>", "ron": "<ron>",
    "ukr": "<ukr>",
}


class CharsiuEngine:
    name = "charsiu-byt5"
    # the HuggingFace hub cache is blocked in the sandbox: use the hand-fetched
    # local model dirs when present (see tools/fetch_charsiu.py)
    LOCAL_MODEL = os.path.join(HERE, "models", "charsiu_byt5_small_100")
    LOCAL_TOKENIZER = os.path.join(HERE, "models", "byt5_small_tokenizer")

    def __init__(self, model_id: str = "charsiu/g2p_multilingual_byT5_small_100",
                 device: str | None = None, max_len: int = 64):
        try:
            import torch
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        except Exception as e:
            raise EngineUnavailable(
                f"torch/transformers not importable ({e}); install: pip install torch transformers sentencepiece") from e
        import torch as _t
        self.torch = _t
        self.model_id = model_id
        if device is None:
            device = "cuda" if _t.cuda.is_available() else "cpu"
        self.device = device
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        # local files first (the Charsiu repo ships no tokenizer: use byT5's)
        if os.path.isdir(self.LOCAL_MODEL):
            try:
                from transformers import ByT5Tokenizer
                self.tok = ByT5Tokenizer.from_pretrained(self.LOCAL_TOKENIZER)
            except Exception:
                self.tok = AutoTokenizer.from_pretrained("google/byt5-small")
            self.model = AutoModelForSeq2SeqLM.from_pretrained(self.LOCAL_MODEL)
        else:
            self.tok = AutoTokenizer.from_pretrained("google/byt5-small")
            self.model = AutoModelForSeq2SeqLM.from_pretrained(model_id)
        self.model = self.model.to(device).eval()
        self.max_len = max_len

    def _tag(self, lang):
        return _CHARSIU_TAGS.get(lang, f"<{lang}>")

    def ipa_batch(self, texts, lang: str, batch_size: int = 64, progress: bool = False):
        tag = self._tag(lang)
        inputs = [f"{tag} {clean_name(t)}" for t in texts]
        outs = []
        with self.torch.inference_mode():
            for i in range(0, len(inputs), batch_size):
                chunk = inputs[i:i + batch_size]
                enc = self.tok(chunk, return_tensors="pt", padding=True,
                               truncation=True, max_length=self.max_len).to(self.device)
                gen = self.model.generate(**enc, max_length=self.max_len, num_beams=1)
                outs.extend(self.tok.batch_decode(gen, skip_special_tokens=True))
                if progress and (i // batch_size) % 10 == 0:
                    print(f"  charsiu {i+len(chunk)}/{len(inputs)}", file=sys.stderr)
        return outs

    def ipa(self, text: str, lang: str) -> str:
        return self.ipa_batch([text], lang)[0]


# --------------------------------------------------------------------------
# script-normalising espeak front-ends (CJK)
# --------------------------------------------------------------------------
class EspeakPlusEngine:
    """espeak-ng with script preprocessing for Chinese and Japanese.

    cmn: Hanzi -> pinyin (pypinyin, tone-less) -> espeak-ng pinyin reading.
         espeak's own cmn voice also accepts Hanzi directly; we compare both.
    jpn: Kanji -> kana (pykakasi) -> espeak-ng ja (kana only).
    """

    name = "espeak-plus"

    def __init__(self, espeak: EspeakEngine | None = None):
        self.es = espeak or EspeakEngine()
        self._pinyin = None
        self._kakasi = None

    def ipa(self, text: str, lang: str, mode: str | None = None) -> str:
        text = clean_name(text)
        if not text:
            return ""
        base = lang.split("-")[0]
        if base in ("cmn", "zh", "yue"):
            if mode == "hanzi":
                return self.es.ipa(text, "cmn")
            if self._pinyin is None:
                try:
                    from pypinyin import Style, lazy_pinyin
                except Exception as e:
                    raise EngineUnavailable(f"pypinyin missing ({e})") from e
                self._pinyin = (Style, lazy_pinyin)
            Style, lazy_pinyin = self._pinyin
            # tone-3 pinyin ("ni3 hao3") fed to espeak's pinyin voice keeps the
            # lexical tone, which plain espeak-on-Hanzi does not reliably emit.
            py = " ".join(lazy_pinyin(text, style=Style.TONE3, neutral_tone_with_five=True))
            try:
                return self.es.ipa(py, "cmn-latn-pinyin")
            except EngineUnavailable:
                return self.es.ipa(" ".join(lazy_pinyin(text, style=Style.NORMAL)), "cmn")
        if base in ("jpn", "ja"):
            if self._kakasi is None:
                try:
                    import pykakasi
                except Exception as e:
                    raise EngineUnavailable(f"pykakasi missing ({e})") from e
                k = pykakasi.kakasi()
                if hasattr(k, "convert"):  # pykakasi >= 0.3
                    self._kakasi = ("modern", k)
                else:  # legacy pykakasi 0.2x API: kanji -> hiragana converter
                    k.setMode("J", "H")
                    k.setMode("K", "H")
                    k.setMode("H", "H")
                    self._kakasi = ("legacy", k.getConverter())
            kind, kk = self._kakasi
            if kind == "modern":
                res = kk.convert(text)
                hira = " ".join(r["hira"] for r in res if r.get("hira"))
            else:
                hira = kk.do(text)
            return self.es.ipa(hira, "ja")
        return self.es.ipa(text, lang)


# --------------------------------------------------------------------------
# naive baselines (documented as "worst approach")
# --------------------------------------------------------------------------
class RawGraphemeEngine:
    """The null baseline: return the spelling itself, lowercased.

    This is what a pipeline degenerates to if nobody does G2P. It exists so the
    report can quantify exactly how bad it is (it will score catastrophically
    low on IPA metrics, which is the point).
    """

    name = "raw-grapheme"

    def ipa(self, text: str, lang: str) -> str:
        return clean_name(text).lower()


class LatinApproxEngine:
    """Cheap 'looks-like-IPA' transliteration used by many naive pipelines.

    Applies a tiny English-ish grapheme mapping (c->k, ph->f, ...). Still bad,
    but a realistic strawman.
    """

    name = "latin-approx"
    _RULES = [("tch", "tʃ"), ("ph", "f"), ("sh", "ʃ"), ("ch", "tʃ"), ("th", "θ"),
              ("ck", "k"), ("qu", "kw"), ("j", "dʒ"), ("x", "ks"), ("c", "k"),
              ("y", "j"), ("w", "v")]

    def ipa(self, text: str, lang: str) -> str:
        s = clean_name(text).lower()
        for a, b in self._RULES:
            s = s.replace(a, b)
        return s


# --------------------------------------------------------------------------
# registry + routing
# --------------------------------------------------------------------------
_ENGINES = {
    "espeak": EspeakEngine,
    "espeak-plus": EspeakPlusEngine,
    "wikipron-lexicon": WikiPronLexiconEngine,
    "epitran": EpitranEngine,
    "charsiu": CharsiuEngine,
    "raw-grapheme": RawGraphemeEngine,
    "latin-approx": LatinApproxEngine,
}

# Recommended routing (populated/validated by the benchmark; see EVAL_REPORT.md).
# lang -> (primary engine, fallback engine)
ROUTING = {
    "eng": ("espeak", None), "fra": ("espeak", None), "spa": ("espeak", None),
    "deu": ("espeak", None), "ita": ("espeak", None), "por": ("espeak", None),
    "nld": ("espeak", None), "rus": ("espeak", None), "pol": ("espeak", None),
    "tur": ("espeak", None), "swe": ("espeak", None), "ara": ("espeak", None),
    "cmn": ("espeak-plus", "espeak"), "kor": ("espeak", None), "jpn": ("espeak-plus", "espeak"),
}

_INST = {}


def get_engine(name: str):
    if name not in _INST:
        _INST[name] = _ENGINES[name]()
    return _INST[name]


def ipa(name: str, lang: str, engine: str | None = None) -> str:
    """Convert one name to IPA.

    >>> ipa("Laure", "fr")
    'lˈɔʁ'
    """
    if not name:
        return ""
    lang = (lang or "en").lower().replace("_", "-")
    if engine is None:
        eng, fb = ROUTING.get(lang.split("-")[0], ("espeak", None))
        try:
            return get_engine(eng).ipa(name, lang)
        except NotImplementedError:
            return get_engine(fb).ipa(name, lang)
    try:
        return get_engine(engine).ipa(name, lang)
    except NotImplementedError:
        return ""


def ipa_batch(names, lang: str, engine: str | None = None):
    """Batch wrapper. Uses the engine's native batch path when it has one."""
    lang = (lang or "en").lower().replace("_", "-")
    eng = engine
    if eng is None:
        eng = ROUTING.get(lang.split("-")[0], ("espeak", None))[0]
    e = get_engine(eng)
    if hasattr(e, "ipa_batch"):
        try:
            return e.ipa_batch(names, lang)
        except TypeError:
            pass
    return [e.ipa(n, lang) for n in names]


if __name__ == "__main__":
    import json
    # tiny smoke test: python engines.py
    for name, lang in [("Laure", "fr"), ("Jean", "en-us"), ("Müller", "de"),
                       ("García", "es"), ("Иван", "ru"), ("محمد", "ar"),
                       ("김", "ko"), ("王", "cmn"), ("たなか", "ja"),
                       ("Kowalski", "pl"), ("Σωκράτης", "el")]:
        try:
            print(f"{lang:6s} {name:14s} -> {ipa(name, lang)!r}")
        except EngineUnavailable as e:
            print(f"{lang:6s} {name:14s} -> unavailable: {e}")
