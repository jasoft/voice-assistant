import os
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.append(str(Path(__file__).resolve().parents[1]))

from press_to_talk.storage.service import JinaEmbeddingClient

def test_jina_embedding():
    api_key = os.environ.get("JINA_API_KEY")
    if not api_key:
        print("JINA_API_KEY not found in environment")
        return

    client = JinaEmbeddingClient(
        api_key=api_key,
        model="jina-embeddings-v5-text-small"
    )

    texts = ["你好，世界", "Hello world"]
    print(f"Testing Jina embedding with model: {client.model}")
    print(f"Base URL: {client.base_url}")
    
    embeddings = client.embed_many(texts)
    
    if embeddings:
        print(f"Successfully generated {len(embeddings)} embeddings")
        print(f"Embedding dimensions: {len(embeddings[0])}")
        assert len(embeddings) == 2
        assert len(embeddings[0]) == 512
        print("Test passed!")
    else:
        print("Failed to generate embeddings")
        sys.exit(1)

if __name__ == "__main__":
    test_jina_embedding()
