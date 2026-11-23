from dataclasses import dataclass
import json

from recorder.events import MissingEvent, Origin, PageClosed, ReadingEvent, require_beat
from recorder.slots import occupy_slot
from recorder.store import DeviceStore
from rules.calibration import decode_fraction, encode_fraction
from rules.channels import CHANNELS, require_channel, require_identifier
from rules.readings import parse_raw_reading


class PageCleared(LookupError):
    pass


@dataclass(frozen=True)
class PageLayout:
    page_id: str
    slot: str
    shift_id: str
    revision: str
    beats: tuple[int, ...]

    def __post_init__(self):
        for name in ('page_id', 'slot', 'shift_id', 'revision'):
            require_identifier(getattr(self, name), name)
        beats = tuple(require_beat(beat) for beat in self.beats)
        if not beats or len(set(beats)) != len(beats):
            raise ValueError('page layout needs distinct beats in device order')
        object.__setattr__(self, 'beats', beats)

    def to_record(self):
        return {
            'page_id': self.page_id, 'slot': self.slot, 'shift_id': self.shift_id,
            'revision': self.revision, 'beats': list(self.beats),
        }

    @classmethod
    def from_record(cls, data):
        return cls(**data)


@dataclass(frozen=True)
class PhysicalPage:
    layout: PageLayout
    facts: tuple[ReadingEvent | MissingEvent, ...]
    closed: PageClosed | None


def open_page(store: DeviceStore, layout: PageLayout):
    with store.transaction() as db:
        if db.execute('SELECT 1 FROM device_pages WHERE page_id = ?', (layout.page_id,)).fetchone():
            raise ValueError(f'page ID {layout.page_id!r} was already used')
        db.execute(
            'INSERT INTO device_pages(page_id, layout) VALUES (?, ?)',
            (layout.page_id, json.dumps(layout.to_record())),
        )
        occupy_slot(store, layout.slot, layout.page_id)
    return layout


def page_row(db, page_id):
    require_identifier(page_id, 'page_id')
    row = db.execute('SELECT * FROM device_pages WHERE page_id = ?', (page_id,)).fetchone()
    if row is None:
        raise KeyError(f'unknown device page {page_id!r}')
    return row


def layout_from_row(row):
    return PageLayout.from_record(json.loads(row['layout']))


def page_status(store: DeviceStore, page_id):
    row = page_row(store.connection, page_id)
    if row['cleared']:
        return 'cleared'
    if row['closed_sequence'] is not None:
        return 'closed'
    return 'recording'


def _closed_event(layout, sequence):
    if sequence is None:
        return None
    return PageClosed(Origin(layout.page_id, layout.slot, sequence, None))


def _fact_from_row(layout, row):
    origin = Origin(layout.page_id, layout.slot, row['sequence'], int(row['beat']))
    channel = require_channel(row['channel'])
    if row['kind'] == 'missing':
        return MissingEvent(origin, channel)
    return ReadingEvent(origin, channel, decode_fraction(json.loads(row['raw'])))


def read_page(store: DeviceStore, page_id):
    with store.transaction(write=False) as db:
        row = page_row(db, page_id)
        if row['cleared']:
            raise PageCleared(page_id)
        layout = layout_from_row(row)
        events = db.execute(
            'SELECT * FROM device_events WHERE page_id = ? ORDER BY sequence', (page_id,),
        )
        facts = tuple(_fact_from_row(layout, event) for event in events)
        return PhysicalPage(layout, facts, _closed_event(layout, row['closed_sequence']))


def _close(db, row, layout):
    if row['closed_sequence'] is not None:
        return _closed_event(layout, row['closed_sequence'])
    count, last = db.execute(
        'SELECT count(*), coalesce(max(sequence), 0) FROM device_events WHERE page_id = ?',
        (layout.page_id,),
    ).fetchone()
    if count != len(layout.beats) * len(CHANNELS):
        raise ValueError('cannot close a page with unresolved beats')
    sequence = last + 1
    db.execute('UPDATE device_pages SET closed_sequence = ? WHERE page_id = ?', (sequence, layout.page_id))
    return _closed_event(layout, sequence)


def _feed_fact(db, row, layout, kind, beat, channel, raw):
    if beat not in layout.beats:
        raise ValueError(f'beat {beat} is outside the device layout')
    key = (layout.page_id, str(beat), channel.value)
    existing = db.execute(
        'SELECT * FROM device_events WHERE page_id = ? AND beat = ? AND channel = ?', key,
    ).fetchone()
    if existing is not None:
        event = _fact_from_row(layout, existing)
        if existing['kind'] == kind and getattr(event, 'raw', None) == raw:
            return event
        raise ValueError('conflicting fact for the same page, beat, and channel')
    if row['closed_sequence'] is not None:
        raise ValueError('page is already closed')
    sequence = db.execute(
        'SELECT coalesce(max(sequence), 0) + 1 FROM device_events WHERE page_id = ?',
        (layout.page_id,),
    ).fetchone()[0]
    stored_raw = None if raw is None else json.dumps(encode_fraction(raw))
    db.execute('''
        INSERT INTO device_events(page_id, beat, channel, sequence, kind, raw)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (*key, sequence, kind, stored_raw))
    origin = Origin(layout.page_id, layout.slot, sequence, beat)
    return MissingEvent(origin, channel) if kind == 'missing' else ReadingEvent(origin, channel, raw)


def feed(store: DeviceStore, page_id, kind, *, beat=None, channel=None, raw=None):
    if kind == 'close':
        if any(value is not None for value in (beat, channel, raw)):
            raise ValueError('close accepts only a page ID')
    elif kind in ('reading', 'missing'):
        beat, channel = require_beat(beat), require_channel(channel)
        if kind == 'reading':
            raw = parse_raw_reading(raw)
        elif raw is not None:
            raise ValueError('explicit missing has no raw value')
    else:
        raise ValueError(f'unknown event kind {kind!r}')
    with store.transaction() as db:
        row = page_row(db, page_id)
        if row['cleared']:
            raise PageCleared(page_id)
        layout = layout_from_row(row)
        if kind == 'close':
            return _close(db, row, layout)
        return _feed_fact(db, row, layout, kind, beat, channel, raw)
