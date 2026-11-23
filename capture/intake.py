from dataclasses import dataclass

from capture.evidence import PageEvidence
from capture.handoff import HandoffProgress, handoff_progress
from capture.store import CaptureStore
from recorder.events import ReadingEvent
from recorder.pages import PageCleared, read_page
from rules.channels import require_identifier
from rules.readings import validate_raw_reading
from rules.shifts import load_rule


@dataclass(frozen=True)
class CollectionResult:
    evidence: PageEvidence
    progress: HandoffProgress
    source_status: str


def _retained(store, page_id, status):
    evidence = store.load_evidence(page_id)
    if evidence is None:
        raise KeyError(f'page {page_id!r} has no station evidence')
    return CollectionResult(evidence, handoff_progress(evidence), status)


def collect(store: CaptureStore, device, page_id):
    require_identifier(page_id, 'page_id')
    if device is None:
        return _retained(store, page_id, 'offline')
    try:
        snapshot = read_page(device, page_id)
    except PageCleared:
        return _retained(store, page_id, 'cleared')
    layout = snapshot.layout
    rule = load_rule(store.connection, layout.shift_id, layout.revision)
    for fact in snapshot.facts:
        if isinstance(fact, ReadingEvent):
            validate_raw_reading(rule.for_channel(fact.channel), fact.raw)
    merged = store.save_evidence(snapshot)
    status = 'closed' if snapshot.closed is not None else 'recording'
    return CollectionResult(merged, handoff_progress(merged), status)
