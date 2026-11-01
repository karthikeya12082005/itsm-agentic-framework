"""Step 2 of the knowledge store: embed the processed records and load them into Neon (pgvector).
Run after prepare.py:   python -m src.knowledge.ingest
Safe to re-run: tables are emptied and reloaded."""
import json
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from src.common.config import DATABASE_URL, PROCESSED_DIR
from src.knowledge.embeddings import embed_documents, to_pgvector

SCHEMA = Path(__file__).with_name("schema.sql")


def _load(name):
    with open(PROCESSED_DIR / name, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def ingest():
    if not DATABASE_URL:
        raise SystemExit("DATABASE_URL missing in .env")
    inc, sops, pols = _load("incidents.jsonl"), _load("sops.jsonl"), _load("policies.jsonl")

    print("Embedding SOPs ...");      sop_vecs = embed_documents([r["document_text"] for r in sops])
    print("Embedding policies ...");  pol_vecs = embed_documents([r["document_text"] for r in pols])
    print("Embedding incidents ..."); inc_vecs = embed_documents([r["document_text"] for r in inc])

    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute(SCHEMA.read_text(encoding="utf-8"))
        conn.execute("TRUNCATE kb_policies, kb_incidents, kb_sops")
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO kb_sops (id, ticket_type, category, subcategory, service, action_type, risk_level,"
                " document_text, record, embedding) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector)",
                [(r["id"], r["record"]["ticket_type"], r["record"]["category"], r["record"]["subcategory"],
                  r["record"]["service"], r["record"]["action_type"], r["record"]["risk_level"],
                  r["document_text"], Jsonb(r["record"]), to_pgvector(v)) for r, v in zip(sops, sop_vecs)])
            cur.executemany(
                "INSERT INTO kb_policies (id, sop_id, ticket_type, category, subcategory, service, default_decision,"
                " risk_level, document_text, record, embedding) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector)",
                [(r["id"], r["record"]["sop_id"], r["record"]["ticket_type"], r["record"]["category"],
                  r["record"]["subcategory"], r["record"]["service"], r["record"]["default_decision"],
                  r["record"]["risk_level"], r["document_text"], Jsonb(r["record"]), to_pgvector(v))
                 for r, v in zip(pols, pol_vecs)])
            cur.executemany(
                "INSERT INTO kb_incidents (id, split, rag_query_leak, ticket_type, category, subcategory, service,"
                " sop_id, policy_id, root_cause_category, scenario_type, query_text, document_text, record, embedding)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector)",
                [(r["id"], r["split"], r["rag_query_leak"], r["record"]["ticket_type"], r["record"]["category"],
                  r["record"]["subcategory"], r["record"]["service"], r["record"]["sop_id"], r["record"]["policy_id"],
                  r["record"]["root_cause_category"], r["record"]["scenario_type"], r["query_text"],
                  r["document_text"], Jsonb(r["record"]), to_pgvector(v)) for r, v in zip(inc, inc_vecs)])
        conn.commit()
        for t in ("kb_sops", "kb_policies", "kb_incidents"):
            print(t, conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0], "rows")
        print("train/test:", conn.execute("SELECT split, count(*) FROM kb_incidents GROUP BY split ORDER BY split").fetchall())


if __name__ == "__main__":
    ingest()
