"""Probe CharsiuG2P (byT5-small multilingual, 100 languages): load, sample
outputs, measure CPU throughput.  Writes charsiu_probe.json.
"""
import json
import os
import sys
import time

ROOT = r"E:\Repos\name_collection\g2p_eval"
sys.path.insert(0, os.path.join(ROOT, "pylibs_torch"))
os.environ.setdefault("HF_HOME", os.path.join(ROOT, "hf_cache"))
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

import torch  # noqa: E402
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer  # noqa: E402

MODEL = "charsiu/g2p_multilingual_byT5_small_100"
print("torch", torch.__version__, "cuda", torch.cuda.is_available(), flush=True)

t0 = time.time()
# the charsiu repo has no tokenizer files; byT5 tokenizer comes from google/byt5-small
tok = AutoTokenizer.from_pretrained("google/byt5-small")
print("tokenizer loaded", time.time() - t0, flush=True)
model = AutoModelForSeq2SeqLM.from_pretrained(MODEL)
model.eval()
print("model loaded", time.time() - t0, flush=True)

TESTS = [
    ("eng", ["John", "Mary", "Smith", "Kowalski", "Xin"]),
    ("fra", ["Laure", "Jean", "Pierre", "Nicolas"]),
    ("deu", ["Müller", "Schmidt", "Haus"]),
    ("spa", ["García", "José", "María"]),
    ("rus", ["Иван", "Москва", "Наталья"]),
    ("cmn", ["王", "李", "张"]),
    ("kor", ["김", "이", "박"]),
    ("ara", ["محمد", "أحمد"]),
]
TAGS = {"eng": "<eng-us>", "fra": "<fra>", "deu": "<deu>", "spa": "<spa>",
        "rus": "<rus>", "cmn": "<cmn>", "kor": "<kor>", "ara": "<ara>"}
out = {}
for lang, names in TESTS:
    tag = TAGS.get(lang, f"<{lang}>")
    inputs = [f"{tag} {n}" for n in names]
    enc = tok(inputs, return_tensors="pt", padding=True)
    t1 = time.time()
    with torch.inference_mode():
        gen = model.generate(**enc, max_length=64, num_beams=1)
    res = tok.batch_decode(gen, skip_special_tokens=True)
    dt = time.time() - t1
    out[lang] = dict(names=names, ipa=res, batch=len(names), secs=round(dt, 3))
    print(f"{lang}: {list(zip(names, res))}  ({dt:.2f}s)", flush=True)

# throughput: 128 english words, batch 64
words = ["Kowalski", "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia",
         "Miller", "Davis", "Rodriguez"] * 13
words = words[:128]
enc = tok([f"<eng-us> {w}" for w in words], return_tensors="pt", padding=True)
t1 = time.time()
with torch.inference_mode():
    for i in range(0, len(words), 64):
        _ = model.generate(**tok([f"<eng-us> {w}" for w in words[i:i + 64]],
                                 return_tensors="pt", padding=True),
                           max_length=64, num_beams=1)
dt = time.time() - t1
out["throughput"] = dict(batch=128, secs=round(dt, 2), tokens_per_sec=round(len(words) / dt, 1),
                         device="cpu")
print("throughput:", out["throughput"], flush=True)
json.dump(out, open(os.path.join(ROOT, "charsiu_probe.json"), "w"), ensure_ascii=False, indent=2)
print("DONE")
