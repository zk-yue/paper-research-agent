from unittest.mock import MagicMock, patch

import requests

from backend.download_agent import download_single_pdf


def response_with(chunks):
    response = MagicMock()
    response.__enter__.return_value = response
    response.iter_content.return_value = iter(chunks)
    return response


def test_download_commits_complete_pdf(tmp_path):
    destination = tmp_path / "paper.pdf"
    with patch(
        "backend.download_agent.requests.get",
        return_value=response_with([b"%PD", b"F-1.7\ncontent"]),
    ):
        assert download_single_pdf("https://example.test/paper", str(destination))
    assert destination.read_bytes() == b"%PDF-1.7\ncontent"
    assert list(tmp_path.glob("*.part")) == []


def test_partial_download_does_not_replace_previous_pdf(tmp_path):
    destination = tmp_path / "paper.pdf"
    destination.write_bytes(b"%PDF-existing")

    def interrupted():
        yield b"%PDF-new"
        raise requests.ConnectionError("connection lost")

    response = response_with([])
    response.iter_content.return_value = interrupted()
    with patch("backend.download_agent.requests.get", return_value=response):
        assert not download_single_pdf("https://example.test/paper", str(destination))
    assert destination.read_bytes() == b"%PDF-existing"
    assert list(tmp_path.glob("*.part")) == []


def test_html_error_page_is_not_saved_as_pdf(tmp_path):
    destination = tmp_path / "paper.pdf"
    with patch(
        "backend.download_agent.requests.get", return_value=response_with([b"<html>denied</html>"])
    ):
        assert not download_single_pdf("https://example.test/paper", str(destination))
    assert not destination.exists()
