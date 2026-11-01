"""Okapi BM25 (report equation 3.1) with a tokenizer that keeps IT identifiers intact."""
import math
import re
from collections import Counter

STOP = set("""a an and are as at be by for from has have in is it its of on or that the this to was were will
with not no can after before when into than then there their they them which while""".split())
TOKEN_RE = re.compile(r"[a-z0-9]+(?:[._\-/:][a-z0-9]+)*")


def tokenize(text: str) -> list:
    """'ERR6518 on vpn-gw-hyd-36' -> ['err6518', 'vpn-gw-hyd-36', 'vpn', 'gw', 'hyd', '36']"""
    out = []
    for tok in TOKEN_RE.findall((text or "").lower()):
        if tok in STOP:
            continue
        out.append(tok)
        parts = re.split(r"[._\-/:]", tok)
        if len(parts) > 1:
            out.extend(p for p in parts if p and p not in STOP)
    return out


class BM25:
    def __init__(self, docs: dict, k1: float = 1.2, b: float = 0.75):
        """docs: {doc_id: text}"""
        self.k1, self.b = k1, b
        self.ids = list(docs)
        self.tf = [Counter(tokenize(docs[i])) for i in self.ids]
        self.len = [sum(c.values()) for c in self.tf]
        self.avgdl = (sum(self.len) / len(self.len)) if self.len else 0.0
        df = Counter(t for c in self.tf for t in c)
        n = len(self.ids)
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def scores(self, query: str, allowed_ids=None) -> list:
        """Returns [(doc_id, score)] sorted by score, optionally restricted to allowed_ids."""
        q = [t for t in tokenize(query) if t in self.idf]
        res = []
        for i, doc_id in enumerate(self.ids):
            if allowed_ids is not None and doc_id not in allowed_ids:
                continue
            tf, dl, s = self.tf[i], self.len[i], 0.0
            for t in q:
                f = tf.get(t, 0)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            if s > 0:
                res.append((doc_id, s))
        res.sort(key=lambda x: -x[1])
        return res
