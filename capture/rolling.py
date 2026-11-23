import json

from recorder.events import Origin, ReadingEvent, fact_from_record, require_beat
from recorder.pages import PageLayout
from recorder.store import transaction
from rules.calibration import decode_fraction
from rules.channels import CHANNELS, require_channel, require_identifier


def initialize_rolling_store(connection):
    with transaction(connection) as db:
        db.execute('''
            CREATE TABLE IF NOT EXISTS rolling_covers (
                page_id TEXT PRIMARY KEY,
                notification TEXT NOT NULL
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS rolling_deliveries (
                ticket TEXT PRIMARY KEY,
                page_id TEXT NOT NULL,
                beat INTEGER NOT NULL,
                channel TEXT NOT NULL CHECK (channel IN ('vibration', 'temperature')),
                status TEXT NOT NULL CHECK (status IN ('reading', 'discarded')),
                packet TEXT
            )
        ''')


def _canonical(data):
    return json.dumps(data, sort_keys=True)


def _delivery_from_row(row):
    return {
        'ticket': row['ticket'], 'page_id': row['page_id'], 'beat': row['beat'],
        'channel': row['channel'], 'status': row['status'],
        'packet': None if row['packet'] is None else json.loads(row['packet']),
    }


class RollingStore:
    def __init__(self, connection):
        self.connection = connection

    def transaction(self):
        return transaction(self.connection)

    def has_page(self, page_id):
        row = self.connection.execute('''
            SELECT
                (SELECT COUNT(*) FROM rolling_covers WHERE page_id = ?) +
                (SELECT COUNT(*) FROM rolling_deliveries WHERE page_id = ?)
        ''', (page_id, page_id)).fetchone()
        return row[0] > 0

    def load_cover(self, page_id):
        row = self.connection.execute(
            'SELECT notification FROM rolling_covers WHERE page_id = ?', (page_id,),
        ).fetchone()
        return None if row is None else json.loads(row[0])

    def load_delivery(self, ticket):
        row = self.connection.execute(
            'SELECT * FROM rolling_deliveries WHERE ticket = ?', (ticket,),
        ).fetchone()
        return None if row is None else _delivery_from_row(row)

    def deliveries_for_page(self, page_id):
        rows = self.connection.execute(
            'SELECT * FROM rolling_deliveries WHERE page_id = ? ORDER BY rowid', (page_id,),
        ).fetchall()
        return [_delivery_from_row(row) for row in rows]


def _parse_cover(notification):
    if not isinstance(notification, dict) or notification.get('kind') != 'cover':
        raise ValueError('expected a coverage notification')
    page_id = require_identifier(notification.get('page_id'), 'page_id')
    body = notification.get('body')
    if not isinstance(body, dict):
        raise ValueError('coverage notification needs a body dictionary')
    try:
        layout = PageLayout.from_record(body['layout'])
        raw_facts = body['facts']
        raw_closed = body['closed']
        raw_tickets = body['tickets']
    except (KeyError, TypeError) as error:
        raise ValueError(f'coverage body is incomplete: {error}') from None
    if layout.page_id != page_id:
        raise ValueError('coverage body disagrees with the covered page')
    if not isinstance(raw_facts, list) or not isinstance(raw_tickets, list):
        raise ValueError('coverage facts and tickets must be lists')
    facts = {}
    sequences = set()
    for item in raw_facts:
        if not isinstance(item, dict) or 'raw' in item:
            raise ValueError('coverage facts carry origins and kinds only')
        try:
            origin = Origin.from_record(item['origin'])
            channel = require_channel(item['channel'])
            kind = item['kind']
        except (KeyError, TypeError) as error:
            raise ValueError(f'invalid coverage fact: {error}') from None
        if kind not in ('reading', 'missing'):
            raise ValueError(f'unknown coverage fact kind {kind!r}')
        if (origin.page_id, origin.slot) != (layout.page_id, layout.slot):
            raise ValueError('coverage fact origin disagrees with the page')
        if origin.beat not in layout.beats:
            raise ValueError('coverage fact beat is outside the original layout')
        key = (origin.beat, channel.value)
        if key in facts or origin.sequence in sequences:
            raise ValueError('coverage record has duplicate facts')
        facts[key] = {'origin': origin, 'kind': kind}
        sequences.add(origin.sequence)
    expected = {(beat, channel.value) for beat in layout.beats for channel in CHANNELS}
    if set(facts) != expected:
        raise ValueError('coverage record must list every beat on both channels')
    try:
        closed = Origin.from_record(raw_closed)
    except (KeyError, TypeError) as error:
        raise ValueError(f'invalid coverage close origin: {error}') from None
    if closed.beat is not None or (closed.page_id, closed.slot) != (layout.page_id, layout.slot):
        raise ValueError('coverage close origin disagrees with the page')
    if sequences and closed.sequence <= max(sequences):
        raise ValueError('coverage close sequence must follow the facts')
    tickets = {}
    for item in raw_tickets:
        if not isinstance(item, dict):
            raise ValueError('coverage tickets must be dictionaries')
        try:
            ticket = require_identifier(item['ticket'], 'ticket')
            if item['page_id'] != page_id:
                raise ValueError('coverage ticket belongs to another page')
            beat = require_beat(item['beat'])
            channel = require_channel(item['channel'])
        except KeyError as error:
            raise ValueError(f'invalid coverage ticket: {error}') from None
        if ticket in tickets:
            raise ValueError('coverage record has duplicate tickets')
        record = facts.get((beat, channel.value))
        if record is None or record['kind'] != 'reading':
            raise ValueError('coverage tickets must name a recorded reading')
        tickets[ticket] = (beat, channel.value)
    return layout, facts, closed, tickets


