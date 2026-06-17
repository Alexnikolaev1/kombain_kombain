from utils.telegram import escape_html, format_content_preview, split_text


def test_escape_html_protects_special_chars():
    raw = '<script>alert("x")</script> & more'
    escaped = escape_html(raw)
    assert "<" not in escaped
    assert ">" not in escaped
    assert "&amp;" in escaped


def test_split_text_respects_paragraphs():
    text = "A\n\nB\n\nC"
    chunks = split_text(text, max_len=5)
    assert len(chunks) >= 2
    assert "".join(chunks).replace("\n", "") == "ABC"


def test_split_text_keeps_short_text_intact():
    text = "короткий ответ"
    assert split_text(text, max_len=100) == [text]


def test_format_content_preview_short_text_without_ellipsis():
    msg = format_content_preview(title="Test", content="Короткий текст", char_count=14)
    assert "…" not in msg
    assert "..." not in msg
    assert "Короткий текст" in msg


def test_format_content_preview_long_text_truncated_once():
    content = "слово " * 500
    msg = format_content_preview(title="", content=content, char_count=len(content))
    assert msg.count("…") == 1
    assert "..." not in msg
