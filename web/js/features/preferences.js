/** Per-key command ownership: pending intent is distinct from committed state. */
(function () {
    function flushSlots(slots) {
        var keys = Object.keys(slots);
        return Promise.all(keys.map(function(key) { return slots[key].tail; })).then(function() {
            if (keys.some(function(key) { return slots[key].phase === 'failed'; })) throw new Error('Preferences remain unsaved');
        });
    }
    function create(send, project) {
        var slots = {};
        function settle(slot, generation, key, phase, value, error) {
            if (slot.generation !== generation) return;
            slot.phase = phase;
            project(key, phase, value, error);
        }
        function submit(key, value) {
            var slot = slots[key];
            if (!slot) slot = slots[key] = {generation: 0, phase: 'idle', tail: Promise.resolve()};
            var generation = ++slot.generation;
            settle(slot, generation, key, 'pending', value);
            slot.tail = slot.tail.then(function () {
                return send(key, value, generation);
            }).then(function () {
                settle(slot, generation, key, 'saved', value);
            }, function (error) {
                settle(slot, generation, key, 'failed', value, error);
            });
            return slot.tail;
        }
        function flush() {
            return flushSlots(slots);
        }
        function current(key, generation) { return !!slots[key] && slots[key].generation === generation; }
        function unresolved(key) { return !!slots[key] && (slots[key].phase === 'pending' || slots[key].phase === 'failed'); }
        return {submit: submit, flush: flush, current: current, unresolved: unresolved};
    }
    window.PreferenceCommands = {create: create};
})();
