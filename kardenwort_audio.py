"""Pure audio request and bounded media contracts. No playback or state ownership."""
import io
import re
import wave

MAX_TEXT = 2000
MAX_BYTES = 8 * 1024 * 1024


class AudioError(ValueError):
    def __init__(self, message, code='SERVER_ERROR'):
        super().__init__(message)
        self.code = code


def audio_request(body):
    if not isinstance(body, dict):
        raise AudioError('Audio payload must be an object', 'INVALID_PAYLOAD')
    text, language = body.get('text'), body.get('language', body.get('lang'))
    checked_text(text)
    if not isinstance(language, str) or not re.fullmatch(r'[a-zA-Z]{2,3}(?:[-_][a-zA-Z0-9]{2,8})*', language):
        raise AudioError('Invalid audio language', 'INVALID_PAYLOAD')
    return text.strip(), language


def checked_text(text):
    if not isinstance(text, str) or not text.strip():
        raise AudioError('Non-empty audio text is required', 'MISSING_FIELD')
    if len(text) > MAX_TEXT or '\x00' in text:
        raise AudioError('Audio text exceeds limits or contains NUL', 'INVALID_PAYLOAD')


def wav_type(data):
    try:
        with wave.open(io.BytesIO(data), 'rb') as media:
            count = media.getnframes()
            expected = count * media.getnchannels() * media.getsampwidth()
            if count <= 0 or len(media.readframes(count)) != expected:
                raise AudioError('Empty or truncated PCM audio')
    except (wave.Error, EOFError) as error:
        raise AudioError('Malformed PCM audio') from error
    return 'audio/wav'


def mp3_header(header):
    version, layer = (header >> 19) & 3, (header >> 17) & 3
    bitrate, rate = (header >> 12) & 15, (header >> 10) & 3
    if header >> 21 != 0x7ff or version == 1 or layer != 1:
        raise AudioError('Invalid MPEG Layer III frame')
    if bitrate in (0, 15) or rate == 3:
        raise AudioError('Invalid MPEG bitrate/sample rate')
    return version, bitrate, rate


def mp3_frame_size(header):
    version, bitrate, rate = mp3_header(header)
    rates = (44100, 48000, 32000)
    bitrates = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
    if version != 3:
        bitrates = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)
    sample_rate = rates[rate] // {3: 1, 2: 2, 0: 4}[version]
    return (144 if version == 3 else 72) * bitrates[bitrate] * 1000 // sample_rate + ((header >> 9) & 1)


def id3_offset(data):
    if not data.startswith(b'ID3'):
        return 0
    if len(data) < 10 or any(byte & 0x80 for byte in data[6:10]):
        raise AudioError('Invalid ID3 header')
    size = 10 + sum(byte << (7 * (3-i)) for i, byte in enumerate(data[6:10]))
    return size + 10 if data[5] & 0x10 else size


def mp3_type(data):
    offset = id3_offset(data)
    frames = 0
    end = len(data) - 128 if data[-128:-125] == b'TAG' else len(data)
    while offset + 4 <= end:
        offset += mp3_frame_size(int.from_bytes(data[offset:offset+4], 'big'))
        frames += 1
    if not frames or offset != end:
        raise AudioError('Empty or truncated MPEG audio')
    return 'audio/mpeg'


def media_type(data):
    if not data or len(data) > MAX_BYTES:
        raise AudioError('Audio is empty or exceeds byte limit')
    if data.startswith(b'RIFF'):
        return wav_type(data)
    return mp3_type(data)
