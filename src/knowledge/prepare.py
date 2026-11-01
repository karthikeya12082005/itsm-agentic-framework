"""Step 1 of the knowledge store: build retrieval texts and a reproducible train/test split.

Reads data/raw/*.csv  ->  writes data/processed/{incidents,sops,policies}.jsonl

- document_text : full past-case / SOP / policy text stored in the knowledge base
- query_text    : only what is known when a ticket arrives (no root cause, actions or outcome)
- split         : 'train' tickets form the searchable history; 'test' tickets are held out
"""
import json
import math
import re

import pandas as pd

from src.common.config import PROCESSED_DIR, RAW_DIR, SEED, TEST_FRACTION

INCIDENT_DOC_FIELDS = [("Title", "title"), ("Description", "description"), ("Symptoms", "symptoms"),
                       ("Observed state", "observed_state"), ("Diagnostic data", "diagnostic_data"),
                       ("Investigation steps", "investigation_steps"), ("Root cause", "root_cause"),
                       ("Resolution plan", "resolution_plan"), ("Actions taken", "actions_taken"),
                       ("Resolution summary", "resolution_summary")]
INCIDENT_QUERY_FIELDS = [("Title", "title"), ("Description", "description"), ("Symptoms", "symptoms"),
                         ("Observed state", "observed_state"), ("Diagnostic data", "diagnostic_data")]
SOP_FIELDS = [("SOP", "sop_title"), ("Applies to", "_scope"), ("Trigger", "trigger_condition"),
              ("Symptoms", "symptoms"), ("Prerequisites", "prerequisites"), ("Diagnostic steps", "diagnostic_steps"),
              ("Decision condition", "decision_condition"), ("Recommended action", "recommended_action"),
              ("Verification steps", "verification_steps"), ("Failure condition", "failure_condition"),
              ("Escalation condition", "escalation_condition")]
POLICY_FIELDS = [("Policy", "policy_name"), ("Applies to", "_scope"), ("Condition", "condition"),
                 ("Allowed", "allowed_action"), ("Restricted", "restricted_action"),
                 ("Prohibited", "prohibited_action"), ("Eligible roles", "user_role"),
                 ("Executor role", "required_executor_role"), ("Risk level", "risk_level"),
                 ("Security constraint", "security_constraint")]


def _clean(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if hasattr(v, "item"):                       # numpy scalar -> python
        v = v.item()
    return v


def _join(row, fields):
    parts = []
    for label, col in fields:
        v = _clean(row.get(col))
        if v not in (None, ""):
            parts.append(f"{label}: {v}")
    return "\n".join(parts)


def _scope(row):
    return f"{row['category']} / {row['subcategory']} / {row['service']}"


def query_leaks_root_cause(row) -> bool:
    rc = re.sub(r"^provisional:\s*", "", str(row["root_cause"]), flags=re.I).lower().strip()
    return bool(rc) and rc in _join(row, INCIDENT_QUERY_FIELDS).lower()


def stratified_split(df: pd.DataFrame) -> pd.Series:
    """~20% test per subcategory (so every SOP appears in the test set); leak tickets stay in train."""
    split = pd.Series("train", index=df.index)
    eligible = df[~df["rag_query_leak"]]
    for _, grp in eligible.groupby("subcategory"):
        n_test = max(1, round(len(grp) * TEST_FRACTION))
        split.loc[grp.sample(n=n_test, random_state=SEED).index] = "test"
    return split


def build():
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    tix = pd.read_csv(RAW_DIR / "historical_incidents.csv")
    sops = pd.read_csv(RAW_DIR / "incident_sops.csv")
    pols = pd.read_csv(RAW_DIR / "incident_policies.csv")

    tix["rag_query_leak"] = tix.apply(query_leaks_root_cause, axis=1)
    tix["split"] = stratified_split(tix)
    sops["_scope"] = sops.apply(_scope, axis=1)
    pols["_scope"] = pols.apply(_scope, axis=1)

    def dump(name, rows):
        with open(PROCESSED_DIR / name, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    inc_rows = []
    for _, r in tix.iterrows():
        rec = {c: _clean(r[c]) for c in tix.columns if c not in ("split", "rag_query_leak")}
        inc_rows.append({"id": r["incident_id"], "split": r["split"], "rag_query_leak": bool(r["rag_query_leak"]),
                         "query_text": _join(r, INCIDENT_QUERY_FIELDS),
                         "document_text": _join(r, INCIDENT_DOC_FIELDS), "record": rec})
    dump("incidents.jsonl", inc_rows)
    dump("sops.jsonl", [{"id": r["sop_id"], "document_text": _join(r, SOP_FIELDS),
                         "record": {c: _clean(r[c]) for c in sops.columns if c != "_scope"}} for _, r in sops.iterrows()])
    dump("policies.jsonl", [{"id": r["policy_id"], "document_text": _join(r, POLICY_FIELDS),
                             "record": {c: _clean(r[c]) for c in pols.columns if c != "_scope"}} for _, r in pols.iterrows()])

    n_test = int((tix["split"] == "test").sum())
    print(f"incidents: {len(tix)} (train {len(tix) - n_test}, test {n_test}, "
          f"query-leak tickets kept in train: {int(tix['rag_query_leak'].sum())})")
    print(f"sops: {len(sops)} | policies: {len(pols)}")
    print(f"test subcategories covered: {tix.loc[tix['split'] == 'test', 'subcategory'].nunique()} / {tix['subcategory'].nunique()}")
    print(f"written to {PROCESSED_DIR}")


if __name__ == "__main__":
    build()
