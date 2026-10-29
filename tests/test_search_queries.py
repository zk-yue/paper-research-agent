import pytest

from tools import build_arxiv_keyword_query, build_arxiv_multi_query


@pytest.mark.parametrize(
    "query,expected",
    [
        (
            '"vision-language-action" "task decomposition"',
            '"vision-language-action" AND "task decomposition"',
        ),
        ('"world action model" "planning"', '"world action model" AND "planning"'),
        ("“vision-language model” memory", '"vision-language model" AND (memory OR memory*)'),
        ("robot manipulation", "(robot OR robot*) AND (manipulation OR manipulation*)"),
        ("tactile*", "tactile*"),
        ('"  task   planning  "', '"task planning"'),
        ('"task planning', "(task OR task*) AND (planning OR planning*)"),
        ("  ", ""),
    ],
)
def test_query_preserves_phrases_and_existing_keyword_expansion(query, expected):
    assert build_arxiv_keyword_query(query) == expected


def test_multi_query_preserves_phrase_boundaries():
    query = build_arxiv_multi_query(['"vision-language-action" "memory"', "robot planning"])
    assert (
        query
        == '("vision-language-action" AND "memory") OR ((robot OR robot*) AND (planning OR planning*))'
    )
