import pytest
from app.services.knowledge import (
    write_note_atomic,
    read_note,
    list_all_notes,
    search_knowledge,
    rebuild_knowledge_index,
    parse_frontmatter
)

def test_frontmatter_parsing():
    doc = """---
title: Test Title
category: decisions
tags: [adr, rpc]
---

# Body Content Here
This is the markdown body.
"""
    parsed = parse_frontmatter(doc)
    assert parsed["meta"]["title"] == "Test Title"
    assert parsed["meta"]["category"] == "decisions"
    assert "# Body Content Here" in parsed["body"]

def test_atomic_write_and_read():
    rel_path = "decisions/adr-001-sqlite-fts5.md"
    content = """---
title: ADR 001 - SQLite FTS5 for Knowledge
category: decisions
---

We decided to use SQLite FTS5 for full-text search rather than a heavy vector database.
"""
    success = write_note_atomic(rel_path, content)
    assert success is True
    
    note = read_note(rel_path)
    assert note is not None
    assert note["title"] == "ADR 001 - SQLite FTS5 for Knowledge"
    assert note["category"] == "decisions"
    assert "We decided to use SQLite FTS5" in note["rendered_html"]

def test_fts5_full_text_search():
    # Write a note with unique keywords
    write_note_atomic(
        "research/quantization_findings.md",
        """---
title: Quantization Analysis
category: research
---

Q4_K_M offers the best balance of perplexity and memory bandwidth on DDR3 laptops.
"""
    )
    
    results = search_knowledge("perplexity")
    assert len(results) >= 1
    match = next((r for r in results if "quantization_findings" in r["path"]), None)
    assert match is not None
    assert "Quantization Analysis" in match["title"]
    assert "<mark>" in match["snippet"]

def test_prevent_directory_traversal():
    with pytest.raises(ValueError):
        write_note_atomic("../../etc/passwd.md", "malicious content")
        
    assert read_note("../../etc/passwd.md") is None
