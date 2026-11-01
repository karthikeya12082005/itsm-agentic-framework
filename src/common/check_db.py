import os
import psycopg
from dotenv import load_dotenv

load_dotenv()
with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    print("Connected:", conn.execute("SELECT version()").fetchone()[0][:60])
    ext = conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()
    print("pgvector:", ext[0] if ext else "NOT INSTALLED")