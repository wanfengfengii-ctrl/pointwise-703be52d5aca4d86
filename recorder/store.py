from contextlib import contextmanager
import sqlite3

from rules.channels import require_identifier


DEFAULT_SLOTS = ('S1', 'S2')


def open_connection(path):
    connection = sqlite3.connect(path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys = ON')
    return connection


@contextmanager
def transaction(connection, *, write=True):
    connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


class DeviceStore:
    def __init__(self, connection):
        self.connection = connection

    def transaction(self, *, write=True):
        return transaction(self.connection, write=write)

    def close(self):
        self.connection.close()


def initialize_device_store(store, slots=DEFAULT_SLOTS):
    with store.transaction() as db:
        db.execute('''
            CREATE TABLE IF NOT EXISTS device_pages (
                page_id TEXT PRIMARY KEY,
                layout TEXT NOT NULL,
                closed_sequence INTEGER,
                cleared INTEGER NOT NULL DEFAULT 0 CHECK (cleared IN (0, 1))
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS device_slots (
                slot TEXT PRIMARY KEY,
                page_id TEXT UNIQUE REFERENCES device_pages(page_id)
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS device_events (
                page_id TEXT NOT NULL REFERENCES device_pages(page_id),
                beat TEXT NOT NULL,
                channel TEXT NOT NULL CHECK (channel IN ('vibration', 'temperature')),
                sequence INTEGER NOT NULL,
                kind TEXT NOT NULL,
                raw TEXT,
                PRIMARY KEY (page_id, beat, channel),
                UNIQUE (page_id, sequence),
                CHECK ((kind = 'reading' AND raw IS NOT NULL)
                    OR (kind = 'missing' AND raw IS NULL))
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS device_releases (
                request_id TEXT PRIMARY KEY,
                page_id TEXT NOT NULL UNIQUE REFERENCES device_pages(page_id),
                slot TEXT NOT NULL REFERENCES device_slots(slot)
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS device_rolling_pages (
                page_id TEXT PRIMARY KEY REFERENCES device_pages(page_id)
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS device_dispatches (
                ticket TEXT PRIMARY KEY,
                page_id TEXT NOT NULL REFERENCES device_pages(page_id),
                beat INTEGER NOT NULL,
                channel TEXT NOT NULL CHECK (channel IN ('vibration', 'temperature'))
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS device_covers (
                page_id TEXT PRIMARY KEY REFERENCES device_pages(page_id),
                notification TEXT NOT NULL
            )
        ''')
        if db.execute('SELECT 1 FROM device_slots LIMIT 1').fetchone() is None:
            names = tuple(require_identifier(slot, 'slot') for slot in slots)
            if not names or len(set(names)) != len(names):
                raise ValueError('device needs distinct physical slots')
            db.executemany('INSERT INTO device_slots(slot) VALUES (?)', [(name,) for name in names])


def open_device_store(path, slots=DEFAULT_SLOTS):
    store = DeviceStore(open_connection(path))
    try:
        initialize_device_store(store, slots)
    except BaseException:
        store.close()
        raise
    return store
