## Retrieval evaluation (301 held-out test tickets, no metadata filter)

| Method | SOP Hit@1 | SOP Hit@3 | SOP MRR@10 | Policy Hit@1 | Policy Hit@3 | Policy MRR@10 | Incident P@5 |
|---|---|---|---|---|---|---|---|
| BM25 only | 0.821 | 0.920 | 0.873 | 0.757 | 0.887 | 0.827 | 0.960 |
| Dense only (bge-small) | 0.867 | 0.947 | 0.908 | 0.764 | 0.904 | 0.837 | 0.958 |
| Hybrid (BM25 + dense, RRF k=60) | 0.851 | 0.930 | 0.897 | 0.814 | 0.920 | 0.871 | 0.966 |
