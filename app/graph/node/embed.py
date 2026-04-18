import logging

from app.graph.state import IngestState
from app.services.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)
embedding_service = EmbeddingService()


async def embed_node(state: IngestState) -> dict:
    """Generate embedding vector from combined text."""
    combined_text = state.get("combined_text", "")
    if not combined_text:
        return {"embedding": [], "error": "No text to embed"}

    try:
        embedding = await embedding_service.generate_embedding(combined_text[:8000])
        return {"embedding": embedding}
    except Exception as e:
        logger.error(f"Embedding failed: {e}")
        return {"embedding": [], "error": str(e)}
