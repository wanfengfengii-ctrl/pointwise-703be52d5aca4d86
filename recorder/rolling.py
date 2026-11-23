import json

from recorder.events import Origin, ReadingEvent, fact_to_record, require_beat
from recorder.pages import layout_from_row, page_row
from recorder.slots import active_binding, occupy_slot
from rules.calibration import decode_fraction
from rules.channels import require_channel, require_identifier


def is_rolling_page(store, page_id):
    row = store.connection.execute(
        'SELECT 1 FROM device_rolling_pages WHERE page_id = ?', (page_id,),
    ).fetchone()
    return row is not None


def page_kind(store, page_id):
    row = store.connection.execute(
        'SELECT 1 FROM device_pages WHERE page_id = ?', (page_id,),
    ).fetchone()
    if row is None:
        return None
    return 'rolling' if is_rolling_page(store, page_id) else 'normal'


def _cover_notification(db, row):
    layout = layout_from_row(row)
    events = db.execute(
        'SELECT * FROM device_events WHERE page_id = ? ORDER BY sequence', (layout.page_id,),
    ).fetchall()
    facts = [
        {
            'origin': Origin(layout.page_id, layout.slot, event['sequence'], int(event['beat'])).to_record(),
            'channel': event['channel'],
            'kind': event['kind'],
        }
        for event in events
    ]
    closed = Origin(layout.page_id, layout.slot, row['closed_sequence'], None).to_record()
    tickets = [
        {'ticket': ticket['ticket'], 'page_id': ticket['page_id'],
         'beat': ticket['beat'], 'channel': ticket['channel']}
        for ticket in db.execute(
            'SELECT ticket, page_id, beat, channel FROM device_dispatches WHERE page_id = ? ORDER BY rowid',
            (layout.page_id,),
        ).fetchall()
    ]
    return {
        'kind': 'cover', 'page_id': layout.page_id,
        'body': {
            'layout': layout.to_record(), 'facts': facts,
            'closed': closed, 'tickets': tickets,
        },
    }


def open_rolling(store, layout):
    """Open a rolling page, covering the sealed rolling page that holds the slot."""
    with store.transaction() as db:
        if db.execute('SELECT 1 FROM device_pages WHERE page_id = ?', (layout.page_id,)).fetchone():
            raise ValueError(f'page ID {layout.page_id!r} was already used')
        binding = active_binding(store, layout.slot)
        notification = None
        if binding is not None:
            covered = page_row(db, binding.page_id)
            if not is_rolling_page(store, binding.page_id):
                raise ValueError(f'slot {layout.slot!r} holds a page outside the rolling flow')
            if covered['closed_sequence'] is None:
                raise ValueError('only a sealed rolling page can be covered')
            notification = _cover_notification(db, covered)
        db.execute(
            'INSERT INTO device_pages(page_id, layout) VALUES (?, ?)',
            (layout.page_id, json.dumps(layout.to_record())),
        )
        db.execute('INSERT INTO device_rolling_pages(page_id) VALUES (?)', (layout.page_id,))
        if binding is None:
            occupy_slot(store, layout.slot, layout.page_id)
        else:
            changed = db.execute(
                'UPDATE device_slots SET page_id = ? WHERE slot = ? AND page_id = ?',
                (layout.page_id, layout.slot, binding.page_id),
            ).rowcount
            if changed != 1:
                raise ValueError('physical slot no longer contains the covered page')
            db.execute('DELETE FROM device_events WHERE page_id = ?', (binding.page_id,))
            db.execute(
                'INSERT INTO device_covers(page_id, notification) VALUES (?, ?)',
                (binding.page_id, json.dumps(notification)),
            )
    return {'layout': layout.to_record(), 'cover': notification}


def dispatch(store, ticket, page_id, beat, channel):
    """Accept a ticket for one recorded reading on a still-readable rolling page."""
    require_identifier(ticket, 'ticket')
    require_identifier(page_id, 'page_id')
    beat = require_beat(beat)
    channel = require_channel(channel)
    with store.transaction() as db:
        existing = db.execute(
            'SELECT page_id, beat, channel FROM device_dispatches WHERE ticket = ?', (ticket,),
        ).fetchone()
        if existing is not None:
            bound = (existing['page_id'], existing['beat'], existing['channel'])
            if bound == (page_id, beat, channel.value):
                return None
            raise ValueError('ticket was already accepted with different parameters')
        row = page_row(db, page_id)
        if not is_rolling_page(store, page_id):
            raise ValueError('dispatch is only available on rolling pages')
        if db.execute('SELECT 1 FROM device_covers WHERE page_id = ?', (page_id,)).fetchone():
            raise ValueError(f'page {page_id!r} was covered and can no longer dispatch')
        event = db.execute(
            'SELECT * FROM device_events WHERE page_id = ? AND beat = ? AND channel = ?',
            (page_id, str(beat), channel.value),
        ).fetchone()
        if event is None:
            raise ValueError(f'no recorded fact for beat {beat} on {channel.value}')
        if event['kind'] != 'reading':
            raise ValueError('only a recorded reading can be dispatched')
        layout = layout_from_row(row)
        origin = Origin(page_id, layout.slot, event['sequence'], beat)
        fact = ReadingEvent(origin, channel, decode_fraction(json.loads(event['raw'])))
        db.execute(
            'INSERT INTO device_dispatches(ticket, page_id, beat, channel) VALUES (?, ?, ?, ?)',
            (ticket, page_id, beat, channel.value),
        )
        packet = {'ticket': ticket, 'fact': fact_to_record(fact)}
    return packet
