"""Shared tokenisation used by the lexical index, the query parser and the hashing embedder."""
from __future__ import annotations

import re
from functools import lru_cache

_CAMEL1 = re.compile(r"([a-z0-9])([A-Z])")
_CAMEL2 = re.compile(r"([A-Z]+)([A-Z][a-z])")
_NONALNUM = re.compile(r"[^A-Za-z0-9]+")

STOPWORDS = frozenset(
    """a an the of to in on at by for from with without and or not no is are was were be been being do does did
    we you i it its this that these those there here where which what who whom how when why can could should would
    will shall may might must our your their my me us them he she they as if then else than so such any all each
    about into over under again also very just only own same too out up down off via per
    self cls return def fn func function class public private protected static final void var let const new
    import from export default null none nil true false int string str bool boolean""".split()
)


def split_words(text: str) -> list[str]:
    """Split identifiers and prose into lowercase sub-words (camelCase, snake_case, digits)."""
    text = _CAMEL1.sub(r"\1 \2", text)
    text = _CAMEL2.sub(r"\1 \2", text)
    return [w.lower() for w in _NONALNUM.split(text) if w]


@lru_cache(maxsize=131072)
def stem(word: str) -> str:
    """Very light, deterministic suffix stripping. Only consistency between index and query matters."""
    w = word
    if len(w) <= 3:
        return w
    if w.endswith("ies") and len(w) > 4:
        w = w[:-3] + "y"
    elif w.endswith("sses"):
        w = w[:-2]
    elif w.endswith("ss"):
        pass
    elif w.endswith("s"):
        w = w[:-1]
    if w.endswith("ation") and len(w) > 7:
        w = w[:-3]
    elif w.endswith(("tion", "sion")) and len(w) > 6:
        w = w[:-3]
    elif w.endswith("ing") and len(w) > 6:
        w = w[:-3]
    elif w.endswith("ed") and len(w) > 5:
        w = w[:-2]
    if w.endswith("e") and len(w) > 4:
        w = w[:-1]
    return w


def tokenize(text: str, *, drop_stopwords: bool = True) -> list[str]:
    """Stemmed sub-word tokens. Single characters are dropped."""
    out = []
    for w in split_words(text):
        if len(w) < 2:
            continue
        if drop_stopwords and w in STOPWORDS:
            continue
        out.append(stem(w))
    return out
