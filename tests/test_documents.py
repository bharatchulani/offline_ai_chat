from app.documents import chunk_text


def test_chunk_text_returns_empty_for_whitespace() -> None:
    assert chunk_text("  \n  ", size=20, overlap=5) == []


def test_chunk_text_preserves_content_with_overlap() -> None:
    text = "alpha beta gamma delta epsilon zeta eta theta"
    chunks = chunk_text(text, size=20, overlap=5)

    assert len(chunks) > 1
    assert chunks[0].startswith("alpha")
    assert chunks[-1].endswith("theta")

