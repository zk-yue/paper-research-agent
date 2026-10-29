"""Survey provenance and coverage regressions; no model, network, or task I/O."""

from copy import deepcopy

import pytest

from backend.survey_validation import (
    audit_coverage,
    citation_ids,
    link_citations,
    render_bibliography,
    repair_quote_escaping,
    validate_outline,
    validate_section,
)


def registry(count=6):
    return [
        {
            "citation_id": i,
            "title": f"Robot memory study {i}",
            "url": f"https://example.org/paper/{i}",
            "year": "2026",
            "source_level": "full_text",
            "passages": [
                {
                    "id": "p1-method",
                    "page": 1,
                    "kind": "method",
                    "text": f"Policy {i} retrieves task history to guide subsequent robot actions.",
                }
            ],
        }
        for i in range(1, count + 1)
    ]


def outline(papers):
    return {
        "title": "Task memory and hierarchical robot planning",
        "scope": "Compare mechanisms, evidence, and evaluation limitations.",
        "sections": [
            {
                "id": f"s{i}",
                "title": f"Mechanism {i}",
                "focus": "Explain the mechanism and compare relevant evidence.",
                "reference_ids": [paper["citation_id"]],
                "target_chars": 1500,
            }
            for i, paper in enumerate(papers, 1)
        ],
    }


def evidence(paper):
    return {
        "citation_id": paper["citation_id"],
        "passage_id": paper["passages"][0]["id"],
        "quote": paper["passages"][0]["text"],
        "claim": "The policy uses stored task history for subsequent decisions.",
    }


def test_citations_support_groups_ranges_and_adjacent_citations():
    text = "Grouped [1, 3–5]; ranged [6-8]; adjacent [9][10]; repeated [1]; [11，12—13]."
    assert citation_ids(text) == {1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13}


def test_citations_do_not_count_markdown_links_images_code_or_math():
    text = r"""Real [1]. [2](https://example.org/path_(x)) is a link.
![3](https://example.org/figure.png) is an image.
[text [4]](https://example.org) is a nested link label.
[5][paper] and [6] and [7][] are reference links.
[paper]: https://example.org/paper
[6]: https://example.org/six
[7]: https://example.org/seven
Inline `[8]`, ``a `[9]` b`` and $[10, 11]$ are not citations.
\([12]\) and \[[13]\] are math. <https://example.org/[14]> is a URL.
````python
data = [15]
```
still fenced [16]
````
~~~
[17]
~~~
Escaped \[18] and real [19].
"""
    assert citation_ids(text) == {1, 19}


def test_valid_outline_covers_all_98_without_depending_on_registry_order():
    papers = registry(98)
    plan = outline(papers[:6])
    for paper in papers[6:]:
        plan["sections"][(paper["citation_id"] - 1) % 6]["reference_ids"].append(
            paper["citation_id"]
        )
    before = deepcopy((plan, papers))
    assert validate_outline(plan, list(reversed(papers))) == []
    assert (plan, papers) == before


def test_outline_rejects_silent_first_n_selection_and_unknown_references():
    papers = registry(98)
    plan = outline(papers[:6])
    plan["sections"][0]["reference_ids"].append(999)
    issues = validate_outline(plan, papers)
    assert any("unknown paper [999]" in issue for issue in issues)
    assert any("without a section assignment" in issue and "98" in issue for issue in issues)


def test_outline_rejects_invalid_duplicate_ids_and_wrong_chapter_count():
    papers = registry()
    plan = outline(papers)
    plan["sections"][1]["id"] = "S1"
    plan["sections"][2]["id"] = ""
    assert any("duplicated" in issue for issue in validate_outline(plan, papers))
    assert any("invalid id" in issue for issue in validate_outline(plan, papers))
    plan["sections"] = plan["sections"][:2]
    assert any("6–10 sections" in issue for issue in validate_outline(plan, papers))
    assert validate_outline(outline(registry(2)), registry(2)) == []


