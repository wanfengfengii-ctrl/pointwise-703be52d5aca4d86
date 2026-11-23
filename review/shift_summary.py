from dataclasses import dataclass
from typing import Iterable

from review.missing import select_missing
from review.reports import HistoricalReport
from rules.channels import Channel


@dataclass(frozen=True)
class ShiftSummary:
    shift_id: str
    channel: Channel
    measured: int
    missing: int


def summarize_shift(reports: Iterable[HistoricalReport]):
    seen = set()
    totals = {}
    for historical in reports:
        report = historical.report
        request = report.request
        original = request.page_id, request.channel
        if original in seen:
            continue
        seen.add(original)
        missing = len(select_missing(report))
        measured = len(report.rows) - missing
        key = request.shift_id, request.channel
        previous_measured, previous_missing = totals.get(key, (0, 0))
        totals[key] = previous_measured + measured, previous_missing + missing
    return tuple(ShiftSummary(*key, *totals[key]) for key in sorted(totals))
