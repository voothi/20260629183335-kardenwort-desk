"""Acceptance schedules missing from the earlier release evidence."""
import pytest
import kardenwort_desk as desk
from kardenwort_events import EventDispatcher
from tests.test_release_completion_regressions import completion
from tests.test_canonical_pipeline import world
from tests.reword_contract_support import ZID, ROWS, backend, provider_name


def test_accepted_retry_after_final_read_cannot_be_overwritten(world):
    world.seed(ZID, ROWS)
    original = world.adapter
    raced = []

    class RetryAfterRead(desk.SqliteStorageAdapter):
        def restore_session(self, identity, **kwargs):
            restored = super().restore_session(identity, **kwargs)
            if not raced:
                raced.append(True)
                world.arbiter.retry_session_rows(identity,
                    target_coordinates=[dict(sentence_idx=1, token_order=0)])
            return restored

    adapter = RetryAfterRead(world.config, world.paths)
    before = original.restore_session(ZID)
    mapping = desk.load_anki_mapping(world.paths['anki_mapping_file'])
    roles = desk.get_role_fields(mapping, before['headers'])
    dispatcher = EventDispatcher.get_instance()
    events = dispatcher.register(ZID)
    try:
        world.arbiter.enrichment_queue._publish_progressive_final_state(
            world.arbiter, adapter, ZID, True, ZID, 'de', before['headers'], before['data_rows'],
            roles, mapping, None, None, 'acceptance:retry', {}, None)
        final = []
        while not events.empty():
            event = events.get_nowait()
            if event.get('stage') == 'finished': final.append(event)
        assert final[-1]['rows']['0']['trans'] == 'fresh-Haus'
        cached = world.arbiter.sessions[ZID]
        assert cached['data_rows'][0][cached['headers'].index('WordDestination')] == 'fresh-Haus'
        assert world.values()[(1, 0)] == 'fresh-Haus'
    finally:
        dispatcher.unregister(ZID, events)


def test_delete_after_final_read_cannot_publish_success(completion):
    db, arbiter, zid, arguments = completion
    original = arguments['storage_adapter']
    deleted = []

    class DeleteAfterRead(desk.SqliteStorageAdapter):
        def restore_session(self, identity, **kwargs):
            restored = super().restore_session(identity, **kwargs)
            if not deleted:
                deleted.append(True)
                db.soft_delete_session(identity)
            return restored

    arguments['storage_adapter'] = DeleteAfterRead(original.config, original.resolved_paths, db_path=db.db_path)
    dispatcher = EventDispatcher.get_instance()
    events = dispatcher.register(zid)
    try:
        arbiter.enrichment_queue._publish_progressive_final_state(**arguments)
        final = events.get_nowait()
        assert final['status'] == 'failed'
        assert final['rows'] == {}
        assert zid not in arbiter.sessions
    finally:
        dispatcher.unregister(zid, events)


def test_generation_replaced_after_read_cannot_publish(completion):
    _, arbiter, zid, arguments = completion
    queue = arbiter.enrichment_queue
    queue._progressive_generations[zid] = 1
    original = arguments['storage_adapter']

    class SupersedeAfterRead(desk.SqliteStorageAdapter):
        def restore_session(self, identity, **kwargs):
            restored = super().restore_session(identity, **kwargs)
            with queue._lock:
                queue._progressive_generations[zid] = 2
            return restored

    arguments.update(storage_adapter=SupersedeAfterRead(original.config, original.resolved_paths,
        db_path=original.db.db_path), generation=1)
    dispatcher = EventDispatcher.get_instance()
    events = dispatcher.register(zid)
    try:
        queue._publish_progressive_final_state(**arguments)
        assert events.empty()
    finally:
        dispatcher.unregister(zid, events)


def test_publication_acceptance_excludes_an_independent_writer(completion):
    import sqlite3
    from kardenwort_db import KardenwortDB
    db, arbiter, zid, arguments = completion
    contender = KardenwortDB(db_path=db.db_path, busy_timeout_ms=1)
    word = db.get_words_by_session(zid)[0]
    attempted = []

    class CompetingWriter:
        def publish(self, event):
            assert contender.get_worker_status(zid)['worker_status'] == 'finished'
            with pytest.raises(sqlite3.OperationalError, match='locked'):
                contender.update_word(word['id'], dict(word_destination='competing'))
            attempted.append(True)

    dispatcher = EventDispatcher.get_instance()
    subscriber = CompetingWriter()
    with dispatcher._lock:
        dispatcher._subscribers.setdefault(zid, []).append(subscriber)
    try:
        arbiter.enrichment_queue._publish_progressive_final_state(**arguments)
    finally:
        dispatcher.unregister(zid, subscriber)
    assert attempted == [True]
    assert db.get_words_by_session(zid)[0]['word_destination'] == 'old destination'
    assert contender.update_word(word['id'], dict(word_destination='accepted afterwards'))
    assert db.get_words_by_session(zid)[0]['word_destination'] == 'accepted afterwards'


def test_continuously_invalidated_completion_fails_with_recovery(completion):
    db, arbiter, zid, arguments = completion
    original = arguments['storage_adapter']
    attempts = []

    class BusyWriter(desk.SqliteStorageAdapter):
        def restore_session(self, identity, **kwargs):
            restored = super().restore_session(identity, **kwargs)
            attempts.append(True)
            db.batch_update_words(zid, [dict(sentence_index=1, token_order=0,
                updates=dict(word_destination='write-' + str(len(attempts))))])
            return restored

    arguments['storage_adapter'] = BusyWriter(original.config, original.resolved_paths, db_path=db.db_path)
    dispatcher = EventDispatcher.get_instance()
    events = dispatcher.register(zid)
    try:
        with pytest.raises(desk.StructuredError, match='changed during publication') as caught:
            arbiter.enrichment_queue._publish_progressive_final_state(**arguments)
        assert len(attempts) == 3
        assert caught.value.details['committed'] is True
        assert caught.value.details['recovery_url'].endswith('&committed=1')
        assert db.get_worker_status(zid)['worker_status'] == 'failed'
        assert db.get_words_by_session(zid)[0]['word_destination'] == 'write-3'
        assert events.empty()
    finally:
        dispatcher.unregister(zid, events)