def test_section_resolves_ids_not_positions_and_accepts_multiple_papers():
    papers = registry(3)
    section = {
        "content": "## Mechanism comparison\n\nThe methods all retain task history [1–3].",
        "evidence": [evidence(paper) for paper in reversed(papers)],
    }
    before = deepcopy((section, papers))
    assert validate_section(section, [3, 1, 2], list(reversed(papers))) == []
    assert (section, papers) == before


def test_quote_cannot_be_borrowed_from_another_paper_even_with_same_passage_id():
    papers = registry(2)
    wrong = evidence(papers[1])
    wrong["citation_id"] = 1
    section = {"content": "A claim about policy one [1].", "evidence": [wrong]}
    issues = validate_section(section, [1], papers)
    assert any("does not occur in paper [1]" in issue for issue in issues)
    assert any("lack valid same-paper" in issue for issue in issues)


def test_quote_must_come_from_the_named_passage_not_merely_the_same_pdf():
    papers = registry(1)
    papers[0]["passages"].append(
        {"id": "p8-results", "page": 8, "kind": "results", "text": "Success rate was 64 percent."}
    )
    item = evidence(papers[0])
    item["quote"] = "Success rate was 64 percent."
    section = {"content": "The result is 64 percent [1].", "evidence": [item]}
    assert any("does not occur" in issue for issue in validate_section(section, [1], papers))


def test_section_flags_unknown_out_of_scope_and_uncited_assigned_papers():
    papers = registry(3)
    section = {
        "content": "The comparison cites an unknown work [99] and an unassigned work [3].",
        "evidence": [evidence(papers[2])],
    }
    issues = validate_section(section, [1, 2], papers)
    assert any("unknown paper [99]" in issue for issue in issues)
    assert any("outside this section" in issue for issue in issues)
    assert any("does not cite assigned papers: [1, 2]" in issue for issue in issues)
    assert any("unassigned paper [3]" in issue for issue in issues)


def test_section_requires_valid_evidence_for_every_body_citation():
    papers = registry(2)
    section = {
        "content": "Both mechanisms use task memory [1, 2].",
        "evidence": [evidence(papers[0])],
    }
    assert any("evidence: [2]" in issue for issue in validate_section(section, [1, 2], papers))
    section["evidence"][0]["quote"] = "Policy 1"
    assert any("12 characters" in issue for issue in validate_section(section, [1, 2], papers))


def test_quote_normalizes_pdf_whitespace_ligatures_and_line_hyphens_but_not_numbers():
    papers = registry(1)
    papers[0]["passages"][0]["text"] = (
        "Our trans-\nformer encodes vision-\nlanguage memories.\n"
        "The ﬁnal success rate is 64 percent."
    )
    item = evidence(papers[0])
    item["quote"] = (
        "Our transformer encodes vision-language memories. The final success rate is 64 percent."
    )
    section = {"content": "The method achieves 64 percent [1].", "evidence": [item]}
    assert validate_section(section, [1], papers) == []
    item["quote"] = "The final success rate is 64 percent."
    assert validate_section(section, [1], papers) == []
    item["quote"] = "Our transformer encodes visionlanguage memories."
    assert validate_section(section, [1], papers) == []
    item["quote"] = "The final success rate is 94 percent."
    assert any("does not occur" in issue for issue in validate_section(section, [1], papers))

    papers[0]["passages"][0]["text"] = "The policy uses task-dependent control for memory."
    item["quote"] = "The policy uses taskdependent control for memory."
    assert any("does not occur" in issue for issue in validate_section(section, [1], papers))


def test_malformed_large_ranges_cannot_hide_coverage_or_allocate_unbounded_sets():
    papers = registry(1)
    section = {
        "content": "Malformed [3–1] and enormous [1–999999999].",
        "evidence": [evidence(papers[0])],
    }
    issues = validate_section(section, [1], papers)
    assert any("Descending citation range" in issue for issue in issues)
    assert any("too large" in issue for issue in issues)
    assert any("does not cite assigned papers" in issue for issue in issues)
    assert citation_ids(section["content"]) == set()


