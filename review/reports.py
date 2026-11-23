from dataclasses import dataclass

from desk.receipts import FirstReceipt
from desk.report import Report, read_committed_report
from recorder.events import Origin
from review.provenance import find_origin, list_origins
from rules.channels import require_identifier


@dataclass(frozen=True)
class HistoricalReport:
    report: Report
    receipt: FirstReceipt
    origins: tuple[Origin, ...]

    def origin(self, beat):
        return find_origin(self.report, beat)


def lookup(store, report_id):
    require_identifier(report_id, 'report_id')
    publication = read_committed_report(store, report_id)
    if publication is None:
        raise KeyError(f'no committed report {report_id!r}')
    report = publication.report
    return HistoricalReport(report, publication.receipt, list_origins(report))
