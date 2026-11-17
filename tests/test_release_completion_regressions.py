"""Real SQLite completion snapshots with controlled storage/transport schedules."""
import pytest

import kardenwort_desk as desk
from kardenwort_events import EventDispatcher
from tests.test_progressive import _seed_stage3_session


@pytest.fixture
def completion(tmp_path):
    db, config, paths, arbiter, zid = _seed_stage3_session(tmp_path, 'completion_review')
    db.batch_update_words(zid, [dict(sentence_index=1, token_order=0,
        updates=dict(word_destination='old destination'))])
    adapter = desk.SqliteStorageAdapter(config=config, resolved_paths=paths, db_path=db.db_path)
    restored = adapter.restore_session(zid)
    mapping = desk.load_anki_mapping(paths['anki_mapping_file'])
    roles = desk.get_role_fields(mapping, restored['headers'])
    arguments = dict(arbiter=arbiter, storage_adapter=adapter, session_zid=zid,
        is_sqlite=True, tsv_path=None, sess_lang='de', headers=restored['headers'],
        data_rows=restored['data_rows'], role_fields=roles, mapping=mapping,
        active_text_prov=None, worker_error=None, eff_trace_id='review:finished',
        sess_row_provs={}, generation=None)
    try:
        yield db, arbiter, zid, arguments
    finally:
        arbiter.enrichment_queue.shutdown()
        desk.unregister_failover_callback(arbiter._on_global_failover)


def test_completion_metadata_uses_the_same_committed_bundle_as_rows(completion):
    db, arbiter, zid, arguments = completion
    original = arguments['storage_adapter']

    class CompetingCommitAdapter(desk.SqliteStorageAdapter):
        def restore_session(self, identity, **kwargs):
            restored = super().restore_session(identity, **kwargs)
            # A separate real connection commits after the atomic read completes.
            db.batch_update_words(zid, [dict(sentence_index=1, token_order=0,
                updates=dict(word_destination='new destination', word_provenance='live:deepl'))])
            return restored

    scheduled = CompetingCommitAdapter(config=original.config,
        resolved_paths=original.resolved_paths, db_path=db.db_path)
    arguments['storage_adapter'] = scheduled
    dispatcher = EventDispatcher.get_instance()
    channel = dispatcher.register(zid)
    try:
        arbiter.enrichment_queue._publish_progressive_final_state(**arguments)
        event = channel.get_nowait()
        row = event['rows']['0']
        assert (row['trans'], row['provenance']) in {
            ('old destination', 'live:google'), ('new destination', 'live:deepl')}
        assert db.get_words_by_session(zid)[0]['word_destination'] == 'new destination'
    finally:
        dispatcher.unregister(zid, channel)


@pytest.mark.parametrize('status_failure', [False, True])
@pytest.mark.parametrize('deleted', [False, True])
def test_failed_terminal_delivery_remains_unobserved_until_reported(completion, status_failure, deleted):
    db, arbiter, zid, arguments = completion
    arguments['worker_error'] = dict(code='ERR_PROVIDER', message='owned provider failure')
    if deleted:
        assert db.soft_delete_session(zid)
    if status_failure:
        with db.get_connection() as connection:
            connection.execute("CREATE TRIGGER reject_terminal_status BEFORE UPDATE OF worker_status ON sessions "
                "BEGIN SELECT RAISE(ABORT, 'owned terminal status failure'); END")

    class UnavailableTransport:
        def publish(self, payload):
            raise ConnectionError('owned terminal delivery failure')

    dispatcher = EventDispatcher.get_instance()
    sink = UnavailableTransport()
    with dispatcher._lock:
        dispatcher._subscribers.setdefault(zid, []).append(sink)
    try:
        with pytest.raises((desk.StructuredError, BaseExceptionGroup)) as caught:
            arbiter.enrichment_queue._publish_progressive_final_state(**arguments)
        errors = caught.value.exceptions if isinstance(caught.value, BaseExceptionGroup) else [caught.value]
        delivery = [error for error in errors if isinstance(error, desk.StructuredError)]
        assert len(delivery) == 1
        assert isinstance(delivery[0].__cause__, ConnectionError)
        assert delivery[0].details['session_zid'] == zid
        assert not arbiter.enrichment_queue._acknowledged_failures
        if status_failure:
            assert any('owned terminal status failure' in str(error) for error in errors)
    finally:
        dispatcher.unregister(zid, sink)
