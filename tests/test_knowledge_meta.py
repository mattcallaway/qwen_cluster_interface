"""Milestone 1 knowledge layer: typed metadata, provenance, trust review, relations, project scoping."""
import sqlite3
import uuid

import pytest

from app.config import settings
from app.database import get_db_connection
from app.services import knowledge_meta as km
from app.services import projects as ps
from app.services.knowledge import read_note, write_note_atomic


def _project(prefix="KProj"):
    return ps.create_project(f"{prefix} {uuid.uuid4().hex[:8]}", owner="admin")


def _note(project, title=None, **kw):
    args = dict(knowledge_type="finding", origin="human", body="Kestrel findings body", author="admin")
    args.update(kw)
    return km.create_project_note(project["id"], title or f"Finding {uuid.uuid4().hex[:6]}", **args)


def test_project_note_written_to_vault_and_registered():
    p = _project()
    meta = _note(p, title="Kestrel survey", body="The kestrel population doubled.")
    assert meta["trust_status"] == "captured"
    assert meta["project_id"] == p["id"]
    assert meta["note_path"].startswith(p["knowledge_dir"] + "/")
    assert (settings.knowledge_dir / meta["note_path"]).is_file()
    note = read_note(meta["note_path"])
    assert note["meta"]["kn_id"] == meta["id"]
    assert "kestrel population doubled" in note["raw_content"]


def test_project_scoped_search_does_not_leak_other_projects():
    a, b = _project("A"), _project("B")
    _note(a, title="Osprey nest", body="osprey nesting quokkaword data")
    _note(b, title="Osprey other", body="osprey quokkaword elsewhere")
    hits_a = km.search_project_knowledge(a["id"], "quokkaword")
    assert len(hits_a) == 1 and hits_a[0]["trust_status"] == "captured"
    assert all(h["path"].startswith(a["knowledge_dir"]) for h in hits_a)


def test_external_knowledge_requires_provenance_and_keeps_it():
    p = _project()
    with pytest.raises(ps.ValidationError, match="source"):
        _note(p, origin="external_source")
    meta = _note(p, origin="vps_extracted", source_url="https://example.org/doc",
                 source_retrieved_at="2026-10-01", source_citation="County records index")
    assert (meta["source_url"], meta["source_retrieved_at"]) == ("https://example.org/doc", "2026-10-01")
    with pytest.raises(ps.ValidationError):
        _note(p, origin="external_source", source_url="javascript:alert(1)")


def test_knowledge_cannot_be_inserted_as_trusted():
    conn = get_db_connection()
    with pytest.raises(sqlite3.IntegrityError, match="captured or proposed"):
        conn.execute("""INSERT INTO knowledge_metadata (id, note_path, title, knowledge_type, origin, trust_status,
                        author, created_at, updated_at)
                        VALUES ('kn-x', 'inbox/x.md', 'x', 'finding', 'vps_extracted', 'accepted', 'bot', 'now', 'now')""")
    conn.close()


def test_trust_transitions_require_review_path():
    p = _project()
    meta = _note(p)
    with pytest.raises(ps.ValidationError):
        km.review_knowledge(meta["id"], "accepted", "admin")  # captured -> accepted skips verification
    v = km.review_knowledge(meta["id"], "verified", "admin", "Checked against source")
    assert v["trust_status"] == "verified" and v["reviewed_by"] == "admin" and v["last_verified_at"]
    a = km.review_knowledge(meta["id"], "accepted", "admin")
    assert a["trust_status"] == "accepted"


def test_direct_promotion_without_new_review_blocked_by_database():
    p = _project()
    meta = _note(p)
    conn = get_db_connection()
    with pytest.raises(sqlite3.IntegrityError, match="review"):
        conn.execute("UPDATE knowledge_metadata SET trust_status='verified' WHERE id = ?", (meta["id"],))
    conn.close()


def test_dispute_requires_note():
    meta = _note(_project())
    with pytest.raises(ps.ValidationError, match="note"):
        km.review_knowledge(meta["id"], "disputed", "admin", "")
    d = km.review_knowledge(meta["id"], "disputed", "admin", "Contradicted by 2025 census")
    assert d["trust_status"] == "disputed" and d["review_note"]


