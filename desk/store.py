import json

from desk.intake import IntakeState, merge_intake
from recorder.store import transaction


class DeskStore:
    def __init__(self, connection):
        self.connection = connection

    def load_intake(self, request_id):
        row = self.connection.execute(
            'SELECT intake FROM desk_requests WHERE request_id = ?', (request_id,),
        ).fetchone()
        return None if row is None else IntakeState.from_record(json.loads(row[0]))

    def save_intake(self, request, offer):
        with transaction(self.connection) as db:
            state = merge_intake(request, offer, self.load_intake(request.request_id))
            db.execute('''
                INSERT INTO desk_requests(request_id, intake) VALUES (?, ?)
                ON CONFLICT(request_id) DO UPDATE SET intake = excluded.intake
            ''', (request.request_id, json.dumps(state.to_record())))
        return state

    def load_publication(self, report_id):
        row = self.connection.execute('''
            SELECT reports.body, receipts.body FROM desk_reports AS reports
            JOIN desk_receipts AS receipts ON receipts.report_id = reports.report_id
            WHERE reports.report_id = ?
        ''', (report_id,)).fetchone()
        return None if row is None else (json.loads(row[0]), json.loads(row[1]))

    def load_receipt(self, report_id):
        row = self.connection.execute(
            'SELECT body FROM desk_receipts WHERE report_id = ?', (report_id,),
        ).fetchone()
        return None if row is None else json.loads(row[0])

    def publish(self, report, receipt, *, before_commit=None):
        request_id = report.request.request_id
        with transaction(self.connection) as db:
            prior = self.load_publication(report.report_id)
            if prior is not None:
                return prior
            bound = self.load_intake(request_id)
            if bound is None or bound.request != report.request:
                raise ValueError('publication has no matching request binding')
            if receipt.request_id != request_id or receipt.report_id != report.report_id:
                raise ValueError('receipt does not identify the proposed publication')
            report_data, receipt_data = report.to_record(), receipt.to_record()
            db.execute('''
                INSERT INTO desk_reports(report_id, request_id, body) VALUES (?, ?, ?)
            ''', (report.report_id, request_id, json.dumps(report_data)))
            db.execute('''
                INSERT INTO desk_receipts(receipt_id, report_id, body) VALUES (?, ?, ?)
            ''', (receipt.receipt_id, report.report_id, json.dumps(receipt_data)))
            if before_commit is not None:
                before_commit()
        return report_data, receipt_data


def initialize_desk_store(connection):
    with transaction(connection) as db:
        db.execute('''
            CREATE TABLE IF NOT EXISTS desk_requests (
                request_id TEXT PRIMARY KEY,
                intake TEXT NOT NULL
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS desk_reports (
                report_id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL UNIQUE REFERENCES desk_requests(request_id),
                body TEXT NOT NULL
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS desk_receipts (
                receipt_id TEXT PRIMARY KEY,
                report_id TEXT NOT NULL UNIQUE REFERENCES desk_reports(report_id),
                body TEXT NOT NULL
            )
        ''')
