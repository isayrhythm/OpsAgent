from io import BytesIO
import json

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
