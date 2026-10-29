import json

import pytest

import paper_config


def test_fresh_library_works_without_bundled_personal_config(tmp_path, monkeypatch):
    target = tmp_path / "config" / "papers_keywords.json"
    monkeypatch.setattr(paper_config, "CONFIG_PATH", target)
    first = paper_config.load_config()
    first["keywords"].append("sample keyword")
    assert paper_config.load_config()["keywords"] == []
    assert not target.exists()
    paper_config.save_config(first)
    assert paper_config.load_config() == first


def test_existing_library_is_preserved_and_utilities_share_it(tmp_path, monkeypatch):
    import extract_papers
    import manage_papers

    target = tmp_path / "config" / "papers_keywords.json"
    monkeypatch.setattr(paper_config, "CONFIG_PATH", target)
    config = {"papers": [{"title": "Example paper", "keywords": []}], "keywords": ["existing"]}
    paper_config.save_config(config)
    manage_papers.add_keywords(["new"])
    result = extract_papers.load_config()
    assert result["papers"] == config["papers"]
    assert result["keywords"] == ["existing", "new"]


@pytest.mark.parametrize("raw", ["{", json.dumps([]), json.dumps({"papers": {}, "keywords": []})])
def test_invalid_library_is_reported_without_resetting_it(tmp_path, raw):
    target = tmp_path / "library.json"
    target.write_text(raw)
    with pytest.raises(ValueError, match="configuration"):
        paper_config.load_config(target)
    assert target.read_text() == raw