def test_supersession_rules():
    p = _project()
    old, new = _note(p), _note(p)
    km.review_knowledge(old["id"], "verified", "admin")
    with pytest.raises(ps.ValidationError):
        km.review_knowledge(old["id"], "superseded", "admin", "replaced", superseded_by=old["id"])
    with pytest.raises(ps.ValidationError):
        km.review_knowledge(old["id"], "superseded", "admin", "replaced", superseded_by="kn-missing")
    s = km.review_knowledge(old["id"], "superseded", "admin", "Newer data", superseded_by=new["id"])
    assert s["trust_status"] == "superseded" and s["superseded_by"] == new["id"]
    with pytest.raises(ps.ValidationError):
        km.review_knowledge(old["id"], "verified", "admin")  # superseded is terminal
    # Old entry is preserved, not deleted
    assert km.get_metadata(old["id"]) is not None


def test_relations_validate_target_and_project_scope():
    a, b = _project("RA"), _project("RB")
    path_a = ps.create_path(a["id"], "admin", name="PA", rationale="r")
    path_b = ps.create_path(b["id"], "admin", name="PB", rationale="r")
    meta = _note(a, knowledge_type="assumption")
    km.add_relation(meta["id"], "path", path_a["id"], "supports", "admin", scope_project_id=a["id"])
    with pytest.raises(ps.ValidationError, match="does not belong"):
        km.add_relation(meta["id"], "path", path_b["id"], "supports", "admin", scope_project_id=a["id"])
    with pytest.raises(ps.ValidationError, match="does not exist"):
        km.add_relation(meta["id"], "path", "pth-missing", "supports", "admin")
    with pytest.raises(ps.ValidationError, match="already exists"):
        km.add_relation(meta["id"], "path", path_a["id"], "supports", "admin", scope_project_id=a["id"])
    with pytest.raises(ps.ValidationError):
        km.add_relation(meta["id"], "knowledge", meta["id"], "related_to", "admin")
    rels = km.list_relations_for_target("path", path_a["id"])
    assert [(r["knowledge_id"], r["relation_type"]) for r in rels] == [(meta["id"], "supports")]


def test_cross_project_reuse_preserves_provenance():
    a, b = _project("Src"), _project("Reuse")
    lesson = _note(a, knowledge_type="lesson_learned", title="Rate limits matter")
    path_b = ps.create_path(b["id"], "admin", name="PB", rationale="r")
    km.add_relation(lesson["id"], "path", path_b["id"], "procedure_for", "admin", scope_project_id=b["id"])
    listed = {e["id"]: e for e in km.list_project_knowledge(b["id"])}
    assert listed[lesson["id"]]["link_kind"] == "linked"
    assert km.get_metadata(lesson["id"])["project_id"] == a["id"]  # original home unchanged


def test_register_existing_note_without_moving_it():
    write_note_atomic("research/existing_heron.md", "---\ntitle: Heron\n---\nHeron notes")
    p = _project()
    meta = km.register_existing_note("research/existing_heron.md", "source", "human", "admin", project_id=p["id"])
    assert meta["note_path"] == "research/existing_heron.md"
    assert (settings.knowledge_dir / "research/existing_heron.md").is_file()
    with pytest.raises(ps.ValidationError, match="already registered"):
        km.register_existing_note("research/existing_heron.md", "source", "human", "admin")


@pytest.mark.parametrize("bad", ["../../secrets.md", "..\\..\\x.md", "C:/Windows/win.md",
                                 "/etc/passwd.md", "research/../../outside.md", "research/not_markdown.txt"])
def test_register_rejects_paths_outside_vault(bad):
    with pytest.raises(ps.ValidationError):
        km.register_existing_note(bad, "source", "human", "admin")


def test_frontmatter_injection_in_title_is_neutralized():
    p = _project()
    meta = _note(p, title="Innocent\ntrust_status: accepted\n---\n<script>x</script>")
    note = read_note(meta["note_path"])
    assert "trust_status" not in note["meta"]
    assert meta["trust_status"] == "captured"
    assert "<script>" not in note["rendered_html"]


def test_invalid_type_and_origin_rejected():
    p = _project()
    with pytest.raises(ps.ValidationError):
        _note(p, knowledge_type="gospel")
    with pytest.raises(ps.ValidationError):
        _note(p, origin="oracle")