def test_invalid_registry_and_boolean_ids_are_not_silently_coerced():
    papers = registry(1)
    papers.append({**papers[0], "title": "Duplicate identity"})
    assert any("duplicated" in issue for issue in validate_outline(outline(papers), papers))
    with pytest.raises(ValueError, match="duplicated"):
        render_bibliography(papers)
    assert any(
        "invalid" in issue
        for issue in validate_section({"content": "Claim [1]", "evidence": []}, [True], registry(1))
    )


def test_bibliography_keeps_ids_titles_years_and_safe_links_after_reordering():
    papers = registry(3)
    papers[0]["title"] = "Memory [for] **robots**"
    papers[0]["url"] = "https://example.org/a path(paper)"
    papers[1]["year"] = ""
    papers[1]["published_date"] = "2025-03-10"
    papers[2]["year"] = ""
    papers[2]["url"] = "javascript:alert(1)"
    rendered = render_bibliography(list(reversed(papers)))
    assert rendered.index("**[1]**") < rendered.index("**[2]**") < rendered.index("**[3]**")
    assert r"Memory \[for\] \*\*robots\*\*" in rendered
    assert "https://example.org/a%20path(paper)" in rendered
    assert "(2025)" in rendered
    assert "年份未提供" in rendered
    assert "javascript" not in rendered


def test_link_citations_keeps_all_existing_markdown_code_and_math_unchanged():
    papers = registry(3)
    untouched = r"""[1](https://example.org/existing_(x)) and ![2](figure.png).
[table [1]](https://example.org/table), [[2]](https://example.org/two).
[3][paper] and [paper]:
[paper]: https://example.org/reference
Code `[1]` and ``[2] `[3]` ``; math $[1, 2]$ and \([3]\).
$$
[1–3]
$$
```python
array = [1, 2]
```
Escaped \[1] and <https://example.org/[2]>.
"""
    text = "Real [1, 3] and [2–3].\n" + untouched
    result = link_citations(text, list(reversed(papers)))
    assert result == (
        "Real [[1]](<https://example.org/paper/1>)[[3]](<https://example.org/paper/3>) "
        "and [[2]](<https://example.org/paper/2>)[[3]](<https://example.org/paper/3>).\n"
        + untouched
    )
    assert link_citations(result, papers) == result


def test_link_citations_handles_unsafe_urls_unknown_ids_and_invalid_ranges():
    papers = registry(3)
    papers[0]["url"] = "https://example.org/a path)with]special>characters"
    papers[1]["url"] = "javascript:alert(1)"
    papers[2]["url"] = "https://example.org/\nmalformed"
    text = "Valid [1–3]. Unknown [1, 99]. Invalid [3–1], [1–999999999]."
    result = link_citations(text, papers)
    assert result == (
        "Valid [[1]](<https://example.org/a%20path)with]special%3Echaracters>)[2][3]. "
        "Unknown [1, 99]. Invalid [3–1], [1–999999999]."
    )
    assert "javascript" not in result
    with pytest.raises(ValueError, match="duplicated"):
        link_citations("[1]", [papers[0], papers[0]])


def test_coverage_counts_real_body_citations_and_assignments_only_once():
    papers = registry(4)
    sections = [
        {
            "content": "Mechanisms [1–2]. [3](https://example.org) is a link.",
            "reference_ids": [1, 2],
        },
        {"content": "Comparison [2, 4, 999].", "reference_ids": [2, 4, 999]},
    ]
    assert audit_coverage(sections, list(reversed(papers))) == {
        "total": 4,
        "cited": 3,
        "uncited_ids": [3],
        "assigned": 3,
    }


