"""Committed Re-word facts survive initial rendering, status and owned export."""
import json
import re
from http.client import HTTPConnection

import pytest

import kardenwort_desk as desk
from tests.reword_contract_support import ZID, TOKEN, backend, provider_name
from tests.release_projection_support import checked_world as world
from tests.test_reword_projection_history import occurrence_world, row_request


@pytest.mark.parametrize('provider_name', ['google', 'intellifiller'])
def test_generated_cards_use_complete_provenance_identity(world, provider_name):
    occurrence_world(world)
    status, payload = world.post(row_request(world))
    assert status == 200, payload
    world.config.add_section('rendering')
    world.config.set('rendering', 'filter_selected_only', 'false')
    world.config.set('sentences_mode', 'enabled', 'true')
    before = world.snapshot()
    rendered = desk.run_render_flow(text='Haus und Haus. Baum.', language='de', zid=ZID,
        text_mode='single', config=world.config, resolved_paths=world.paths,
        tsv_path=str(world.path(ZID)), spawn_children=False)
    match = re.search(r'<script id="sentence-cards" type="application/json">(.*?)</script>', rendered, re.S)
    assert match
    cards = json.loads(match.group(1))
    observed = {str(card['sentence_idx']): {word['lemma']: word['provenance']
                for word in card['words']} for card in cards}
    assert observed['1']['Haus'] == 'live:' + provider_name
    assert observed['2']['Baum'] == 'fixture:initial'
    assert observed['0'] == {'Haus': 'live:' + provider_name, 'Baum': 'fixture:initial'}
    assert world.snapshot() == before


@pytest.mark.parametrize('provider_name', ['google', 'intellifiller'])
def test_status_and_owned_export_after_contextual_row_reword(world, provider_name):
    occurrence_world(world)
    status, payload = world.post(row_request(world))
    assert status == 200, payload
    connection = HTTPConnection('127.0.0.1', world.port, timeout=10)
    try:
        connection.request('GET', '/session/status?zid=' + ZID, headers={'X-API-Token': TOKEN})
        response = connection.getresponse()
        body = json.load(response)
        assert response.status == 200, body
    finally:
        connection.close()
    rows = body.get('data', body)['rows']
    assert rows['0']['trans'] == 'fresh-Haus'
    assert rows['0']['provenance'] == 'live:' + provider_name
    assert rows['1']['trans'] == 'tree'
    assert rows['1']['provenance'] == 'fixture:initial'
    world.paths['favorites_output_dir'] = world.owner / 'owned-export'
    result = world.adapter.export_favorites(ZID, selected_row_ids=[0],
        save_to_favorites_override=True, send_to_anki_override=False, language='de')
    assert result['import_complete'] is True
    files = list(world.paths['favorites_output_dir'].glob('*.tsv'))
    assert len(files) == 1
    _, headers, exported = desk.load_tsv_rows(files[0])
    assert len(exported) == 1
    assert exported[0][headers.index('WordDestination')] == 'fresh-Haus'
