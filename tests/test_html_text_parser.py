"""Tests for private-gate HTML/TXT parsing."""

from __future__ import annotations

from idis.parsers.html_text import parse_html_text


def test_html_parser_extracts_visible_text_without_scripts_or_tags() -> None:
    result = parse_html_text(
        b"""
        <html>
          <head><style>.secret { display: none; }</style></head>
          <body>
            <h1>Visible heading</h1>
            <script>confidentialScriptToken()</script>
            <p>Visible paragraph &amp; value.</p>
          </body>
        </html>
        """,
        is_html=True,
    )

    encoded = result.to_dict()
    assert result.success is True
    assert result.doc_type == "HTML"
    assert [span.text_excerpt for span in result.spans] == [
        "Visible heading",
        "Visible paragraph & value.",
    ]
    assert "script" not in str(encoded).lower()
    assert "<h1>" not in str(encoded)


def test_text_parser_extracts_non_empty_lines_deterministically() -> None:
    result = parse_html_text(
        b"First line\n\nSecond line\n",
        is_html=False,
    )

    assert result.success is True
    assert result.doc_type == "TEXT"
    assert [span.locator for span in result.spans] == [
        {"line": 1, "source": "text"},
        {"line": 3, "source": "text"},
    ]
    assert [span.text_excerpt for span in result.spans] == ["First line", "Second line"]
