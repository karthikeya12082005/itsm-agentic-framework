-- Knowledge store for the ITSM agents (PostgreSQL + pgvector). 384 = bge-small-en-v1.5 dimension.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS kb_incidents (
    id                  TEXT PRIMARY KEY,
    split               TEXT NOT NULL,            -- 'train' = searchable history, 'test' = held out
    rag_query_leak      BOOLEAN NOT NULL DEFAULT FALSE,
    ticket_type         TEXT, category TEXT, subcategory TEXT, service TEXT,
    sop_id              TEXT, policy_id TEXT, root_cause_category TEXT, scenario_type TEXT,
    query_text          TEXT NOT NULL,
    document_text       TEXT NOT NULL,
    record              JSONB NOT NULL,           -- full original ticket (all 68 fields)
    embedding           vector(384) NOT NULL
);

CREATE TABLE IF NOT EXISTS kb_sops (
    id                  TEXT PRIMARY KEY,
    ticket_type         TEXT, category TEXT, subcategory TEXT, service TEXT,
    action_type         TEXT, risk_level TEXT,
    document_text       TEXT NOT NULL,
    record              JSONB NOT NULL,
    embedding           vector(384) NOT NULL
);

CREATE TABLE IF NOT EXISTS kb_policies (
    id                  TEXT PRIMARY KEY,
    sop_id              TEXT REFERENCES kb_sops(id),
    ticket_type         TEXT, category TEXT, subcategory TEXT, service TEXT,
    default_decision    TEXT, risk_level TEXT,
    document_text       TEXT NOT NULL,
    record              JSONB NOT NULL,
    embedding           vector(384) NOT NULL
);

CREATE INDEX IF NOT EXISTS kb_incidents_emb_idx ON kb_incidents USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS kb_sops_emb_idx      ON kb_sops      USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS kb_policies_emb_idx  ON kb_policies  USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS kb_incidents_cat_idx ON kb_incidents (split, category, subcategory);
