"""Quick ctypes probe of libespeak-ng.dll: does it give clean IPA, fast?"""
import ctypes
import os
import sys
import time

APP = r"E:\Repos\name_collection\g2p_eval\espeak\installed\SourceDir\eSpeak NG"
DLL = os.path.join(APP, "libespeak-ng.dll")
os.add_dll_directory(APP)
lib = ctypes.CDLL(DLL)

lib.espeak_Initialize.restype = ctypes.c_int
lib.espeak_Initialize.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
lib.espeak_SetVoiceByName.restype = ctypes.c_int
lib.espeak_SetVoiceByName.argtypes = [ctypes.c_char_p]
lib.espeak_TextToPhonemes.restype = ctypes.c_char_p
lib.espeak_TextToPhonemes.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_int, ctypes.c_int]

AUDIO_OUTPUT_SYNCHRONOUS = 2
CHARS_UTF8 = 1
PHONEMES_IPA = 0x02

buf = lib.espeak_Initialize(AUDIO_OUTPUT_SYNCHRONOUS, 0, APP.encode(), 0)
print("Initialize ->", buf, "dll dir:", APP)

def set_voice(name):
    rc = lib.espeak_SetVoiceByName(name.encode())
    return rc

def ipa(text, lang):
    rc = set_voice(lang)
    if rc != 0:
        return f"<voice-err {rc}>"
    raw = text.encode("utf-8") + b"\x00"
    sbuf = ctypes.create_string_buffer(raw)
    ptr = ctypes.c_void_p(ctypes.addressof(sbuf))
    out = []
    yld = 0
    while ptr.value and yld < 64:
        yld += 1
        res = lib.espeak_TextToPhonemes(ctypes.byref(ptr), CHARS_UTF8, PHONEMES_IPA)
        if not res:
            break
        out.append(res.decode("utf-8"))
    return " ".join(p for p in out if p)

tests = [("Laure", "fr"), ("Jean", "fr"), ("Jean", "en-us"), ("Smith", "en-us"),
         ("Müller", "de"), ("Haus", "de"), ("García", "es"), ("Rossi", "it"),
         ("Иван", "ru"), ("Москва", "ru"), ("محمد", "ar"), ("김", "ko"),
         ("王", "cmn"), ("wang", "cmn"), ("田中", "ja"), ("たなか", "ja"),
         ("Jansen", "nl"), ("Kowalski", "pl"), ("Yılmaz", "tr"),
         ("Andersson", "sv"), ("Søren", "da"), ("Σωκράτης", "el")]
for name, lang in tests:
    t0 = time.time()
    out = ipa(name, lang)
    print(f"{lang:6s} {name!r:14s} -> {out!r}  ({1000*(time.time()-t0):.2f}ms)")

# throughput on repeated calls
t0 = time.time()
n = 2000
for i in range(n):
    ipa("Kowalski", "pl")
dt = time.time() - t0
print(f"throughput: {n/dt:.0f} calls/s single-thread CPU")