@pytest.mark.parametrize(
    "heading",
    [
        "## References",
        "### 4. REFERENCES ###",
        "参考文献\n----",
        "**参考文献：**",
        "**Citation Usage:**",
        "**Citation Usage**:",
        "Citation Usage：",
        "## Bibliography",
    ],
)
def test_reference_lists_cannot_satisfy_body_coverage_even_with_valid_quotes(heading):
    papers = registry(2)
    content = "Policy one retrieves historical context [1].\n\n" + heading + "\n- [2] Paper two."
    section = {
        "content": content,
        "reference_ids": [1, 2],
        "evidence": [evidence(paper) for paper in papers],
    }
    assert citation_ids(content) == {1}
    issues = validate_section(section, [1, 2], papers)
    assert any("must not contain a reference list" in issue for issue in issues)
    assert any("does not cite assigned papers: [2]" in issue for issue in issues)
    assert audit_coverage([section], papers) == {
        "total": 2,
        "cited": 1,
        "uncited_ids": [2],
        "assigned": 2,
    }
    linked = link_citations(content, papers)
    assert "[[1]](<https://example.org/paper/1>)" in linked
    assert linked.endswith("- [2] Paper two.")


def test_bibliography_mask_ends_at_peer_heading_and_keeps_real_comparison_tables():
    text = """## Mechanisms
Policy one uses retrieved context [1].
## References
- [2] Paper two.
### Citation Usage
- [3] Paper three.
### Supplemental entries
- [2, 3] Repeated listed works.
## Comparison
| Method | References | Memory update |
| --- | --- | --- |
| Policy four | [4] | Writes temporal state after each action |
| Policy five | [5] | Retrieves stored observations |
"""
    assert citation_ids(text) == {1, 4, 5}
    section = {
        "content": text,
        "reference_ids": list(range(1, 6)),
        "evidence": [evidence(paper) for paper in registry(5)],
    }
    assert audit_coverage([section], registry(5))["uncited_ids"] == [2, 3]


def test_normal_prose_bullets_tables_and_code_examples_are_not_reference_lists():
    papers = registry(3)
    text = """### References to historical observations improve planning
References are retrieved from stored trajectories rather than documents [1].
- Policy two performs an explicit memory update after each action [2].
| Method | References | Interface |
| --- | --- | --- |
| Policy three | [3] | Persistent state enters the planner |
The prompt's forbidden appendix can be illustrated as:
```markdown
## References
[99] An example, not an actual cited work.
```
"""
    section = {"content": text, "evidence": [evidence(paper) for paper in papers]}
    assert citation_ids(text) == {1, 2, 3}
    assert validate_section(section, [1, 2, 3], papers) == []


def test_all_98_usage_checklist_entries_cannot_fake_complete_review_coverage():
    papers = registry(98)
    section = {
        "content": "Only policy one has been discussed [1].\n\n**Citation Usage:**\n"
        + "\n".join(f"- [{i}]: used" for i in range(1, 99)),
        "reference_ids": list(range(1, 99)),
        "evidence": [evidence(paper) for paper in papers],
    }
    coverage = audit_coverage([section], papers)
    assert coverage["cited"] == 1 and coverage["assigned"] == 98
    assert coverage["uncited_ids"] == list(range(2, 99))
    assert any(
        "does not cite assigned papers" in issue and "98" in issue
        for issue in validate_section(section, section["reference_ids"], papers)
    )


@pytest.mark.parametrize("slash_count", [2, 4])
def test_quote_escape_repair_is_exact_scoped_auditable_and_nonmutating(slash_count):
    papers = registry(1)
    text = r"Planner speed improves by $8.7\times$ under the matched protocol."
    papers[0]["passages"][0]["text"] = "Before. " + text + " After."
    original = text.replace("\\times", "\\" * slash_count + "times")
    item = {**evidence(papers[0]), "quote": original}
    output = {"content": "The planner speed improves by 8.7 times [1].", "evidence": [item]}
    before = deepcopy((output, papers))
    assert validate_section(output, [1], papers)
    result = repair_quote_escaping(output, papers)
    assert (output, papers) == before
    assert result["evidence"][0]["quote"] == text
    assert result["evidence"][0]["original_quote"] == original
    info = result["evidence"][0]["quote_normalization"]
    assert info["kind"] == "duplicate_latex_command_backslashes"
    assert info["citation_id"] == 1 and info["passage_id"] == "p1-method"
    assert papers[0]["passages"][0]["text"][info["source_start"] : info["source_end"]] == text
    assert validate_section(result, [1], papers) == []
    assert repair_quote_escaping(result, papers) == result


