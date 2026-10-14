"""Behavioral tests: knowledge rendering must not execute HTML/JS from vault content.

Vault content may originate from external sources or the VPS worker, so it is untrusted.
"""
import re
import pytest
from starlette.testclient import TestClient
from app.main import app
from app.auth import create_session_token
from app.services.knowledge import write_note_atomic, read_note

PAYLOAD_NOTE = """---
title: Hostile Note
---

Intro text with zebrafinch keyword.

<script>alert('xss-script')</script>

<img src=x onerror="alert('xss-img')">

[click me](javascript:alert('xss-link'))

![pic](javascript:alert('xss-imgsrc'))

[good link](https://example.com/page)

```
<b>code stays literal</b>
```
"""


@pytest.fixture
def auth_client():
    client = TestClient(app)
    client.cookies.set("qc_session", create_session_token("admin"))
    return client


def test_rendered_markdown_strips_raw_html_and_js_urls():
    write_note_atomic("inbox/hostile_note.md", PAYLOAD_NOTE)
    note = read_note("inbox/hostile_note.md")
    html = note["rendered_html"]
    assert "<script" not in html
    assert not re.search(r"<img[^>]*onerror", html), "live <img onerror> tag rendered"
    assert "javascript:" not in html
    # Legitimate markdown still works
    assert 'href="https://example.com/page"' in html
    assert "&lt;b&gt;code stays literal&lt;/b&gt;" in html


def test_note_view_page_does_not_emit_executable_html(auth_client):
    write_note_atomic("inbox/hostile_note.md", PAYLOAD_NOTE)
    r = auth_client.get("/knowledge/view", params={"path": "inbox/hostile_note.md"})
    assert r.status_code == 200
    # The raw markdown editor textarea is autoescaped; the rendered body must not contain live tags.
    body = r.text
    assert "<script>alert('xss-script')</script>" not in body
    assert 'href="javascript:' not in body
    assert 'src="javascript:' not in body


def test_search_snippet_is_escaped(auth_client):
    write_note_atomic("inbox/hostile_note.md", PAYLOAD_NOTE)
    r = auth_client.get("/knowledge", params={"q": "zebrafinch"})
    assert r.status_code == 200
    assert "<script>alert" not in r.text
    assert "<img src=x onerror" not in r.text
    # Highlighting is still rendered as markup
    assert "<mark>" in r.text
