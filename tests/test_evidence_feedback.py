import json
import sqlite3
from uuid import uuid4
import pytest
from app.database import get_db_connection, db_transaction
from app.services import projects
from app.services.research.feedback import ProjectEvidenceFeedback, install_feedback_schema
from app.services.research.planner import ResearchPlanner, ResearchBudget
from app.services.research.sources import SourceLibrary
from app.services.research.manager import ResearchManager

STATEMENT='The pilot grant requires a cash match.'
COUNTER='The pilot grant does not require a cash match.'


@pytest.fixture
def feedback(tmp_path):
    pid=projects.create_project('Feedback fixture '+uuid4().hex,'tester')['id']
    rid='research-'+uuid4().hex
    with db_transaction() as c:
        c.execute('''INSERT INTO research_runs (id,project_id,status,created_at,updated_at)
            VALUES (?,?,'active','2026-10-26T00:00:00Z','2026-10-26T00:00:00Z')''',(rid,pid))
    library=SourceLibrary(base_storage_dir=tmp_path/'sources')
    texts={'support':'Grant terms\n'+STATEMENT+'\n', 'contradiction':COUNTER+'\n',
        'copy':'Grant terms\n'+STATEMENT+'\n','unrelated':'A different program supports tree planting.\n',
        'negated':'It is false that '+STATEMENT+'\n'}
    sources={name:library.add_source(pid,'https://'+name+'.example/terms',extracted_text=text,
        research_run_id=rid) for name,text in texts.items()}
    conn=get_db_connection()
    install_feedback_schema(conn)
    ledger=ProjectEvidenceFeedback(conn,pid,rid)
    ledger.add_requirement('match','What match is required for the pilot grant?','budget')
    ledger.add_claim('claim',STATEMENT,['match'],COUNTER)
    conn.commit()
    yield ledger,sources,tmp_path/'sources'
    conn.rollback(); conn.close()


def capture(fixture,name):
    ledger,sources,root=fixture
    return ledger.capture_registered_source(sources[name]['id'],root)


def test_missing_evidence_creates_targeted_path(feedback):
    ledger,_,_=feedback
    result=ResearchPlanner().plan_evidence_feedback(ledger,ResearchBudget())
    path=result['paths'][0]
    assert path['reason']=='MISSING_EVIDENCE'
    assert path['deliverable_key']=='budget' and path['requirement_id']=='match'
    assert path['assertion_to_check']==STATEMENT
    assert path['requires_native_dispatch'] is True
    assert path['grants_execution_authority'] is False


def test_whole_assertion_support_with_exact_offsets(feedback):
    ledger,_,_=feedback
    snapshot=capture(feedback,'support')
    claim=ledger.assess()['claims'][0]
    assert claim['status']=='SUPPORTED_QUOTATION'
    passage=claim['support'][0]
    assert snapshot['text'][passage['start']:passage['end']]==STATEMENT
    assert passage['sha256']==snapshot['sha256']
    assert claim['confidence']=='SOURCE_QUOTATION_ONLY'
    assert claim['empirical_probability'] is None
    assert claim['independent_source_count'] is None
    assert claim['certification_eligible'] is False
    assert ledger.propose_paths()['paths'][0]['reason']=='AUTHENTICATED_RETRIEVAL_REQUIRED'


def test_contradiction_reopens_supported_claim(feedback):
    ledger,_,_=feedback
    capture(feedback,'support')
    first=ledger.propose_paths()['paths'][0]
    capture(feedback,'contradiction')
    result=ledger.propose_paths()
    claim=result['assessment']['claims'][0]
    assert claim['status']=='CONFLICT' and claim['confidence']=='CONFLICTED'
    assert claim['support'] and claim['contradictions']
    assert result['paths'][0]['reason']=='CONFLICT'
    assert result['paths'][0]['id']!=first['id']
    assert len(ledger.items('path'))==2


def test_copied_evidence_never_adds_independent_confidence(feedback):
    ledger,_,_=feedback
    capture(feedback,'support')
    before=ledger.propose_paths()['assessment']['claims'][0]
    capture(feedback,'copy')
    after=ledger.propose_paths()
    claim=after['assessment']['claims'][0]
    assert claim['distinct_content_count']==1
    assert claim['confidence']==before['confidence']
    assert claim['revision']==before['revision']
    assert after['paths']==[]
    assert after['stop_reason']=='NO_NEW_PATHS'


