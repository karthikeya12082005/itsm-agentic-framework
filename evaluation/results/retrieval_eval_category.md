## Retrieval evaluation (301 held-out test tickets, with category filter)

| Method | SOP Hit@1 | SOP Hit@3 | SOP MRR@10 | Policy Hit@1 | Policy Hit@3 | Policy MRR@10 | Incident P@5 |
|---|---|---|---|---|---|---|---|
| BM25 only | 0.877 | 0.964 | 0.923 | 0.844 | 0.973 | 0.906 | 0.973 |
| Dense only (bge-small) | 0.904 | 0.990 | 0.946 | 0.807 | 0.980 | 0.887 | 0.976 |
| Hybrid (BM25 + dense, RRF k=60) | 0.887 | 0.980 | 0.934 | 0.857 | 0.980 | 0.917 | 0.980 |
