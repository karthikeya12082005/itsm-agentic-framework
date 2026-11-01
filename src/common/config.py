"""Project-wide settings loaded from the .env file in the project root."""
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
RESULTS_DIR = PROJECT_ROOT / "evaluation" / "results"

EMBED_MODEL = "BAAI/bge-small-en-v1.5"      # 384-dimensional sentence embeddings
EMBED_DIM = 384
# bge models recommend this instruction in front of short search queries (not documents)
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "

SEED = 42
TEST_FRACTION = 0.20        # share of historical tickets held out for evaluation
RRF_K = 60                  # Reciprocal Rank Fusion smoothing constant
BM25_K1, BM25_B = 1.2, 0.75
WEAK_EVIDENCE_COSINE = 0.55  # below this best-match cosine, evidence is marked weak (tuned later)
