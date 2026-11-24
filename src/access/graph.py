"""The access-request flow now runs inside the supervisor graph (src/orchestrator/graph.py, TODO 2.3).
Kept so existing imports (`from src.access.graph import build_graph`) keep working."""
from src.orchestrator.graph import build_graph, route  # noqa: F401
