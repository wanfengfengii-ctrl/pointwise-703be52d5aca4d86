import json
from dataclasses import dataclass
from fractions import Fraction

from desk.intake import SettlementRequest
from desk.readiness import BeatStatus
from desk.receipts import make_first_receipt, report_number, restore_first_receipt
from desk.report import Publication, Report, ReportRow, SettlementResult
from recorder.events import Origin
from recorder.pages import PageLayout
from recorder.store import transaction
from rules.calibration import decode_fraction
from rules.channels import require_channel, require_identifier
from rules.readings import validate_raw_reading
from rules.shifts import load_rule


def initialize_rolling_desk_store(connection):
    with transaction(connection) as db:
        db.execute('''
            CREATE TABLE IF NOT EXISTS rolling_requests (
                request_id TEXT PRIMARY KEY,
                page_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                shift_id TEXT NOT NULL,
                revision TEXT NOT NULL
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS rolling_reports (
                report_id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL UNIQUE REFERENCES rolling_requests(request_id),
                body TEXT NOT NULL
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS rolling_receipts (
                receipt_id TEXT PRIMARY KEY,
                report_id TEXT NOT NULL UNIQUE REFERENCES rolling_reports(report_id),
                body TEXT NOT NULL
            )
        ''')


class RollingDeskStore:
    def __init__(self, connection):
        self.connection = connection

    def load_request(self, request_id):
        row = self.connection.execute(
            'SELECT * FROM rolling_requests WHERE request_id = ?', (request_id,),
        ).fetchone()
        if row is None:
            return None
        return SettlementRequest(
            row['request_id'], row['page_id'], require_channel(row['channel']),
            row['shift_id'], row['revision'],
        )

    def save_request(self, request):
        with transaction(self.connection) as db:
            existing = self.load_request(request.request_id)
            if existing is not None:
                if existing != request:
                    raise ValueError('settlement request was already bound to different parameters')
                return existing
            db.execute('''
                INSERT INTO rolling_requests(request_id, page_id, channel, shift_id, revision)
                VALUES (?, ?, ?, ?, ?)
            ''', (request.request_id, request.page_id, request.channel.value,
                  request.shift_id, request.revision))
        return request

    def load_publication(self, report_id):
        row = self.connection.execute('''
            SELECT reports.body, receipts.body FROM rolling_reports AS reports
            JOIN rolling_receipts AS receipts ON receipts.report_id = reports.report_id
            WHERE reports.report_id = ?
        ''', (report_id,)).fetchone()
        return None if row is None else (json.loads(row[0]), json.loads(row[1]))

    def publish(self, report, receipt, *, before_commit=None):
        request_id = report.request.request_id
        with transaction(self.connection) as db:
            prior = self.load_publication(report.report_id)
            if prior is not None:
                return prior
            bound = self.load_request(request_id)
            if bound is None or bound != report.request:
                raise ValueError('publication has no matching request binding')
            if receipt.request_id != request_id or receipt.report_id != report.report_id:
                raise ValueError('receipt does not identify the proposed publication')
            report_data, receipt_data = report.to_record(), receipt.to_record()
            db.execute('''
                INSERT INTO rolling_reports(report_id, request_id, body) VALUES (?, ?, ?)
            ''', (report.report_id, request_id, json.dumps(report_data)))
            db.execute('''
                INSERT INTO rolling_receipts(receipt_id, report_id, body) VALUES (?, ?, ?)
            ''', (receipt.receipt_id, report.report_id, json.dumps(receipt_data)))
            if before_commit is not None:
                before_commit()
        return report_data, receipt_data


@dataclass(frozen=True)
class RollingTerminal:
    beat: int
    status: BeatStatus
    origin: Origin
    raw: Fraction | None


def channel_terminals(cover, deliveries, channel):
    """Terminal state per beat in original page order, plus beats still in flight."""
    channel = require_channel(channel)
    body = cover['body']
    layout = PageLayout.from_record(body['layout'])
    facts = {(item['origin']['beat'], item['channel']): item for item in body['facts']}
    tickets = {}
    for item in body['tickets']:
        tickets.setdefault((item['beat'], item['channel']), []).append(item['ticket'])
    by_ticket = {delivery['ticket']: delivery for delivery in deliveries}
    terminals = []
    pending = []
    for beat in layout.beats:
        record = facts[(beat, channel.value)]
        origin = Origin.from_record(record['origin'])
        if record['kind'] == 'missing':
            terminals.append(RollingTerminal(beat, BeatStatus.MISSING, origin, None))
            continue
        arrived = [
            delivery for delivery in deliveries
            if delivery['status'] == 'reading'
            and (delivery['beat'], delivery['channel']) == (beat, channel.value)
        ]
        if arrived:
            raw = decode_fraction(arrived[0]['packet']['fact']['raw'])
            terminals.append(RollingTerminal(beat, BeatStatus.READING, origin, raw))
            continue
        if any(ticket not in by_ticket for ticket in tickets.get((beat, channel.value), ())):
            pending.append(beat)
            continue
        terminals.append(RollingTerminal(beat, BeatStatus.TRANSPORT_LOST, origin, None))
    return tuple(terminals), tuple(pending)


def _restore_publication(data):
    report_data, receipt_data = data
    return Publication(Report.from_record(report_data), restore_first_receipt(receipt_data))


def read_committed_rolling_report(store, report_id):
    data = store.load_publication(report_id)
    return None if data is None else _restore_publication(data)


def settle_rolling(rolling_store, desk_store, request_id, page_id, channel, *, before_commit=None):
    require_identifier(request_id, 'request_id')
    require_identifier(page_id, 'page_id')
    channel = require_channel(channel)
    bound = desk_store.load_request(request_id)
    if bound is not None and (bound.page_id, bound.channel) != (page_id, channel):
        raise ValueError('settlement request was already bound to different parameters')
    report_id = report_number(request_id)
    committed = read_committed_rolling_report(desk_store, report_id)
    if committed is not None:
        return SettlementResult('published', (), committed)
    cover = rolling_store.load_cover(page_id)
    if cover is None:
        if rolling_store.has_page(page_id):
            raise ValueError(f'rolling page {page_id!r} has no coverage evidence')
        raise KeyError(f'unknown rolling page {page_id!r}')
    layout = PageLayout.from_record(cover['body']['layout'])
    request = SettlementRequest(request_id, page_id, channel, layout.shift_id, layout.revision)
    if bound is not None and bound != request:
        raise ValueError('settlement request was already bound to different parameters')
    desk_store.save_request(request)
    deliveries = rolling_store.deliveries_for_page(page_id)
    terminals, pending = channel_terminals(cover, deliveries, channel)
    if pending:
        return SettlementResult('pending', pending)
    rule = load_rule(desk_store.connection, layout.shift_id, layout.revision)
    calibration = rule.for_channel(channel)
    rows = []
    for terminal in terminals:
        raw = converted = None
        if terminal.status is BeatStatus.READING:
            raw = validate_raw_reading(calibration, terminal.raw)
            converted = calibration.convert(raw)
        rows.append(ReportRow(
            terminal.beat, terminal.status, raw, converted,
            calibration.raw_unit, calibration.unit, terminal.origin,
        ))
    report = Report(report_id, request, tuple(rows))
    receipt = make_first_receipt(request, report_id)
    data = desk_store.publish(report, receipt, before_commit=before_commit)
    return SettlementResult('published', (), _restore_publication(data))
