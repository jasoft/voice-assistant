import math
import httpx
from .logging import log

def cosine_similarity(v1: list[float], v2: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    mag1 = math.sqrt(sum(a * a for a in v1))
    mag2 = math.sqrt(sum(b * b for b in v2))
    return dot / (mag1 * mag2) if mag1 * mag2 else 0.0

def rerank_with_jina(
    query: str, 
    documents: list[str], 
    api_key: str, 
    base_url: str = "https://api.jina.ai/v1/rerank",
    model: str = "jina-reranker-v2-base-multilingual"
) -> list[float]:
    """Helper function to call Jina AI Reranker API."""
    if not documents:
        return []
    
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                base_url,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model or "jina-reranker-v2-base-multilingual",
                    "query": query,
                    "documents": documents,
                    "top_n": len(documents)
                }
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            # Map back to original order
            scores = [0.0] * len(documents)
            for r in results:
                scores[r["index"]] = r["relevance_score"]
            return scores
    except Exception as e:
        log(f"Jina Rerank failed: {e}", level="error")
        return [0.0] * len(documents)
