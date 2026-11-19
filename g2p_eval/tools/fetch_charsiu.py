"""Download CharsiuG2P by hand (the HF hub cache/symlink path is blocked by the
Windows sandbox) and probe it.

Files needed:
  charsiu model: config.json + pytorch_model.bin
  tokenizer:     google/byt5-small spiece.model + tokenizer_config.json + config
Writes charsiu_probe.json.
"""
import json
import os
import sys
import time

import requests

ROOT = r"E:\Repos\name_collection\g2p_eval"
MODEL_DIR = os.path.join(ROOT, "models", "charsiu_byt5_small_100")
TOK_DIR = os.path.join(ROOT, "models", "byt5_small_tokenizer")
sys.path.insert(0, os.path.join(ROOT, "pylibs_torch"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")

H = {"User-Agent": "dsh"}
FILES = {
    MODEL_DIR: [
        ("https://huggingface.co/charsiu/g2p_multilingual_byT5_small_100/resolve/main/config.json", "config.json"),
        ("https://huggingface.co/charsiu/g2p_multilingual_byT5_small_100/resolve/main/pytorch_model.bin", "pytorch_model.bin"),
    ],
    TOK_DIR: [
        ("https://huggingface.co/google/byt5-small/resolve/main/spiece.model", "spiece.model"),
        ("https://huggingface.co/google/byt5-small/resolve/main/tokenizer_config.json", "tokenizer_config.json"),
        ("https://huggingface.co/google/byt5-small/resolve/main/config.json", "config.json"),
        ("https://huggingface.co/google/byt5-small/resolve/main/special_tokens_map.json", "special_tokens_map.json"),
    ],
}


def fetch(url, dest, size_hint=None):
    if os.path.exists(dest) and os.path.getsize(dest) > 1000:
        print(f"cached {dest} ({os.path.getsize(dest)/1e6:.1f}MB)", flush=True)
        return
    t = time.time()
    r = requests.get(url, headers=H, timeout=1800, stream=True)
    r.raise_for_status()
    tmp = dest + ".part"
    n = 0
    with open(tmp, "wb") as f:
        for chunk in r.iter_content(1 << 20):
            f.write(chunk)
            n += len(chunk)
    os.replace(tmp, dest)
    print(f"downloaded {dest} ({n/1e6:.1f}MB in {time.time()-t:.0f}s)", flush=True)


for d, files in FILES.items():
    os.makedirs(d, exist_ok=True)
    for url, name in files:
        try:
            fetch(url, os.path.join(d, name))
        except Exception as e:
            print(f"FAIL {name}: {type(e).__name__}: {e}", flush=True)

import torch  # noqa: E402
from transformers import AutoModelForSeq2SeqLM, ByT5Tokenizer  # noqa: E402

print("torch", torch.__version__, "cuda", torch.cuda.is_available(), flush=True)
t0 = time.time()
tok = ByT5Tokenizer.from_pretrained(TOK_DIR)
print("tokenizer loaded", round(time.time() - t0, 1), flush=True)
model = AutoModelForSeq2SeqLM.from_pretrained(MODEL_DIR)
model.eval()
print("model loaded", round(time.time() - t0, 1), flush=True)

TAGS = {"eng": "<eng-us>", "fra": "<fra>", "deu": "<deu>", "spa": "<spa>",
        "ita": "<ita>", "por": "<por>", "nld": "<nld>", "rus": "<rus>",
        "pol": "<pol>", "tur": "<tur>", "swe": "<swe>", "ara": "<ara>",
        "cmn": "<cmn>", "kor": "<kor>", "jpn": "<jpn>"}
TESTS = {
    "eng": ["John", "Mary", "Smith", "Kowalski", "Xin"],
    "fra": ["Laure", "Jean", "Pierre", "Nicolas"],
    "deu": ["Müller", "Schmidt", "Haus"],
    "spa": ["García", "José", "María"],
    "rus": ["Иван", "Москва", "Наталья"],
    "cmn": ["王", "李", "张"],
    "kor": ["김", "이", "박"],
    "ara": ["محمد", "أحمد"],
}


def run(lang, names):
    tag = TAGS.get(lang, f"<{lang}>")
    inputs = [f"{tag} {n}" for n in names]
    enc = tok(inputs, return_tensors="pt", padding=True)
    with torch.inference_mode():
        gen = model.generate(**enc, max_length=64, num_beams=1)
    return tok.batch_decode(gen, skip_special_tokens=True)


out = {}
for lang, names in TESTS.items():
    t1 = time.time()
    res = run(lang, names)
    dt = time.time() - t1
    out[lang] = dict(names=names, ipa=res, batch=len(names), secs=round(dt, 3))
    print(f"{lang}: {list(zip(names, res))}  ({dt:.2f}s)", flush=True)

words = ["Kowalski", "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia",
         "Miller", "Davis", "Rodriguez"] * 13
words = words[:128]
t1 = time.time()
for i in range(0, len(words), 64):
    run("eng", words[i:i + 64])
dt = time.time() - t1
out["throughput"] = dict(batch=128, secs=round(dt, 2),
                         tokens_per_sec=round(len(words) / dt, 1), device="cpu")
print("throughput:", out["throughput"], flush=True)
json.dump(out, open(os.path.join(ROOT, "charsiu_probe.json"), "w"), ensure_ascii=False, indent=2)
print("DONE")
