import logging

from langgraph.graph import END, StateGraph

from app.graph.node.describe import describe_node
from app.graph.node.embed import embed_node
from app.graph.node.extract import extract_node
from app.graph.node.store import store_node
from app.graph.node.transcribe import transcribe_node
from app.graph.state import IngestState
from app.models.fragment import FragmentType

logger = logging.getLogger(__name__)


def should_transcribe(state: IngestState) -> str:
    """Route: transcribe only audio/video fragments."""
    ftype = state.get("fragment_type")
    if ftype in (FragmentType.AUDIO, FragmentType.VIDEO):
        return "transcribe"
    return "skip_transcribe"


def should_describe(state: IngestState) -> str:
    """Route: describe only image/video fragments."""
    ftype = state.get("fragment_type")
    if ftype in (FragmentType.IMAGE, FragmentType.VIDEO, FragmentType.MIXED):
        return "describe"
    return "skip_describe"


def check_error(state: IngestState) -> str:
    if state.get("error"):
        return "error"
    return "continue"


def build_ingest_graph() -> StateGraph:
    """
    Build the LangGraph ingestion pipeline.

    Flow:
        START
          ├── [audio/video] → transcribe → extract → embed → store → END
          ├── [image/mixed] → describe  → extract → embed → store → END
          └── [text]        ─────────────→ extract → embed → store → END
    """
    graph = StateGraph(IngestState)

    # Add nodes
    graph.add_node("transcribe", transcribe_node)
    graph.add_node("describe", describe_node)
    graph.add_node("extract", extract_node)
    graph.add_node("embed", embed_node)
    graph.add_node("store", store_node)

    # Entry: conditional routing based on fragment type
    graph.add_conditional_edges(
        "__start__",
        should_transcribe,
        {
            "transcribe": "transcribe",
            "skip_transcribe": "describe",  # check describe next
        },
    )

    # After transcribe, also check if we need to describe (video has both)
    graph.add_conditional_edges(
        "transcribe",
        should_describe,
        {
            "describe": "describe",
            "skip_describe": "extract",
        },
    )

    # After describe (or skipped), always extract
    graph.add_conditional_edges(
        "describe",
        lambda _: "extract",
        {"extract": "extract"},
    )

    # For text-only that skipped both: the skip_transcribe → describe path
    # handles routing through should_describe, which sends text to skip_describe → extract

    # Linear: extract → embed → store → END
    graph.add_edge("extract", "embed")

    graph.add_conditional_edges(
        "embed",
        check_error,
        {"continue": "store", "error": END},
    )

    graph.add_edge("store", END)

    return graph


# Compiled graph instance — reuse across invocations
ingest_graph = build_ingest_graph().compile()


async def run_ingest(state: IngestState) -> IngestState:
    """Run the full ingestion pipeline for a fragment."""
    logger.info(f"Ingesting fragment {state['fragment_id']} (type={state.get('fragment_type')})")
    result = await ingest_graph.ainvoke(state)
    if result.get("error"):
        logger.error(f"Ingest failed for {state['fragment_id']}: {result['error']}")
    else:
        logger.info(f"Ingest complete for {state['fragment_id']}")
    return result
