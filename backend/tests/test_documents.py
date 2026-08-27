from app.services.documents import chunk_document, document_checksum, extract_document_text


def test_extract_html_and_chunk_with_overlap() -> None:
    content = b"<html><body><h1>Title</h1><p>Evidence paragraph.</p></body></html>"
    text = extract_document_text(
        content,
        filename="archive.html",
        media_type="text/html",
    )
    chunks = chunk_document(text, chunk_size=20, overlap=5)

    assert "Title" in text
    assert "Evidence paragraph." in text
    assert len(chunks) >= 2
    assert len(document_checksum(content)) == 64
