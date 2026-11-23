from dataclasses import dataclass

from desk.readiness import BeatStatus
from desk.report import Report
from recorder.events import Origin
from review.provenance import list_origins
from review.reports import lookup
from rules.channels import Channel


@dataclass(frozen=True)
class MissingItem:
    report_id: str
    channel: Channel
    beat: int
    origin: Origin


def select_missing(report: Report):
    return tuple(
        MissingItem(report.report_id, report.request.channel, row.beat, origin)
        for row, origin in zip(report.rows, list_origins(report), strict=True)
        if row.status is BeatStatus.MISSING
    )


def follow_up(store, report_id):
    return select_missing(lookup(store, report_id).report)
