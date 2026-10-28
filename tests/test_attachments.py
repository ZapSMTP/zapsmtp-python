from __future__ import annotations

from pathlib import Path

from zapsmtp import attachment, attachment_from_file


def test_encodes_bytes_and_text_and_guesses_the_content_type() -> None:
    assert attachment("a.pdf", b"\x01\x02") == {"filename": "a.pdf", "content": "AQI=", "content_type": "application/pdf"}
    assert attachment("notes", "hi", content_type="text/plain", content_id="n1") == {
        "filename": "notes", "content": "aGk=", "content_type": "text/plain", "content_id": "n1"}
    assert "content_type" not in attachment("x.unknown", bytearray(1))
    assert "content_type" not in attachment("x.pdf", memoryview(b"x"), content_type="")
    assert attachment("Report.DOCX", b"")["content_type"].endswith("wordprocessingml.document")


def test_reads_a_file(tmp_path: Path) -> None:
    path = tmp_path / "Report.PNG"
    path.write_bytes(b"\x89P")
    assert attachment_from_file(path) == {"filename": "Report.PNG", "content": "iVA=", "content_type": "image/png"}
    assert attachment_from_file(str(path), filename="r.png")["filename"] == "r.png"
    assert attachment_from_file(path, content_id="logo")["content_id"] == "logo"