def _parse_packet(ticket, page_id, beat, channel, packet):
    if not isinstance(packet, dict):
        raise ValueError('a reading delivery needs the original packet dictionary')
    if packet.get('ticket') != ticket:
        raise ValueError('packet ticket disagrees with the delivery identity')
    fact_data = packet.get('fact')
    if not isinstance(fact_data, dict):
        raise ValueError('packet needs a fact record')
    try:
        fact = fact_from_record(fact_data)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f'invalid packet fact: {error}') from None
    if not isinstance(fact, ReadingEvent):
        raise ValueError('only an original reading packet can be received')
    if (fact.origin.page_id, fact.origin.beat, fact.channel) != (page_id, beat, channel):
        raise ValueError('packet fact disagrees with the delivery identity')
    return fact


def _ingest_cover(store, notification):
    layout, facts, closed, tickets = _parse_cover(notification)
    page_id = layout.page_id
    with store.transaction() as db:
        existing = store.load_cover(page_id)
        if existing is not None:
            if _canonical(existing) == _canonical(notification):
                return None
            raise ValueError('conflicting coverage notification for the same page')
        for delivery in store.deliveries_for_page(page_id):
            bound = tickets.get(delivery['ticket'])
            if bound != (delivery['beat'], delivery['channel']):
                raise ValueError('accepted delivery is not bound in the coverage record')
            if delivery['status'] == 'reading':
                record = facts[(delivery['beat'], delivery['channel'])]
                if record['kind'] != 'reading':
                    raise ValueError('accepted packet conflicts with an explicit missing')
                packet_origin = Origin.from_record(delivery['packet']['fact']['origin'])
                if packet_origin != record['origin']:
                    raise ValueError('accepted packet provenance disagrees with the coverage record')
        db.execute(
            'INSERT INTO rolling_covers(page_id, notification) VALUES (?, ?)',
            (page_id, _canonical(notification)),
        )
    return notification


def _ingest_delivery(store, event, kind):
    ticket = require_identifier(event.get('ticket'), 'ticket')
    page_id = require_identifier(event.get('page_id'), 'page_id')
    beat = require_beat(event.get('beat'))
    channel = require_channel(event.get('channel'))
    if kind == 'reading':
        if 'packet' not in event:
            raise ValueError('a reading delivery needs the original packet')
        packet = event['packet']
        fact = _parse_packet(ticket, page_id, beat, channel, packet)
    else:
        if 'packet' in event:
            raise ValueError('a discarded delivery carries no packet')
        packet = fact = None
    with store.transaction() as db:
        existing = store.load_delivery(ticket)
        if existing is not None:
            same = (
                existing['page_id'] == page_id and existing['beat'] == beat
                and existing['channel'] == channel.value and existing['status'] == kind
                and (kind == 'discarded' or _canonical(existing['packet']) == _canonical(packet))
            )
            if same:
                return None
            raise ValueError('delivery ticket was already accepted with different content')
        cover = store.load_cover(page_id)
        if cover is not None:
            _, facts, _, tickets = _parse_cover(cover)
            if tickets.get(ticket) != (beat, channel.value):
                raise ValueError('delivery ticket is not bound to this beat in the coverage record')
            if kind == 'reading':
                record = facts[(beat, channel.value)]
                if record['kind'] != 'reading':
                    raise ValueError('coverage record shows an explicit missing for this beat')
                if fact.origin != record['origin']:
                    raise ValueError('packet provenance disagrees with the coverage record')
        if kind == 'reading':
            for other in store.deliveries_for_page(page_id):
                if other['status'] != 'reading':
                    continue
                if (other['beat'], other['channel']) != (beat, channel.value):
                    continue
                if decode_fraction(other['packet']['fact']['raw']) != fact.raw:
                    raise ValueError('conflicting packets for the same original reading')
        db.execute('''
            INSERT INTO rolling_deliveries(ticket, page_id, beat, channel, status, packet)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (ticket, page_id, beat, channel.value, kind,
              None if packet is None else _canonical(packet)))
    accepted = {'kind': kind, 'ticket': ticket, 'page_id': page_id, 'beat': beat,
                'channel': channel.value}
    if kind == 'reading':
        accepted['packet'] = packet
    return accepted


def ingest(store, event):
    if not isinstance(event, dict):
        raise ValueError('rolling intake expects an event dictionary')
    kind = event.get('kind')
    if kind == 'cover':
        return _ingest_cover(store, event)
    if kind in ('reading', 'discarded'):
        return _ingest_delivery(store, event, kind)
    raise ValueError(f'unknown rolling event kind {kind!r}')
