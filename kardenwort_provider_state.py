"""SQLite coordination for configured provider policy across controller/CLI processes."""


def cooldowns(db):
    with db.get_connection(read_only=True) as connection:
        return dict(connection.execute('SELECT provider_key, expires_at FROM provider_cooldowns'))


def set_cooldown(db, key, expires_at):
    with db.transaction() as connection:
        connection.execute('INSERT INTO provider_cooldowns VALUES (?, ?) '
                           'ON CONFLICT(provider_key) DO UPDATE SET expires_at=excluded.expires_at',
                           (key, expires_at))


def remaining(db, provider, task, now):
    values = cooldowns(db)
    keys = [provider, f'{task}:{provider}'] if task else [provider, f'text:{provider}', f'lemma:{provider}']
    return max([0.0, *(values.get(key, 0.0) - now for key in keys)])


def providers(db, zid):
    with db.get_connection(read_only=True) as connection:
        return dict(connection.execute('SELECT field, value FROM session_provider_state WHERE session_zid=?', (zid,)))


def set_providers(db, zid, values):
    with db.transaction() as connection:
        connection.executemany('INSERT INTO session_provider_state VALUES (?, ?, ?) '
                               'ON CONFLICT(session_zid, field) DO UPDATE SET value=excluded.value',
                               [(zid, field, value) for field, value in values.items()])


def clear(db):
    with db.transaction() as connection:
        connection.execute('DELETE FROM provider_cooldowns')
        connection.execute('DELETE FROM session_provider_state')
