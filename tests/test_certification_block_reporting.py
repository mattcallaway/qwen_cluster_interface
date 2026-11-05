import json
from unittest.mock import patch
import pytest
from certification_fixtures import recorded_native_outputs
from app.database import db_transaction, get_db_connection
from app.services.orchestration import engine, states, evaluator
from app.services.orchestration.common import StaleState, get_run


@pytest.fixture
def native_run():
    plan={'deliverables':[{'key':'output','name':'Output','produced_by':'produce'}]}
    pid,run,tested=recorded_native_outputs(plan,{'objective':'Produce a local output'},inference=True)
    with db_transaction() as c:
        c.execute('UPDATE orchestration_runs SET analysis_json=? WHERE id=?',
                  (json.dumps({'keep':'existing metadata','deliverable_map':{'output':'fixture'}}),run['id']))
    return pid,get_run(run['id']),plan,tested


def test_native_nonresearch_positive_control(native_run):
    pid,run,plan,tested=native_run
    result=evaluator.evaluate_project_objective_fulfillment(pid,run,plan,
        {'success_criteria':'model inference required'},deliverables_tested=tested)
    assert result['technical_execution_success'] is True
    assert result['substantively_fulfilled'] is True


@pytest.mark.parametrize('code',['REQUIRED_ARTIFACT_MISSING','DELIVERABLE_EVIDENCE_BLOCKED','DELIVERABLE_ACCEPTANCE_REJECTED'])
def test_block_reason_preserved_with_metadata(native_run,code):
    _,run,_,_=native_run
    result=engine._block_deliverable_evaluation(run,'output','Missing independently verified evidence',code,
        {'checks':[{'name':'native_output_provenance','passed':False}]})
    assert result['state']=='BLOCKED'
    meta=json.loads(result['analysis_json'])
    assert meta['keep']=='existing metadata'
    assert meta['deliverable_map']=={'output':'fixture'}
    sub=meta['substantive_evaluation']
    assert sub['reason_code']==code
    assert sub['substantively_fulfilled'] is False
    assert sub['objective_evaluation_performed'] is False
    assert sub['fulfillment_score']==0
    assert sub['checks'][0]['passed'] is False
    with get_db_connection() as c:
        event=c.execute('SELECT detail_json FROM orchestration_events WHERE run_id=? AND event_type=?',
            (run['id'],'deliverable_validation_blocked')).fetchone()
    assert json.loads(event['detail_json'])==sub


def test_block_transition_rolls_back_if_audit_write_fails(native_run):
    _,run,_,_=native_run
    with patch.object(states,'log_event',side_effect=RuntimeError('audit unavailable')):
        with pytest.raises(RuntimeError,match='audit unavailable'):
            engine._block_deliverable_evaluation(run,'output','Missing evidence','DELIVERABLE_EVIDENCE_BLOCKED')
    fresh=get_run(run['id'])
    assert fresh['state']=='EVALUATING'
    assert fresh['analysis_json']==run['analysis_json']
    assert fresh['version']==run['version']


def test_repeat_block_is_idempotent(native_run):
    _,run,_,_=native_run
    first=engine._block_deliverable_evaluation(run,'output','First failure','DELIVERABLE_EVIDENCE_BLOCKED')
    again=engine._block_deliverable_evaluation(run,'output','Different failure','REQUIRED_ARTIFACT_MISSING')
    assert again==first
    with get_db_connection() as c:
        assert c.execute('SELECT count(*) FROM orchestration_events WHERE run_id=? AND event_type=?',
            (run['id'],'deliverable_validation_blocked')).fetchone()[0]==1


@pytest.mark.parametrize('state',['PAUSED','CANCELLED','COMPLETED'])
def test_stale_evaluation_cannot_overwrite_lifecycle(native_run,state):
    _,run,_,_=native_run
    with db_transaction() as c:
        c.execute('UPDATE orchestration_runs SET state=? WHERE id=?',(state,run['id']))
    before=get_run(run['id'])
    with pytest.raises(StaleState):
        engine._block_deliverable_evaluation(run,'output','Late result','DELIVERABLE_EVIDENCE_BLOCKED')
    assert get_run(run['id'])==before