def test_unrelated_evidence_does_not_restart_stagnant_work(feedback):
    ledger,_,_=feedback
    first=ledger.propose_paths()
    capture(feedback,'unrelated')
    again=ledger.propose_paths()
    assert again['paths']==[]
    assert again['assessment']['claims'][0]['revision']==first['assessment']['claims'][0]['revision']
    assert len(ledger.items('cycle'))==1


def test_negated_context_is_not_substring_support(feedback):
    ledger,_,_=feedback
    capture(feedback,'negated')
    assert ledger.assess()['claims'][0]['status']=='MISSING_EVIDENCE'


def test_identical_capture_is_idempotent(feedback):
    ledger,_,_=feedback
    assert capture(feedback,'support')==capture(feedback,'support')
    assert len(ledger.items('snapshot'))==1


def test_snapshot_survives_source_file_change(feedback):
    ledger,sources,_=feedback
    snapshot=capture(feedback,'support')
    from pathlib import Path
    Path(sources['support']['storage_path']).write_text(COUNTER,encoding='utf-8')
    newer=capture(feedback,'support')
    assert newer['id']!=snapshot['id']
    assert ledger.assess()['claims'][0]['status']=='CONFLICT'
    assert ledger.items('snapshot') and len(ledger.items('snapshot'))==2


def test_claim_identity_and_requirements_are_immutable(feedback):
    ledger,_,_=feedback
    with pytest.raises(ValueError,match='Immutable'):
        ledger.add_claim('claim',COUNTER,['match'],STATEMENT)
    with pytest.raises(ValueError,match='existing scoped'):
        ledger.add_claim('new','Another assertion.',['unknown'])


@pytest.mark.parametrize('operation',["UPDATE research_feedback_items SET payload_json='{}'",'DELETE FROM research_feedback_items'])
def test_append_only_storage(feedback,operation):
    with pytest.raises(sqlite3.IntegrityError,match='append-only'):
        feedback[0].conn.execute(operation)


def test_cross_project_run_isolation(feedback):
    ledger,sources,root=feedback
    with pytest.raises(ValueError,match='belong'):
        ProjectEvidenceFeedback(ledger.conn,'another-project',ledger.run_id)
    ledger.conn.execute('UPDATE research_sources SET research_run_id=NULL WHERE id=?',(sources['support']['id'],))
    with pytest.raises(ValueError,match='cross-run'):
        ledger.capture_registered_source(sources['support']['id'],root)


def test_source_path_escape_is_rejected(feedback,tmp_path):
    with pytest.raises(ValueError,match='outside'):
        feedback[0].capture_registered_source(feedback[1]['support']['id'],tmp_path/'wrong-root')


def test_failed_source_cannot_be_captured(feedback):
    ledger,sources,root=feedback
    ledger.conn.execute("UPDATE research_sources SET status='failed' WHERE id=?",(sources['support']['id'],))
    with pytest.raises(ValueError,match='metadata'):
        ledger.capture_registered_source(sources['support']['id'],root)


def test_requirement_without_claim_creates_path(feedback):
    ledger,_,_=feedback
    ledger.add_requirement('authority','Which authority governs?','regulatory')
    result=ledger.propose_paths(max_paths=2)
    missing=next(p for p in result['paths'] if p['requirement_id']=='authority')
    assert missing['reason']=='NO_CLAIM' and missing['claim_id'] is None


def test_cycle_budget_does_not_hide_conflicts(feedback):
    ledger,_,_=feedback
    capture(feedback,'support')
    ledger.propose_paths(max_cycles=1)
    capture(feedback,'contradiction')
    result=ledger.propose_paths(max_cycles=1)
    assert result['paths']==[] and result['stop_reason']=='CYCLE_BUDGET_EXHAUSTED'
    assert result['assessment']['claims'][0]['status']=='CONFLICT'


