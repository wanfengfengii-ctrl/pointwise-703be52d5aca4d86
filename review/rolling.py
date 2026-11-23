from desk.rolling import read_committed_rolling_report
from review.provenance import list_origins
from review.reports import HistoricalReport
from rules.channels import require_identifier


def lookup_rolling(store, report_id):
    require_identifier(report_id, 'report_id')
    publication = read_committed_rolling_report(store, report_id)
    if publication is None:
        raise KeyError(f'no committed report {report_id!r}')
    return HistoricalReport(publication.report, publication.receipt, list_origins(publication.report))