def test_quote_escape_repair_rejects_numeric_changes_wrong_paper_and_ambiguous_spans():
    papers = registry(2)
    text = r"The measured improvement is $8.7\times$ on the matched task."
    papers[0]["passages"][0]["text"] = text
    quote = text.replace("\\times", "\\\\times")
    output = {"content": "A result [1].", "evidence": [{**evidence(papers[0]), "quote": quote}]}
    changed = deepcopy(output)
    changed["evidence"][0]["quote"] = quote.replace("8.7", "9.7")
    assert repair_quote_escaping(changed, papers) == changed
    wrong_paper = deepcopy(output)
    wrong_paper["evidence"][0]["citation_id"] = 2
    assert repair_quote_escaping(wrong_paper, papers) == wrong_paper
    wrong_passage = deepcopy(output)
    wrong_passage["evidence"][0]["passage_id"] = "nonexistent"
    assert repair_quote_escaping(wrong_passage, papers) == wrong_passage
    papers[0]["passages"][0]["text"] = text + "\n" + text
    assert repair_quote_escaping(output, papers) == output


def test_quote_escape_repair_preserves_noncommand_backslashes_and_source_whitespace():
    papers = registry(1)
    text = "The planner uses $\\bm{p}$ and\n\t$\\tau$ for continuous control."
    papers[0]["passages"][0]["text"] = text
    quote = "The planner uses $\\\\bm{p}$ and $\\\\\\\\tau$ for continuous control."
    output = {
        "content": "A planner mechanism [1].",
        "evidence": [{**evidence(papers[0]), "quote": quote}],
    }
    result = repair_quote_escaping(output, papers)
    assert result["evidence"][0]["quote"] == text
    assert validate_section(result, [1], papers) == []
    papers[0]["passages"][0]["text"] = r"Matrix rows use \\ before their next entry."
    original = deepcopy(output)
    original["evidence"][0]["quote"] = r"Matrix rows use \\\\ before their next entry."
    assert repair_quote_escaping(original, papers) == original
    papers[0]["passages"][0]["text"] = r"The data directory is C:\folder\samples."
    original["evidence"][0]["quote"] = r"The data directory is C:\\folder\\samples."
    assert repair_quote_escaping(original, papers) == original


@pytest.mark.parametrize("source_count,quote_count", [(1, 2), (2, 4), (1, 4)])
def test_percent_escape_repair_retains_exact_source_backslashes(source_count, quote_count):
    papers = registry(1)
    template = (
        "It also achieves $98.1{percent}$ success on LIBERO and raises Fold Cloth success "
        "on a real Piper arm from $20.0{percent}$ to $60.0{percent}$ relative to $π_0$."
    )
    source_quote = template.format(percent="\\" * source_count + "%")
    original = template.format(percent="\\" * quote_count + "%")
    source = "Before. " + source_quote + " After."
    papers[0]["passages"][0]["text"] = source
    output = {
        "content": "The author reports these results [1].",
        "evidence": [{**evidence(papers[0]), "quote": original}],
    }
    before = deepcopy((output, papers))
    assert validate_section(output, [1], papers)
    result = repair_quote_escaping(output, papers)
    assert (output, papers) == before
    item = result["evidence"][0]
    assert item["quote"] == source_quote
    assert item["original_quote"] == original
    info = item["quote_normalization"]
    assert info["operations"] == ["duplicate_latex_percent_backslashes"]
    assert info["citation_id"] == 1 and info["passage_id"] == "p1-method"
    assert source[info["source_start"] : info["source_end"]] == source_quote
    assert validate_section(result, [1], papers) == []
    assert repair_quote_escaping(result, papers) == result