def test_planner_respects_existing_query_and_iteration_budget(feedback):
    ledger,_,_=feedback
    planner=ResearchPlanner()
    for budget in (ResearchBudget(max_search_queries=0),ResearchBudget(max_iterations=0),
                   ResearchBudget(max_retrieved_documents=0),ResearchBudget(max_depth=0),
                   ResearchBudget(max_browsing_seconds=0),ResearchBudget(max_inference_tokens=0)):
        result=planner.plan_evidence_feedback(ledger,budget)
        assert not result['paths'] and result['stop_reason']=='RESEARCH_BUDGET_EXHAUSTED'
    budget=ResearchBudget()
    planner.plan_evidence_feedback(ledger,budget)
    assert budget.queries_executed==0 and budget.iterations_run==0


def test_durable_restart_deduplicates_paths(feedback):
    ledger,_,_=feedback
    ledger.propose_paths(); ledger.conn.commit()
    fresh=ProjectEvidenceFeedback(ledger.conn,ledger.project_id,ledger.run_id)
    assert fresh.propose_paths()['paths']==[]


def test_caller_rollback_removes_proposals(feedback):
    ledger,_,_=feedback
    ledger.propose_paths()
    ledger.conn.rollback()
    assert ledger.items('path')==[] and ledger.items('cycle')==[]


def test_manager_bridge_uses_transaction_and_does_not_execute(feedback):
    ledger,_,_=feedback
    ledger.conn.commit()
    manager=object.__new__(ResearchManager)
    manager.planner=ResearchPlanner()
    result=manager.prepare_evidence_feedback(ledger.project_id,ledger.run_id)
    assert result['paths'] and result['assessment']['certification_eligible'] is False
    with get_db_connection() as c:
        assert c.execute('SELECT count(*) FROM tasks WHERE project_id=?',(ledger.project_id,)).fetchone()[0]==0


def test_invalid_budgets_fail_closed(feedback):
    for count in (-1,21,True,'3'):
        with pytest.raises(ValueError): feedback[0].propose_paths(max_paths=count)


def test_assessment_history_preserves_reopened_claim(feedback):
    ledger,_,_=feedback
    capture(feedback,'support'); ledger.propose_paths()
    capture(feedback,'contradiction'); ledger.propose_paths()
    history=ledger.items('assessment')
    assert {a['claims'][0]['status'] for a in history}=={'SUPPORTED_QUOTATION','CONFLICT'}
    assert all(a['verifier_version']=='exact-source-quotation.v1' for a in history)
    assert all(a['certification_eligible'] is False for a in history)


def test_no_path_budget_still_records_unresolved_assessment(feedback):
    ledger,_,_=feedback
    result=ledger.propose_paths(max_paths=0)
    assert result['stop_reason']=='PATH_BUDGET_EXHAUSTED'
    assert ledger.items('assessment') and not ledger.items('cycle')


def test_missing_schema_is_not_silently_installed(feedback):
    ledger,_,_=feedback
    # New isolated connection containing scope metadata but no feedback schema.
    with sqlite3.connect(':memory:') as conn:
        conn.execute('CREATE TABLE research_runs (id,project_id)')
        conn.execute('INSERT INTO research_runs VALUES (?,?)',(ledger.run_id,ledger.project_id))
        fresh=ProjectEvidenceFeedback(conn,ledger.project_id,ledger.run_id)
        with pytest.raises(sqlite3.OperationalError): fresh.assess()


def test_manager_obeys_persisted_project_budget(feedback):
    ledger,_,_=feedback
    ledger.conn.commit()
    manager=object.__new__(ResearchManager)
    manager.planner=ResearchPlanner()
    manager.create_or_get_budget(ledger.project_id,ResearchBudget(max_search_queries=0))
    result=manager.prepare_evidence_feedback(ledger.project_id,ledger.run_id)
    assert result['stop_reason']=='RESEARCH_BUDGET_EXHAUSTED'
    assert not result['paths']


def test_malformed_retrieval_time_is_rejected(feedback):
    ledger,sources,root=feedback
    ledger.conn.execute("UPDATE research_sources SET retrieved_at='unverified' WHERE id=?",(sources['support']['id'],))
    with pytest.raises(ValueError):
        ledger.capture_registered_source(sources['support']['id'],root)
