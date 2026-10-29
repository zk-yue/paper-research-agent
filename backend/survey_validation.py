"""Pure structural checks for survey plans, citations, and quoted source evidence.

These checks establish identity, coverage, and verbatim provenance. They do not
prove that a quotation entails a claim or that a paper's scientific result is true.
Citation identities always come from ``citation_id``, never registry list order.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection
from copy import deepcopy
from urllib.parse import quote, urlsplit

_NUMBER = r"[0-9]{1,9}"
_ITEM = rf"{_NUMBER}(?:\s*[-–—]\s*{_NUMBER})?"
_CITATION_LABEL = re.compile(rf"{_ITEM}(?:\s*[,，;；]\s*{_ITEM})*\Z")
_BRACKETS = re.compile(r"(?<!\\)\[([^\[\]\n]+)\]")
_MAX_RANGE_SIZE = 10_000
_REFERENCE_HEADINGS = {
    "references",
    "bibliography",
    "reference list",
    "citation usage",
    "citation usage checklist",
    "citation checklist",
    "参考文献",
    "参考文献列表",
    "引用文献",
    "引用清单",
    "引用使用情况",
    "文献引用清单",
    "文献覆盖清单",
}


def _blank(value: str) -> str:
    return "".join("\n" if c == "\n" else " " for c in value)


def _closing(text: str, start: int, opening: str, closing: str) -> int | None:
    """Locate a balanced delimiter, honoring Markdown backslash escapes."""
    depth = 0
    cursor = start
    while cursor < len(text):
        char = text[cursor]
        if char == "\\":
            cursor += 2
            continue
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return cursor
        cursor += 1
    return None


def _label(value: str) -> str:
    return " ".join(value.split()).casefold()


def _reference_sections(text: str) -> tuple[str, list[str]]:
    """Mask bibliography/checklist sections, stopping at a peer body heading.

    Input already has code, math, and links masked. Recognize real Markdown
    headings plus common standalone/bold list labels, not headings merely
    containing the word "references" or ordinary comparison-table columns.
    """
    lines = text.splitlines(keepends=True)
    masked = []
    found = []
    excluded_level = None
    for position, line in enumerate(lines):
        heading = re.match(r"^ {0,3}(#{1,6})(?:[ \t]+|$)(.*)", line)
        level = len(heading[1]) if heading else None
        label = heading[2] if heading else line.strip()
        if heading:
            label = re.sub(r"[ \t]+#+[ \t]*$", "", label)
        elif label and position + 1 < len(lines):
            underline = re.fullmatch(r" {0,3}(=+|-+)[ \t]*\n?", lines[position + 1])
            if underline:
                level = 1 if underline[1][0] == "=" else 2
        label = label.strip().strip("*_ ").rstrip(":： ").strip("*_ ")
        label = re.sub(r"^(?:\d+(?:\.\d+)*[.)、:]?|[一二三四五六七八九十]+[、.])\s*", "", label)
        label = _label(label)
        is_reference_heading = label in _REFERENCE_HEADINGS
        if excluded_level is not None and level is not None and level <= excluded_level:
            excluded_level = None
        if is_reference_heading:
            reference_level = level if level is not None else 7
            excluded_level = (
                min(excluded_level, reference_level)
                if excluded_level is not None
                else reference_level
            )
            found.append(label)
        masked.append(_blank(line) if excluded_level is not None else line)
    return "".join(masked), found


def _prose_only(text: str, *, exclude_reference_lists: bool = True) -> str:
    """Mask non-body syntax and reference lists before citation parsing."""
    lines = text.splitlines(keepends=True)
    fence = None
    masked = []
    for line in lines:
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence:
            masked.append(_blank(line))
            if (
                marker
                and marker[1][0] == fence[0]
                and len(marker[1]) >= len(fence)
                and not line[marker.end() :].strip()
            ):
                fence = None
        elif marker:
            fence = marker[1]
            masked.append(_blank(line))
        else:
            masked.append(line)
    text = "".join(masked)
    # Backtick spans can cross lines; matching delimiter lengths are required.
    text = re.sub(r"(`+)(?!`)([\s\S]*?)(?<!`)\1(?!`)", lambda m: _blank(m[0]), text)
    text = re.sub(r"(?<!\\)\$\$[\s\S]*?\$\$", lambda m: _blank(m[0]), text)
    text = re.sub(r"(?<![\\$])\$(?!\$)[^\n$]+(?<!\\)\$", lambda m: _blank(m[0]), text)
    text = re.sub(r"\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]", lambda m: _blank(m[0]), text)
    text = re.sub(r"<(?:https?://|mailto:)[^>]+>", lambda m: _blank(m[0]), text, flags=re.I)

    definitions = set()

    def definition(match: re.Match) -> str:
        definitions.add(_label(match[1]))
        return _blank(match[0])

    text = re.sub(r"^ {0,3}\[([^\]\n]+)\]:[^\n]*", definition, text, flags=re.M)
    spans = []
    cursor = 0
    while cursor < len(text):
        if text[cursor] != "[" or (cursor and text[cursor - 1] == "\\"):
            cursor += 1
            continue
        end = _closing(text, cursor, "[", "]")
        if end is None:
            cursor += 1
            continue
        label = _label(text[cursor + 1 : end])
        final = None
        if end + 1 < len(text) and text[end + 1] == "(":
            final = _closing(text, end + 1, "(", ")")
        elif end + 1 < len(text) and text[end + 1] == "[":
            ref_end = _closing(text, end + 1, "[", "]")
            if ref_end is not None:
                ref_label = _label(text[end + 2 : ref_end]) or label
                if ref_label in definitions:
                    final = ref_end
        if final is None and label in definitions:
            final = end
        if final is not None:
            start = cursor - 1 if cursor and text[cursor - 1] == "!" else cursor
            spans.append((start, final + 1))
            cursor = final + 1
        else:
            cursor += 1
    for start, end in reversed(spans):
        text = text[:start] + _blank(text[start:end]) + text[end:]
    if exclude_reference_lists:
        text, _ = _reference_sections(text)
    return text


def _citations(text: str) -> tuple[set[int], list[str]]:
    ids: set[int] = set()
    issues = []
    for match in _BRACKETS.finditer(_prose_only(text)):
        label = match[1].strip()
        if not _CITATION_LABEL.fullmatch(label):
            continue
        for item in re.split(r"[,，;；]", label):
            endpoints = re.split(r"[-–—]", item.strip())
            start = int(endpoints[0])
            if len(endpoints) == 1:
                ids.add(start)
                continue
            end = int(endpoints[1])
            if end < start:
                issues.append(f"Descending citation range [{item.strip()}] is invalid.")
            elif end - start + 1 > _MAX_RANGE_SIZE:
                issues.append(f"Citation range [{item.strip()}] is too large.")
            else:
                ids.update(range(start, end + 1))
    return ids, issues


def citation_ids(text: str) -> set[int]:
    """Return prose citation IDs, including comma groups and ascending ranges.

    Markdown links, code, math, bibliographies, and usage lists are not citations.
    Invalid descending or excessively large ranges are ignored here and reported
    by ``validate_section``. This bounds processing of malformed model output.
    """
    return _citations(text if isinstance(text, str) else "")[0]


def _is_id(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _registry_index(registry: list[dict]) -> tuple[dict[int, dict], list[str]]:
    index = {}
    issues = []
    if not isinstance(registry, list):
        return {}, ["Registry must be a list."]
    for position, paper in enumerate(registry):
        if not isinstance(paper, dict) or not _is_id(paper.get("citation_id")):
            issues.append(f"Registry entry {position} has an invalid citation_id.")
            continue
        pid = paper["citation_id"]
        if pid in index:
            issues.append(f"Registry citation_id [{pid}] is duplicated.")
            continue
        index[pid] = paper
    if not index:
        issues.append("Registry contains no valid papers.")
    return index, issues


def validate_outline(plan: dict, registry: list[dict]) -> list[str]:
    """Validate chapter structure and complete assignment of the source corpus.

    A full corpus needs 6–10 chapters. With fewer than six papers, the minimum
    chapter count is reduced to the number of papers (at least one). A paper may
    appear in multiple chapters, but cannot silently disappear from the outline.
    """
    papers, issues = _registry_index(registry)
    if not isinstance(plan, dict):
        return [*issues, "Outline must be a JSON object."]
    for field in ("title", "scope"):
        if not isinstance(plan.get(field), str) or not plan[field].strip():
            issues.append(f"Outline {field} must be a nonempty string.")
    sections = plan.get("sections")
    if not isinstance(sections, list):
        return [*issues, "Outline sections must be a list."]
    minimum = min(6, max(1, len(papers)))
    if not minimum <= len(sections) <= 10:
        issues.append(f"Outline needs {minimum}–10 sections; received {len(sections)}.")
    section_ids = set()
    assigned = set()
    for position, section in enumerate(sections, 1):
        if not isinstance(section, dict):
            issues.append(f"Section {position} must be an object.")
            continue
        sid = section.get("id")
        if not isinstance(sid, str) or not sid.strip():
            issues.append(f"Section {position} has an invalid id.")
        elif sid.strip().casefold() in section_ids:
            issues.append(f"Section id {sid!r} is duplicated.")
        else:
            section_ids.add(sid.strip().casefold())
        for field in ("title", "focus"):
            if not isinstance(section.get(field), str) or not section[field].strip():
                issues.append(f"Section {position} {field} must be a nonempty string.")
        target = section.get("target_chars")
        if not isinstance(target, int) or isinstance(target, bool) or target <= 0:
            issues.append(f"Section {position} target_chars must be a positive integer.")
        references = section.get("reference_ids")
        if not isinstance(references, list) or not references:
            issues.append(f"Section {position} reference_ids must be a nonempty list.")
            continue
        seen = set()
        for pid in references:
            if not _is_id(pid):
                issues.append(f"Section {position} has invalid reference id {pid!r}.")
            elif pid not in papers:
                issues.append(f"Section {position} references unknown paper [{pid}].")
            elif pid in seen:
                issues.append(f"Section {position} repeats reference [{pid}].")
            else:
                seen.add(pid)
                assigned.add(pid)
    missing = sorted(set(papers) - assigned)
    if missing:
        issues.append(f"Outline leaves papers without a section assignment: {missing}.")
    return issues


def _quote_forms(text: str) -> set[str]:
    text = unicodedata.normalize("NFKC", text).replace("\u00ad", "")
    # PDF text may split either a single word or a compound across a line.
    joined = re.sub(r"(?<=\w)-[ \t]*\r?\n[ \t]*(?=\w)", "", text)
    hyphenated = re.sub(r"(?<=\w)-[ \t]*\r?\n[ \t]*(?=\w)", "-", text)
    return {" ".join(value.split()) for value in (text, joined, hyphenated)}


def _quote_occurs(quoted: str, source: str) -> bool:
    source_forms = _quote_forms(source)
    quoted_forms = _quote_forms(quoted)
    if any(q and q in s for q in quoted_forms for s in source_forms):
        return True
    # A passage can mix split words (trans-\nformer) and compounds
    # (vision-\nlanguage). Only actual line-break hyphens are optional;
    # deleting every ordinary hyphen would change the quoted wording.
    marker = "\ufff0"
    normalized = unicodedata.normalize("NFKC", source).replace("\u00ad", "")
    normalized = re.sub(r"(?<=\w)-[ \t]*\r?\n[ \t]*(?=\w)", marker, normalized)
    normalized = " ".join(normalized.split())
    if marker not in normalized:
        return False
    for value in quoted_forms:
        parts = []
        for index, char in enumerate(value):
            if index and value[index - 1].isalnum() and char.isalnum():
                parts.append(marker + "?")
            parts.append(f"(?:-|{marker})" if char == "-" else re.escape(char))
        if value and re.search("".join(parts), normalized):
            return True
    return False


def validate_section(
    section: dict, allowed_ids: Collection[int], registry: list[dict]
) -> list[str]:
    """Check assigned citation coverage and same-paper verbatim evidence identity."""
    papers, issues = _registry_index(registry)
    allowed = set()
    for pid in allowed_ids:
        if not _is_id(pid):
            issues.append(f"Assigned reference id {pid!r} is invalid.")
        elif pid not in papers:
            issues.append(f"Assigned reference [{pid}] is absent from the registry.")
        else:
            allowed.add(pid)
    if not isinstance(section, dict):
        return [*issues, "Section output must be a JSON object."]
    content = section.get("content")
    if not isinstance(content, str) or not content.strip():
        return [*issues, "Section content must be nonempty Markdown."]
    _, reference_lists = _reference_sections(_prose_only(content, exclude_reference_lists=False))
    if reference_lists:
        issues.append(
            "Section must not contain a reference list or Citation Usage checklist; "
            "cite assigned papers within substantive prose or comparison tables."
        )
    cited, parsing_issues = _citations(content)
    issues.extend(parsing_issues)
    for pid in sorted(cited - set(papers)):
        issues.append(f"Body cites unknown paper [{pid}].")
    for pid in sorted((cited & set(papers)) - allowed):
        issues.append(f"Body cites paper [{pid}] outside this section's assigned references.")
    missing = sorted(allowed - cited)
    if missing:
        issues.append(f"Body does not cite assigned papers: {missing}.")
    evidence = section.get("evidence")
    if not isinstance(evidence, list):
        evidence = []
        issues.append("Section evidence must be a list.")
    verified = set()
    for position, item in enumerate(evidence, 1):
        if not isinstance(item, dict):
            issues.append(f"Evidence {position} must be an object.")
            continue
        pid = item.get("citation_id")
        if not _is_id(pid) or pid not in papers:
            issues.append(f"Evidence {position} has unknown or invalid citation_id {pid!r}.")
            continue
        if pid not in allowed:
            issues.append(f"Evidence {position} cites unassigned paper [{pid}].")
            continue
        if not isinstance(item.get("claim"), str) or not item["claim"].strip():
            issues.append(f"Evidence {position} for [{pid}] has no claim.")
            continue
        passage_id = item.get("passage_id")
        passages = papers[pid].get("passages")
        passages = passages if isinstance(passages, list) else []
        matching = [p for p in passages if isinstance(p, dict) and p.get("id") == passage_id]
        if not isinstance(passage_id, str) or not passage_id or len(matching) != 1:
            issues.append(
                f"Evidence {position} passage_id {passage_id!r} is not a unique passage of [{pid}]."
            )
            continue
        quoted = item.get("quote")
        if not isinstance(quoted, str) or len(" ".join(quoted.split())) < 12:
            issues.append(
                f"Evidence {position} quote for [{pid}] must have at least 12 characters."
            )
            continue
        source = matching[0].get("text")
        if not isinstance(source, str) or not _quote_occurs(quoted, source):
            issues.append(
                f"Evidence {position} quote does not occur in paper [{pid}] passage {passage_id!r}."
            )
            continue
        verified.add(pid)
    unsupported = sorted(cited - verified)
    if unsupported:
        issues.append(f"Body citations lack valid same-paper quoted evidence: {unsupported}.")
    return issues


def repair_quote_escaping(output: dict, registry: list[dict]) -> dict:
    """Repair formatting escapes only against a unique, same-passage source span.

    This does not relax validation. The caller must still run ``validate_section``
    on the returned copy. A repair needs the explicitly named paper and passage,
    and the repaired quote must match one contiguous span of that passage. Only
    duplicate command/percent backslashes inside explicit math spans and escaped
    JSON newlines can differ; text, numbers, and citation identity cannot. No
    general escape decoder is used, and LaTeX commands such as ``nabla`` and
    ``nu`` are preserved.
    """
    repaired = deepcopy(output)
    papers, issues = _registry_index(registry)
    if issues or not isinstance(repaired, dict) or not isinstance(repaired.get("evidence"), list):
        return repaired
    for entry in repaired["evidence"]:
        if not isinstance(entry, dict):
            continue
        pid = entry.get("citation_id")
        original = entry.get("quote")
        if not _is_id(pid) or pid not in papers or not isinstance(original, str):
            continue
        passages = papers[pid].get("passages")
        if not isinstance(passages, list):
            continue
        matching = [
            passage
            for passage in passages
            if isinstance(passage, dict) and passage.get("id") == entry.get("passage_id")
        ]
        if len(matching) != 1 or not isinstance(matching[0].get("text"), str):
            continue
        source = matching[0]["text"]
        if _quote_occurs(original, source):
            continue
        # A path with backslashes and letters is not necessarily a math command.
        math_pattern = (
            r"(?<!\\)\$\$[\s\S]*?\$\$|(?<![\\$])\$(?!\$)[^$]+?\$"
            r"|\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]"
        )
        candidate = re.sub(
            math_pattern,
            lambda math: re.sub(r"\\{2,}(?=[A-Za-z])", lambda _: "\\", math[0]),
            original,
        )
        operations = ["duplicate_latex_command_backslashes"] if candidate != original else []
        math_spans = [
            (match.start(), match.end()) for match in re.finditer(math_pattern, candidate)
        ]

        def newline_escape(match):
            if any(start <= match.start() < end for start, end in math_spans):
                return match[0]
            command = re.match(r"\\+([A-Za-z]+)", candidate[match.start() :])
            if command and (
                command[1] in {"nabla", "nu"}
                or re.search(r"\\" + re.escape(command[1]) + r"(?![A-Za-z])", source)
            ):
                return match[0]
            return "\n"

        newlines = re.sub(r"\\+(?:r\\+n|n)", newline_escape, candidate)
        if newlines != candidate:
            operations.append("json_newline_escapes")
        candidate = newlines
        if not candidate.strip():
            continue
        candidate = candidate.strip()
        percent_spans = []
        for math in re.finditer(math_pattern, candidate):
            for slashes in re.finditer(r"(?<!\\)\\{2,}(?=%)", math[0]):
                counts = [len(slashes[0])]
                # JSON re-escaping doubles a run. Allow only undoing complete
                # doublings, retaining at least one source backslash. Source
                # matching decides the exact count; never guess 4→1 over 4→2.
                while counts[-1] % 2 == 0:
                    counts.append(counts[-1] // 2)
                if len(counts) > 1:
                    percent_spans.append(
                        (math.start() + slashes.start(), math.start() + slashes.end(), counts)
                    )
        if candidate == original and not percent_spans:
            continue

        def literal_pattern(value):
            return "".join(
                r"\s+" if part.isspace() else re.escape(part) for part in re.split(r"(\s+)", value)
            )

        parts, cursor = [], 0
        for position, (start, end, counts) in enumerate(percent_spans):
            parts.append(literal_pattern(candidate[cursor:start]))
            alternatives = "|".join(re.escape("\\" * count) for count in counts)
            parts.append(r"(?<!\\)" + f"(?P<percent{position}>{alternatives})")
            cursor = end
        parts.append(literal_pattern(candidate[cursor:]))
        pattern = "".join(parts)
        occurrences = list(re.finditer(pattern, source))
        if len(occurrences) != 1:
            continue
        match = occurrences[0]
        if any(
            len(match[f"percent{position}"]) < counts[0]
            for position, (_, _, counts) in enumerate(percent_spans)
        ):
            operations.append("duplicate_latex_percent_backslashes")
        if not operations:
            continue
        entry["original_quote"] = original
        entry["quote"] = source[match.start() : match.end()]
        entry["quote_normalization"] = {
            "kind": operations[0] if len(operations) == 1 else "source_format_escapes",
            "operations": operations,
            "citation_id": pid,
            "passage_id": entry.get("passage_id"),
            "source_start": match.start(),
            "source_end": match.end(),
        }
    return repaired


def _markdown_text(value: object) -> str:
    text = " ".join(str(value).split())
    return re.sub(r"([\\`*_{}\[\]<>])", r"\\\1", text)


def _source_url(value: object) -> str | None:
    if not isinstance(value, str) or any(ord(char) < 32 for char in value):
        return None
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"https", "http"} or not parsed.hostname:
        return None
    return quote(value.strip(), safe=":/?#[]@!$&'()*+,;=%")


def link_citations(text: str, registry: list[dict]) -> str:
    """Link only parsed prose citations while preserving other Markdown exactly.

    Run structural validation before rendering: an unknown or malformed citation
    group remains untouched rather than hiding it through partial conversion.
    The mask preserves character offsets, so code, math, escaped brackets, and
    existing Markdown links remain byte-for-byte unchanged. Unsafe or absent
    source URLs leave the corresponding numeric label as plain text.
    """
    papers, issues = _registry_index(registry)
    if issues:
        raise ValueError("Invalid citation registry: " + " ".join(issues))
    links = {pid: _source_url(paper.get("url")) for pid, paper in papers.items()}
    replacements = []
    for match in _BRACKETS.finditer(_prose_only(text)):
        ids, parsing_issues = _citations(match[0])
        if not ids or parsing_issues or not ids.issubset(papers):
            continue
        replacement = "".join(
            f"[[{pid}]](<{links[pid]}>)" if links[pid] else f"[{pid}]" for pid in sorted(ids)
        )
        replacements.append((match.start(), match.end(), replacement))
    for start, end, replacement in reversed(replacements):
        text = text[:start] + replacement + text[end:]
    return text


def render_bibliography(registry: list[dict]) -> str:
    """Render stable reference labels, source links, and supplied publication years."""
    papers, issues = _registry_index(registry)
    if issues:
        raise ValueError("Invalid bibliography registry: " + " ".join(issues))
    lines = ["## 参考文献", ""]
    for pid, paper in sorted(papers.items()):
        title = _markdown_text(paper.get("title") or "未命名文献")
        url = _source_url(paper.get("url"))
        linked_title = f"[{title}](<{url}>)" if url else title
        value = paper.get("year") or paper.get("published_date") or ""
        year_match = re.search(r"\b(?:19|20|21)\d{2}\b", str(value))
        year = year_match[0] if year_match else "年份未提供"
        authors = paper.get("authors") or ""
        if isinstance(authors, list):
            authors = "; ".join(str(value) for value in authors)
        author_prefix = f"{_markdown_text(authors)}. " if authors else ""
        lines.append(f"- **[{pid}]** {author_prefix}{linked_title} ({year}).")
    return "\n".join(lines)


def audit_coverage(sections: list[dict], registry: list[dict]) -> dict:
    """Count body coverage and outline assignments, excluding unknown reference IDs.

    Pass authored body sections, not the assembled bibliography. Each item may
    contain ``content`` and ``reference_ids``; overlaps count only once.
    """
    papers, issues = _registry_index(registry)
    if issues:
        raise ValueError("Invalid coverage registry: " + " ".join(issues))
    valid = set(papers)
    cited = set()
    assigned = set()
    for section in sections:
        if not isinstance(section, dict):
            continue
        cited.update(citation_ids(section.get("content", "")) & valid)
        references = section.get("reference_ids")
        if isinstance(references, (list, tuple, set)):
            assigned.update(pid for pid in references if _is_id(pid) and pid in valid)
    return {
        "total": len(valid),
        "cited": len(cited),
        "uncited_ids": sorted(valid - cited),
        "assigned": len(assigned),
    }
