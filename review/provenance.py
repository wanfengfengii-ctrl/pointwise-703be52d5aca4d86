from desk.report import Report
from recorder.events import Origin, require_beat


def list_origins(report: Report) -> tuple[Origin, ...]:
    return tuple(row.origin for row in report.rows)


def find_origin(report: Report, beat) -> Origin:
    require_beat(beat)
    for row in report.rows:
        if row.beat == beat:
            return row.origin
    raise KeyError(f'beat {beat} is absent from report {report.report_id!r}')