def test_percent_escape_repair_refuses_ambiguous_counts_or_spans_and_wrong_identity():
    papers = registry(2)
    source = r"The reported success rate is $60.0\%$ on this task."
    original = source.replace("\\%", "\\" * 4 + "%")
    output = {
        "content": "A reported result [1].",
        "evidence": [{**evidence(papers[0]), "quote": original}],
    }
    for second in (source, source.replace("\\%", "\\" * 2 + "%")):
        papers[0]["passages"][0]["text"] = source + "\n" + second
        assert repair_quote_escaping(output, papers) == output
    papers[0]["passages"][0]["text"] = source
    for key, value in (("citation_id", 2), ("passage_id", "other-passage")):
        wrong = deepcopy(output)
        wrong["evidence"][0][key] = value
        assert repair_quote_escaping(wrong, papers) == wrong


@pytest.mark.parametrize(
    "source,original",
    [
        (r"Success is 60.0\% outside math.", r"Success is 60.0\\% outside math."),
        (r"Success is $60.0\%$.", r"Success is $90.0\\%$."),
        ("Success is $60.0%$.", r"Success is $60.0\\%$."),
        (r"Success is $60.0\%.", r"Success is $60.0\\%."),
        (r"Symbols are $a\&b$.", r"Symbols are $a\\&b$."),
        (r"Success is $60.0\%$.", r"Success is $60.0\\\%$."),
    ],
)
def test_percent_escape_repair_does_not_broaden_text_or_numeric_matching(source, original):
    papers = registry(1)
    papers[0]["passages"][0]["text"] = source
    output = {
        "content": "A reported result [1].",
        "evidence": [{**evidence(papers[0]), "quote": original}],
    }
    assert repair_quote_escaping(output, papers) == output
    assert validate_section(output, [1], papers)


@pytest.mark.parametrize("escaped", [r"\n", r"\r\n", r"\\n", r"\\r\\n"])
def test_quote_repair_decodes_only_source_confirmed_json_newline_escapes(escaped):
    papers = registry(1)
    source = "Planning uses actual\nvisual evidence with $\\nabla$ and $\\nu$."
    papers[0]["passages"][0]["text"] = source
    quote = source.replace("\n", escaped)
    output = {
        "content": "Planning uses visual evidence [1].",
        "evidence": [{**evidence(papers[0]), "quote": quote}],
    }
    result = repair_quote_escaping(output, papers)
    assert result["evidence"][0]["quote"] == source
    assert result["evidence"][0]["original_quote"] == quote
    assert result["evidence"][0]["quote_normalization"]["operations"] == ["json_newline_escapes"]
    assert validate_section(result, [1], papers) == []


def test_newline_repair_never_decodes_latex_commands_unicode_or_changed_facts():
    papers = registry(2)
    source = "Use \\nabla and \\nu outside math; accuracy\nis 64 percent."
    papers[0]["passages"][0]["text"] = source
    quote = source.replace("\n", r"\n")
    output = {
        "content": "Accuracy is 64 percent [1].",
        "evidence": [{**evidence(papers[0]), "quote": quote}],
    }
    result = repair_quote_escaping(output, papers)
    assert result["evidence"][0]["quote"] == source
    assert "\\nabla" in result["evidence"][0]["quote"] and "\\nu" in result["evidence"][0]["quote"]
    wrong = deepcopy(output)
    wrong["evidence"][0]["quote"] = quote.replace("64", "94")
    assert repair_quote_escaping(wrong, papers) == wrong
    wrong["evidence"][0].update(quote=quote, citation_id=2)
    assert repair_quote_escaping(wrong, papers) == wrong
    unicode_quote = deepcopy(output)
    unicode_quote["evidence"][0]["quote"] = quote.replace("64", r"\u0036\u0034")
    assert repair_quote_escaping(unicode_quote, papers) == unicode_quote
    papers[0]["passages"][0]["text"] = "Use\nabla and\nu as ordinary letters in this source."
    commands = deepcopy(output)
    commands["evidence"][0]["quote"] = r"Use\nabla and\nu as ordinary letters in this source."
    assert repair_quote_escaping(commands, papers) == commands
