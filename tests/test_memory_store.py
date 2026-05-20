from io import BytesIO

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
