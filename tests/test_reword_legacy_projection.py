"""Legacy read projections resolve through the active card without cross-sentence guessing."""
import pytest

from tests.reword_contract_support import backend, provider_name, world
from tests.test_reword_desktop_persistence import open_generated_desktop


@pytest.mark.parametrize('global_update', [False, True])
def test_repeated_legacy_token_updates_only_its_resolved_card(page, world, global_update):
    errors = open_generated_desktop(page, world, 'Haus. Baum.')
    try:
        page.evaluate('WorkspaceTabs.switchToTab(3)')
        snapshot = world.snapshot()
        page.evaluate('globalUpdate => receiveUpdate({is_global: globalUpdate, rows: {"0": '
                      '{token_order: 0, lemma: "Baum", trans: "legacy-tree"}}})', global_update)
        cards = page.evaluate('WorkspaceTabs.getCards().map(c => ({sentence:c.sentence_idx, '
                              'words:c.words.map(w => ({lemma:w.lemma,translation:w.translation}))}))')
        first = next(card for card in cards if card['sentence'] == 1)
        second = next(card for card in cards if card['sentence'] == 2)
        overview = next(card for card in cards if card['sentence'] == 0)
        assert first['words'][0]['translation'] == 'house'
        expected = 'tree' if global_update else 'legacy-tree'
        assert second['words'][0]['translation'] == expected
        assert {word['lemma']: word['translation'] for word in overview['words']} == {'Haus': 'house', 'Baum': expected}
        assert world.snapshot() == snapshot
        assert errors == []
    finally:
        page.close()


@pytest.mark.parametrize('explicit_row', [True, False])
def test_legacy_row_identity_and_duplicate_token_batches_remain_distinct(page, world, explicit_row):
    errors = open_generated_desktop(page, world, 'Haus. Baum.')
    try:
        page.evaluate('WorkspaceTabs.switchToTab(3)')
        snapshot = world.snapshot()
        rows = {'1': dict(row_id=1, token_order=0, lemma='Baum', trans='legacy-tree')} if explicit_row else {
            '0': dict(token_order=0, lemma='Haus', trans='wrong-house'),
            '1': dict(token_order=0, lemma='Baum', trans='last-result')}
        page.evaluate('rows => receiveUpdate({rows})', rows)
        cards = page.evaluate('WorkspaceTabs.getCards().map(c => ({sentence:c.sentence_idx, '
                              'words:c.words.map(w => ({lemma:w.lemma,translation:w.translation}))}))')
        first = next(card for card in cards if card['sentence'] == 1)
        second = next(card for card in cards if card['sentence'] == 2)
        overview = next(card for card in cards if card['sentence'] == 0)
        assert first['words'][0]['translation'] == 'house'
        expected = 'legacy-tree' if explicit_row else 'tree'
        assert second['words'][0]['translation'] == expected
        assert {word['lemma']: word['translation'] for word in overview['words']} == {'Haus': 'house', 'Baum': expected}
        assert world.snapshot() == snapshot
        assert errors == []
    finally:
        page.close()


def test_legacy_display_row_key_remains_authoritative_when_token_numbers_repeat(page, world):
    errors = open_generated_desktop(page, world, 'Haus. Baum.')
    try:
        page.evaluate('WorkspaceTabs.switchToTab(1)')
        page.evaluate('receiveUpdate({is_global:true, rows:{"0":{token_order:0,trans:"updated-house"}}})')
        cards = page.evaluate('WorkspaceTabs.getCards().map(c => ({sentence:c.sentence_idx, '
                              'words:c.words.map(w => ({lemma:w.lemma,translation:w.translation}))}))')
        overview = next(card for card in cards if card['sentence'] == 0)
        assert {word['lemma']: word['translation'] for word in overview['words']} == {'Haus': 'updated-house', 'Baum': 'tree'}
        assert next(card for card in cards if card['sentence'] == 1)['words'][0]['translation'] == 'updated-house'
        assert next(card for card in cards if card['sentence'] == 2)['words'][0]['translation'] == 'tree'
        assert errors == []
    finally:
        page.close()
