from dataclasses import dataclass
import json

from capture.evidence import PageEvidence, merge_evidence
from recorder.store import transaction
from recorder.terminals import ReleaseOutcome


@dataclass(frozen=True)
class ReleaseClaim:
    request_id: str
    page_id: str
    slot: str
    confirmed: bool

    def outcome(self):
        return ReleaseOutcome(self.request_id, self.page_id, self.slot)


class CaptureStore:
    def __init__(self, connection):
        self.connection = connection

    def load_evidence(self, page_id):
        row = self.connection.execute(
            'SELECT evidence FROM capture_pages WHERE page_id = ?', (page_id,),
        ).fetchone()
        return None if row is None else PageEvidence.from_record(json.loads(row[0]))

    def save_evidence(self, snapshot):
        page_id = snapshot.layout.page_id
        with transaction(self.connection) as db:
            current = self.load_evidence(page_id)
            merged = merge_evidence(current, snapshot.layout, snapshot.facts, snapshot.closed)
            db.execute('''
                INSERT INTO capture_pages(page_id, evidence) VALUES (?, ?)
                ON CONFLICT(page_id) DO UPDATE SET evidence = excluded.evidence
            ''', (page_id, json.dumps(merged.to_record())))
        return merged

    def load_release(self, request_id, page_id):
        rows = self.connection.execute('''
            SELECT request_id, page_id, slot, confirmed FROM capture_releases
            WHERE request_id = ? OR page_id = ?
        ''', (request_id, page_id)).fetchall()
        for row in rows:
            if (row['request_id'], row['page_id']) != (request_id, page_id):
                raise ValueError('release request or page has a different existing binding')
        if not rows:
            return None
        row = rows[0]
        return ReleaseClaim(request_id, page_id, row['slot'], bool(row['confirmed']))

    def claim_release(self, request_id, page_id, slot):
        with transaction(self.connection) as db:
            current = self.load_release(request_id, page_id)
            if current is not None:
                if current.slot != slot:
                    raise ValueError('release slot disagrees with its saved binding')
                return current
            db.execute('''
                INSERT INTO capture_releases(request_id, page_id, slot) VALUES (?, ?, ?)
            ''', (request_id, page_id, slot))
        return ReleaseClaim(request_id, page_id, slot, False)

    def confirm_release(self, outcome):
        with transaction(self.connection) as db:
            claim = self.load_release(outcome.request_id, outcome.page_id)
            if claim is None or claim.outcome() != outcome:
                raise ValueError('device outcome disagrees with the saved release claim')
            db.execute('UPDATE capture_releases SET confirmed = 1 WHERE request_id = ?', (outcome.request_id,))
        return outcome


def initialize_capture_store(connection):
    with transaction(connection) as db:
        db.execute('''
            CREATE TABLE IF NOT EXISTS capture_pages (
                page_id TEXT PRIMARY KEY,
                evidence TEXT NOT NULL
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS capture_releases (
                request_id TEXT PRIMARY KEY,
                page_id TEXT NOT NULL UNIQUE REFERENCES capture_pages(page_id),
                slot TEXT NOT NULL,
                confirmed INTEGER NOT NULL DEFAULT 0 CHECK (confirmed IN (0, 1))
            )
        ''')
