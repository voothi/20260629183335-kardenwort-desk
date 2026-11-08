/** Per-key command ownership: pending intent is distinct from committed state. */
(function () {
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
                return send(key, value);
            }).then(function () {
                settle(slot, generation, key, 'saved', value);
            }, function (error) {
                settle(slot, generation, key, 'failed', value, error);
            });
            return slot.tail;
        }
        return {submit: submit};
    }
    window.PreferenceCommands = {create: create};
})();
