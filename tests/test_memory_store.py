from io import BytesIO
import json

from backend.app.schemas import UploadedFileSummary
from backend.app.memory.store import MemoryPaths, MemoryStore


def test_memory_store_appends_and_loads_history(tmp_path) -> None:
    store = MemoryStore(MemoryPaths(root=tmp_path))

    store.append_exchange("user-a", "session-a", "hello", "world")
    history = store.load_history("user-a", "session-a")

    assert [(item.role, item.content) for item in history] == [
        ("user", "hello"),
        ("assistant", "world"),
    ]


def test_memory_store_saves_upload_metadata_and_file(tmp_path) -> None:
    store = MemoryStore(MemoryPaths(root=tmp_path))

    summary = store.save_upload(
        "user-a",
        "session-a",
        "../unsafe.csv",
        "text/csv",
        BytesIO(b"a,b\n1,2\n"),
    )

    assert summary.filename == "unsafe.csv"
    assert summary.content_type == "text/csv"
    assert summary.size == 8
    assert summary.path is not None
    assert "user-a" in summary.path
    assert "session-a" in summary.path


def test_memory_store_keeps_uploaded_file_summary_in_conversation(tmp_path) -> None:
    store = MemoryStore(MemoryPaths(root=tmp_path))
    summary = store.save_upload(
        "user-a",
        "session-a",
        "proteomics.csv",
        "text/csv",
        BytesIO(b"a,b\n1,2\n"),
    ).model_copy(
        update={
            "intake": {
                "status": "ready",
                "data_family": "proteomics",
                "data_type": "expression_matrix",
            }
        }
    )

    store.append_exchange("user-a", "session-a", "analyze it", "completed", [summary])
    conversation = json.loads(store.paths.conversation_path("user-a", "session-a").read_text(encoding="utf-8"))

    assert conversation["uploaded_files"][0]["filename"] == "proteomics.csv"
    assert conversation["uploaded_files"][0]["intake"]["status"] == "ready"


def test_memory_store_persists_pdf_context_in_hidden_history(tmp_path) -> None:
    store = MemoryStore(MemoryPaths(root=tmp_path))
    summary = UploadedFileSummary(
        file_id="paper",
        filename="paper.pdf",
        content_type="application/pdf",
        size=123,
        path=str(tmp_path / "paper.pdf"),
        intake={
            "status": "ready",
            "data_family": "literature",
            "data_type": "pdf_document",
            "title": "HY2 paper",
            "page_count": 1,
            "parsed_pages": 1,
            "text_file": str(tmp_path / "paper_text.txt"),
            "text_excerpt": "HY2 regulates photomorphogenesis.",
        },
    )

    store.append_exchange("user-a", "session-a", "总结这篇 PDF", "好的", [summary])
    history = store.load_history("user-a", "session-a")

    assert history[0].role == "user"
    assert "总结这篇 PDF" in history[0].content
    assert "PDF 文献上下文" in history[0].content
    assert "HY2 regulates photomorphogenesis" in history[0].content


def test_memory_store_loads_tool_trace_from_hidden_assistant_context(tmp_path) -> None:
    store = MemoryStore(MemoryPaths(root=tmp_path))

    store.append_exchange(
        "user-a",
        "session-a",
        "查这个基因",
        "查到了。",
        tool_trace_context="上一轮工具调用摘要：\n- query_gene_info [skill]: completed；LOC_Os07g48050",
    )
    conversation = json.loads(store.paths.conversation_path("user-a", "session-a").read_text(encoding="utf-8"))
    history = store.load_history("user-a", "session-a")

    assert conversation["messages"][1]["content"] == "查到了。"
    assert "query_gene_info" in conversation["messages"][1]["context_content"]
    assert "LOC_Os07g48050" in history[1].content
