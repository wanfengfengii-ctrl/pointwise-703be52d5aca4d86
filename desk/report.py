from dataclasses import dataclass
from fractions import Fraction

from desk.intake import IntakeState, SettlementRequest, accept_evidence
from desk.readiness import BeatStatus, classify_beat, evaluate_readiness
from desk.receipts import FirstReceipt, make_first_receipt, report_number, restore_first_receipt
from desk.store import DeskStore
from recorder.events import Origin
from rules.calibration import decode_fraction, encode_fraction
from rules.readings import validate_raw_reading
from rules.shifts import ShiftRule, load_rule


class PublishInterrupted(RuntimeError):
    pass


@dataclass(frozen=True)
class ReportRow:
    beat: int
    status: BeatStatus
    raw: Fraction | None
    converted: Fraction | None
    raw_unit: str
    unit: str
    origin: Origin

    def to_record(self):
        return {
            'beat': self.beat, 'status': self.status.value,
            'raw': None if self.raw is None else encode_fraction(self.raw),
            'converted': None if self.converted is None else encode_fraction(self.converted),
            'raw_unit': self.raw_unit, 'unit': self.unit, 'origin': self.origin.to_record(),
        }

    @classmethod
    def from_record(cls, record):
        return cls(
            record['beat'], BeatStatus(record['status']),
            None if record['raw'] is None else decode_fraction(record['raw']),
            None if record['converted'] is None else decode_fraction(record['converted']),
            record['raw_unit'], record['unit'], Origin.from_record(record['origin']),
        )


@dataclass(frozen=True)
class Report:
    report_id: str
    request: SettlementRequest
    rows: tuple[ReportRow, ...]

    def to_record(self):
        return {
            'report_id': self.report_id, 'request': self.request.to_record(),
            'rows': [row.to_record() for row in self.rows],
        }

    @classmethod
    def from_record(cls, record):
        return cls(
            record['report_id'], SettlementRequest(**record['request']),
            tuple(ReportRow.from_record(row) for row in record['rows']),
        )


@dataclass(frozen=True)
class Publication:
    report: Report
    receipt: FirstReceipt


@dataclass(frozen=True)
class SettlementResult:
    status: str
    pending_beats: tuple[int, ...]
    publication: Publication | None = None


def build_report(state: IntakeState, readiness, rule: ShiftRule):
    request = state.request
    if readiness.pending_beats:
        raise ValueError('cannot publish a report with pending beats')
    if (rule.shift_id, rule.revision) != (request.shift_id, request.revision):
        raise ValueError('calibration does not match the original report rule')
    calibration = rule.for_channel(request.channel)
    rows = []
    for fact in readiness.ordered_evidence:
        status = classify_beat(fact)
        raw = converted = None
        if status is BeatStatus.READING:
            raw = validate_raw_reading(calibration, fact.raw)
            converted = calibration.convert(raw)
        rows.append(ReportRow(
            fact.origin.beat, status, raw, converted, calibration.raw_unit, calibration.unit, fact.origin,
        ))
    return Report(report_number(request.request_id), request, tuple(rows))


def _restore_publication(data):
    report, receipt = data
    return Publication(Report.from_record(report), restore_first_receipt(receipt))


def read_committed_report(store: DeskStore, report_id):
    data = store.load_publication(report_id)
    return None if data is None else _restore_publication(data)


def settle(store: DeskStore, request, offer, *, before_commit=None):
    rule = load_rule(store.connection, request.shift_id, request.revision)
    calibration = rule.for_channel(request.channel)
    for fact in offer.evidence:
        if classify_beat(fact) is BeatStatus.READING:
            validate_raw_reading(calibration, fact.raw)
    state = accept_evidence(store, request, offer)
    current = read_committed_report(store, report_number(request.request_id))
    if current is not None:
        return SettlementResult('published', (), current)
    readiness = evaluate_readiness(state)
    if readiness.pending_beats:
        return SettlementResult('pending', readiness.pending_beats)
    report = build_report(state, readiness, rule)
    receipt = make_first_receipt(request, report.report_id)
    committed = store.publish(report, receipt, before_commit=before_commit)
    return SettlementResult('published', (), _restore_publication(committed))
