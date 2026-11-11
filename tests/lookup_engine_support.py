"""Declared unmanaged model/provider outputs; extraction and storage stay real."""
import json
import re

from tests.test_spacy_service import MockSpacyRequestHandler


class NounModel(MockSpacyRequestHandler):
    """Complete tokens and spacing with deterministic noun lemmas."""
    def do_POST(self):
        if self.path != '/tokenize':
            return super().do_POST()
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])).decode('utf-8'))
        nouns = {'apple': 'apple', 'apples': 'apple', 'banana': 'banana', 'bananas': 'banana',
                 'orange': 'orange', 'oranges': 'orange', 'cherry': 'cherry', 'cherries': 'cherry',
                 'fox': 'fox'}
        tokens = model_tokens(body['text'], nouns)
        payload = json.dumps({'status': 'success', 'zid': body.get('zid', ''), 'tokens': tokens}).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def model_tokens(text, nouns):
    matches = list(re.finditer(r'\w+|[^\w\s]', text))
    tokens, sentence = [], 1
    for index, match in enumerate(matches):
        word = match.group()
        end = matches[index+1].start() if index+1 < len(matches) else len(text)
        tokens.append({'word': word, 'lemma': nouns.get(word.lower(), word.lower()),
            'pos': 'NOUN' if word.lower() in nouns else 'X', 'morphology': '',
            'idx': match.start(), 'whitespace': text[match.end():end], 'sentence_index': sentence})
        if word in '.!?':
            sentence += 1
    return tokens


def translate_lookup(handler, provider, text, source, target):
    handler.server.provider_calls.append((text, source, target))
    sentences = {'I like apples.': 'Я люблю яблоки.', 'Bananas are yellow.': 'Бананы желтые.',
                 'I eat an apple.': 'Я ем яблоко.', 'Oranges are sweet.': 'Апельсины сладкие.',
                 'Cherries are red.': 'Вишни красные.', 'First sentence.': 'Первое предложение.',
                 'Second sentence.': 'Второе предложение.', 'The fox jumps.': 'Лиса прыгает.',
                 'The quick fox.': 'Очень быстрая лиса.'}
    for original, translated in sentences.items():
        text = text.replace(original, translated)
    words = {'apple': 'apple_ru', 'banana': 'banana_ru', 'orange': 'апельсин', 'cherry': 'вишня',
             'fox': 'fox_ru', 'quick': 'quick_reprocessed'}
    return re.sub(r'\b(apple|banana|orange|cherry|fox|quick)\b', lambda match: words[match.group()], text)
