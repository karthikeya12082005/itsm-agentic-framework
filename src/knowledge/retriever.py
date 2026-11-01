"""Hybrid evidence retrieval (report Algorithm 3.2): BM25 + pgvector cosine search, fused with RRF.

    from src.knowledge.retriever import HybridRetriever
    r = HybridRetriever.from_database()
    evidence = r.retrieve_all("VPN login fails with ERR6518 on OrbitConnect")
"""
import psycopg

from src.common.config import DATABASE_URL, RRF_K, WEAK_EVIDENCE_COSINE, BM25_K1, BM25_B
from src.knowledge.bm25 import BM25
from src.knowledge.embeddings import embed_query, to_pgvector

SOURCES = {          # source name -> (table, metadata columns usable as filters)
    "incidents": ("kb_incidents", ["ticket_type", "category", "subcategory", "service"]),
    "sops": ("kb_sops", ["ticket_type", "category", "subcategory", "service"]),
    "policies": ("kb_policies", ["ticket_type", "category", "subcategory", "service"]),
}
CANDIDATES = 50      # depth of each ranked list before fusion


class PgVectorIndex:
    """Dense search inside PostgreSQL: cosine distance operator <=> on HNSW indexes."""
    def __init__(self, conn):
        self.conn = conn

    def search(self, source, qvec_literal, k, filters):
        table, cols = SOURCES[source]
        where, params = [], []
        if source == "incidents":
            where.append("split = 'train'")                 # never search held-out test tickets
        for col, val in (filters or {}).items():
            if col in cols and val:
                where.append(f"{col} = %s"); params.append(val)
        sql = (f"SELECT id, 1 - (embedding <=> %s::vector) AS cos FROM {table} "
               f"{'WHERE ' + ' AND '.join(where) if where else ''} ORDER BY embedding <=> %s::vector LIMIT %s")
        return [(i, float(c)) for i, c in self.conn.execute(sql, [qvec_literal, *params, qvec_literal, k]).fetchall()]


def rrf_fuse(ranked_lists, k=RRF_K):
    """Report equation 3.3: RRF(d) = sum over lists of 1 / (k + rank(d)), ranks starting at 1."""
    scores = {}
    for lst in ranked_lists:
        for rank, doc_id in enumerate(lst, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: -x[1])


class HybridRetriever:
    def __init__(self, dense_index, docs, meta):
        """docs[source] = {id: text}; meta[source] = {id: {filter_col: value}}"""
        self.dense = dense_index
        self.meta = meta
        self.bm25 = {s: BM25(docs[s], k1=BM25_K1, b=BM25_B) for s in docs}

    @classmethod
    def from_database(cls, database_url=DATABASE_URL):
        conn = psycopg.connect(database_url, autocommit=True)
        docs, meta = {}, {}
        for source, (table, cols) in SOURCES.items():
            where = "WHERE split = 'train'" if source == "incidents" else ""
            rows = conn.execute(f"SELECT id, document_text, {', '.join(cols)} FROM {table} {where}").fetchall()
            docs[source] = {r[0]: r[1] for r in rows}
            meta[source] = {r[0]: dict(zip(cols, r[2:])) for r in rows}
        return cls(PgVectorIndex(conn), docs, meta)

    def _allowed(self, source, filters):
        if not filters:
            return None
        return {i for i, m in self.meta[source].items() if all(m.get(c) == v for c, v in filters.items() if v)}

    def candidates(self, query, source, qvec_literal, filters=None, exclude_ids=()):
        """Both ranked candidate lists (before fusion): ([(id, bm25)], [(id, cosine)])."""
        sparse = [(i, s) for i, s in self.bm25[source].scores(query, self._allowed(source, filters))
                  if i not in exclude_ids][:CANDIDATES]
        dense = [(i, c) for i, c in self.dense.search(source, qvec_literal, CANDIDATES + len(exclude_ids), filters)
                 if i not in exclude_ids][:CANDIDATES]
        return sparse, dense

    @staticmethod
    def fuse(sparse, dense, mode="hybrid", k=5):
        cos = dict(dense)
        if mode == "bm25":
            ranked = [(i, s) for i, s in sparse]
        elif mode == "dense":
            ranked = list(dense)
        else:
            ranked = rrf_fuse([[i for i, _ in sparse], [i for i, _ in dense]])
        return [{"id": i, "score": round(s, 6), "cosine": round(cos[i], 4) if i in cos else None}
                for i, s in ranked[:k]]

    def retrieve(self, query, source, k=5, filters=None, mode="hybrid", qvec_literal=None, exclude_ids=()):
        if qvec_literal is None:
            qvec_literal = to_pgvector(embed_query(query))
        sparse, dense = self.candidates(query, source, qvec_literal, filters, exclude_ids)
        items = self.fuse(sparse, dense, mode, k)
        best_cos = max((c for _, c in dense), default=0.0)
        return {"source": source, "items": items, "best_cosine": round(best_cos, 4),
                "weak": (not items) or best_cos < WEAK_EVIDENCE_COSINE}

    def retrieve_all(self, query, k=5, filters=None, exclude_ids=()):
        """Evidence from all three knowledge sources for one incident (embeds the query once)."""
        qv = to_pgvector(embed_query(query))
        out = {s: self.retrieve(query, s, k, filters, "hybrid", qv, exclude_ids) for s in SOURCES}
        out["weak"] = all(out[s]["weak"] for s in SOURCES)
        return out
