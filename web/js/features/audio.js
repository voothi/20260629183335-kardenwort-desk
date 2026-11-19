/* Client-owned playback. Engine, browser policy and device errors stay visible. */
(function (global) {
    'use strict';
    function AudioPlayback(options) {
        var current = null;
        var state = 'idle';
        var last = null;

        function publish(next, message) {
            state = next;
            options.onState({ state: next, message: message || '' });
        }

        function disposeMedia(owner) {
            if (owner.audio) {
                owner.audio.removeEventListener('play', owner.onPlay);
                owner.audio.removeEventListener('ended', owner.onEnded);
                owner.audio.removeEventListener('error', owner.onError);
                owner.audio.pause();
                owner.audio.removeAttribute('src');
                owner.audio.load();
                owner.audio = null;
            }
            if (owner.url) {
                URL.revokeObjectURL(owner.url);
                owner.url = null;
            }
        }

        function cancel() {
            if (!current) return;
            var owner = current;
            current = null;
            clearTimeout(owner.timeout);
            owner.request.abort();
            disposeMedia(owner);
            publish('cancelled');
        }

        function fail(owner, error) {
            if (current !== owner) return;
            current = null;
            clearTimeout(owner.timeout);
            owner.request.abort();
            disposeMedia(owner);
            var message = error.message || 'Playback or audio device failed';
            publish('failed', message);
            options.onError(message);
        }

        function validateBlob(blob) {
            if (!blob.size || blob.size > 8 * 1024 * 1024 || !/^audio\/(wav|mpeg)$/.test(blob.type)) {
                throw new Error('Synthesis returned invalid audio');
            }
        }

        function attachMedia(owner, blob) {
            if (current !== owner) return;
            validateBlob(blob);
            owner.url = URL.createObjectURL(blob);
            var audio = new Audio();
            owner.audio = audio;
            owner.onPlay = function () {
                if (current === owner && owner.audio === audio) publish('playing');
            };
            owner.onEnded = function () {
                if (current !== owner || owner.audio !== audio) return;
                disposeMedia(owner);
                nextSegment(owner);
            };
            owner.onError = function () { mediaFailed(owner, audio, new Error('Audio decode or device failure')); };
            owner.audio.addEventListener('play', owner.onPlay);
            owner.audio.addEventListener('ended', owner.onEnded);
            owner.audio.addEventListener('error', owner.onError);
            owner.audio.src = owner.url;
            startMedia(owner, audio);
        }

        function mediaFailed(owner, audio, error) {
            if (owner.audio === audio) fail(owner, error);
        }

        function startMedia(owner, audio) {
            var playing = audio.play();
            if (playing && playing.catch) playing.catch(function (error) { mediaFailed(owner, audio, error); });
        }

        function checkedBlob(response) {
            if (!response.ok) {
                return response.json().then(function (body) {
                    throw new Error(body.message || 'Audio synthesis failed (' + response.status + ')');
                });
            }
            return response.blob();
        }

        function nextSegment(owner) {
            if (owner.index === owner.segments.length) {
                current = null;
                clearTimeout(owner.timeout);
                publish('finished');
                return;
            }
            publish('loading');
            var text = owner.segments[owner.index++];
            fetch(options.url(), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-API-Token': options.token() },
                body: JSON.stringify({ text: text, language: owner.language }),
                signal: owner.request.signal
            }).then(checkedBlob).then(function (blob) {
                attachMedia(owner, blob);
            }).catch(function (error) { fail(owner, error); });
        }

        function play(text, language) {
            cancel();
            last = { text: text, language: language };
            var owner = { segments: text.split('|||').map(function (part) { return part.trim(); }).filter(Boolean),
                language: language, index: 0, request: new AbortController(), audio: null, url: null };
            current = owner;
            owner.timeout = setTimeout(function () { fail(owner, new Error('Audio request timed out')); }, 75000);
            nextSegment(owner);
        }

        this.play = play;
        this.cancel = cancel;
        this.retry = function () { if (last) play(last.text, last.language); };
        this.getState = function () { return state; };
    }

    function BrowserSpeechPlayback(options) {
        var current = null;
        var state = 'idle';
        var last = null;

        function emit(next, message) {
            state = next;
            options.onState({ state: next, message: message || '' });
        }

        function stopSpeech() {
            if (!current) return;
            current = null;
            if (global.speechSynthesis && global.speechSynthesis.cancel) global.speechSynthesis.cancel();
            emit('cancelled');
        }

        function failSpeech(owner, message) {
            if (current !== owner) return;
            current = null;
            emit('failed', message);
            options.onError(message);
        }

        function bindUtterance(owner, utterance) {
            utterance.onstart = function () {
                if (current === owner) emit('playing');
            };
            utterance.onend = function () {
                if (current === owner) speakNext(owner);
            };
            utterance.onerror = function (event) {
                var code = event && event.error;
                if (code === 'canceled' || code === 'interrupted') return;
                failSpeech(owner, code || 'Browser speech synthesis failed');
            };
        }

        function speakNext(owner) {
            if (owner.index === owner.segments.length) {
                current = null;
                emit('finished');
                return;
            }
            emit('loading');
            var utterance = new global.SpeechSynthesisUtterance(owner.segments[owner.index++]);
            utterance.lang = owner.language;
            bindUtterance(owner, utterance);
            global.speechSynthesis.speak(utterance);
        }

        function startSpeech(text, language) {
            stopSpeech();
            last = { text: text, language: language };
            var segments = text.split('|||').map(function (part) { return part.trim(); }).filter(Boolean);
            var owner = { segments: segments, language: language, index: 0 };
            current = owner;
            if (!global.speechSynthesis || !global.SpeechSynthesisUtterance) {
                failSpeech(owner, 'Browser speech synthesis is unavailable');
                return;
            }
            speakNext(owner);
        }

        this.play = startSpeech;
        this.cancel = stopSpeech;
        this.retry = function () { if (last) startSpeech(last.text, last.language); };
        this.getState = function () { return state; };
    }

    global.DeskAudioPlayback = AudioPlayback;
    global.DeskBrowserSpeechPlayback = BrowserSpeechPlayback;
}(window));
