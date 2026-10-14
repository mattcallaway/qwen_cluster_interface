import os
import re
import sqlite3
import tempfile
import markdown
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional
from app.config import settings
from app.database import get_db_connection

def parse_frontmatter(content: str) -> Dict[str, Any]:
    """Parse YAML-like frontmatter if present between --- delimiters."""
    meta = {}
    body = content
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            raw_meta = parts[1].strip()
            body = parts[2].strip()
            for line in raw_meta.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip().lower()] = v.strip()
    return {"meta": meta, "body": body}

def get_knowledge_categories() -> List[str]:
    return ["system", "projects", "research", "sources", "decisions", "journal", "inbox"]

def list_all_notes(category: Optional[str] = None) -> List[Dict[str, Any]]:
    """List all markdown notes across categories or within a specific category."""
    base_dir = settings.knowledge_dir
    notes = []
    
    cats = [category] if category else get_knowledge_categories()
    for cat in cats:
        cat_dir = base_dir / cat
        if not cat_dir.exists():
            continue
        for file in cat_dir.glob("*.md"):
            try:
                stat = file.stat()
                with open(file, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(500)
                parsed = parse_frontmatter(content)
                title = parsed["meta"].get("title") or file.stem.replace("_", " ").replace("-", " ").title()
                notes.append({
                    "path": f"{cat}/{file.name}",
                    "filename": file.name,
                    "title": title,
                    "category": cat,
                    "size_bytes": stat.st_size,
                    "updated_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
                })
            except Exception:
                pass
    notes.sort(key=lambda x: x["updated_at"], reverse=True)
    return notes

def read_note(rel_path: str) -> Optional[Dict[str, Any]]:
    """Read a markdown note securely, rendering both raw markdown and HTML."""
    safe_path = Path(rel_path).as_posix().lstrip("/\\")
    full_path = (settings.knowledge_dir / safe_path).resolve()
    
    # Boundary check to avoid path traversal
    if not full_path.is_relative_to(settings.knowledge_dir) or not full_path.exists():
        return None
        
    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
            
        parsed = parse_frontmatter(content)
        rendered_html = markdown.markdown(
            parsed["body"],
            extensions=["tables", "fenced_code", "nl2br"]
        )
        stat = full_path.stat()
        parts = safe_path.split("/", 1)
        category = parts[0] if len(parts) > 1 else "inbox"
        title = parsed["meta"].get("title") or full_path.stem.replace("_", " ").replace("-", " ").title()
        
        return {
            "path": safe_path,
            "filename": full_path.name,
            "category": category,
            "title": title,
            "meta": parsed["meta"],
            "raw_content": content,
            "rendered_html": rendered_html,
            "updated_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
        }
    except Exception:
        return None

def write_note_atomic(rel_path: str, content: str) -> bool:
    """Safely write a note using an atomic rename operation and index it into FTS5."""
    safe_path = Path(rel_path).as_posix().lstrip("/\\")
    full_path = (settings.knowledge_dir / safe_path).resolve()
    
    if not full_path.is_relative_to(settings.knowledge_dir):
        raise ValueError("Invalid target path outside knowledge repository.")
        
    full_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Write to temp file in same directory first
    temp_dir = full_path.parent
    with tempfile.NamedTemporaryFile("w", dir=temp_dir, delete=False, encoding="utf-8") as tf:
        tf.write(content)
        temp_name = tf.name
        
    try:
        # On Windows, replace requires destination to be overwritten atomically or removed
        os.replace(temp_name, full_path)
    except Exception as e:
        if os.path.exists(temp_name):
            os.remove(temp_name)
        raise e
        
    # Update search index for this file
    index_single_note(safe_path, content)
    return True

def index_single_note(rel_path: str, content: str):
    """Index or update a single note in SQLite FTS5."""
    parsed = parse_frontmatter(content)
    parts = rel_path.split("/", 1)
    category = parts[0] if len(parts) > 1 else "inbox"
    filename = Path(rel_path).name
    title = parsed["meta"].get("title") or Path(rel_path).stem.replace("_", " ").replace("-", " ").title()
    now = datetime.now(timezone.utc).isoformat()
    
    conn = get_db_connection()
    cursor = conn.cursor()
    # Remove existing entry if any
    cursor.execute("DELETE FROM knowledge_fts WHERE path = ?", (rel_path,))
    # Insert new FTS entry
    cursor.execute("""
    INSERT INTO knowledge_fts (path, title, category, content, updated_at)
    VALUES (?, ?, ?, ?, ?)
    """, (rel_path, title, category, f"{title}\n{parsed['body']}", now))
    conn.commit()
    conn.close()

def rebuild_knowledge_index() -> int:
    """Full rebuild of SQLite FTS5 knowledge index."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM knowledge_fts;")
    
    count = 0
    now = datetime.now(timezone.utc).isoformat()
    for cat in get_knowledge_categories():
        cat_dir = settings.knowledge_dir / cat
        if not cat_dir.exists():
            continue
        for file in cat_dir.glob("*.md"):
            try:
                rel_path = f"{cat}/{file.name}"
                with open(file, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                parsed = parse_frontmatter(content)
                title = parsed["meta"].get("title") or file.stem.replace("_", " ").replace("-", " ").title()
                cursor.execute("""
                INSERT INTO knowledge_fts (path, title, category, content, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """, (rel_path, title, cat, f"{title}\n{parsed['body']}", now))
                count += 1
            except Exception:
                pass
                
    conn.commit()
    conn.close()
    return count

def search_knowledge(query: str) -> List[Dict[str, Any]]:
    """Search knowledge repository using SQLite FTS5."""
    if not query.strip():
        return []
        
    # Sanitize FTS5 query terms defensively
    clean_terms = re.findall(r'\w+', query)
    if not clean_terms:
        return []
    fts_query = " ".join([f'"{term}"*' for term in clean_terms])
    
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT path, title, category, snippet(knowledge_fts, 3, '<mark>', '</mark>', '...', 20) as snippet, updated_at
        FROM knowledge_fts
        WHERE knowledge_fts MATCH ?
        ORDER BY rank
        LIMIT 30;
        """, (fts_query,))
        rows = cursor.fetchall()
        results = [dict(row) for row in rows]
    except sqlite3.OperationalError:
        # Fallback to simple LIKE if syntax issue
        like_query = f"%{query.strip()}%"
        cursor.execute("""
        SELECT path, title, category, substr(content, 1, 150) as snippet, updated_at
        FROM knowledge_fts
        WHERE title LIKE ? OR content LIKE ?
        LIMIT 30;
        """, (like_query, like_query))
        rows = cursor.fetchall()
        results = [dict(row) for row in rows]
    finally:
        conn.close()
        
    return results

def seed_initial_knowledge():
    """Create starter documentation notes in the knowledge folders if empty."""
    welcome_path = "system/welcome.md"
    if not (settings.knowledge_dir / welcome_path).exists():
        content = """---
title: Welcome to Qwen Collective
category: system
created: 2026-10-14
tags: [architecture, intro]
---

# Qwen Collective Knowledge Base

Welcome to the persistent knowledge repository of Qwen Collective.

## Core Principles
1. **Local Ownership**: This directory contains raw Markdown files. It can be opened directly in Obsidian or any plain text editor.
2. **Persistence**: Knowledge stored here survives model upgrades, server reboots, and compute node replacements.
3. **Structured Taxonomy**:
   - `system/`: Operational policies, architecture notes, and hardware maps.
   - `projects/`: Specific research or construction efforts.
   - `research/`: Synthesized findings and source analyses.
   - `sources/`: Transcripts, reference summaries, and bookmarks.
   - `decisions/`: Architecture Decision Records (ADRs).
   - `journal/`: Daily logs and historical operational records.
   - `inbox/`: Staged external findings awaiting review and promotion.
"""
        write_note_atomic(welcome_path, content)
