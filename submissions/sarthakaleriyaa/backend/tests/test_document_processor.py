
from app.services.document_processor import chunk_text, extract_text


def test_extract_text_file(tmp_path):
    path = tmp_path / "sample.txt"
    path.write_text("DocuMind retrieves useful document passages.", encoding="utf-8")

    assert extract_text(str(path)) == "DocuMind retrieves useful document passages."


def test_chunk_text_preserves_all_content():
    text = "A" * 2500
    chunks = chunk_text(text, chunk_size=1000, overlap=150)

    assert len(chunks) == 3
    assert all(chunks)
    assert chunks[0][-150:] == chunks[1][:150]


def test_chunk_text_rejects_invalid_overlap():
    import pytest

    with pytest.raises(ValueError):
        chunk_text("example", chunk_size=100, overlap=100)
