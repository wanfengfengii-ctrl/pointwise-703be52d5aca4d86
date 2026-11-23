from dataclasses import dataclass

from capture.evidence import channel_coverage
from capture.store import CaptureStore
from recorder.terminals import ReleaseOutcome, ReleaseReplyLost, clear_page, read_release_outcome
from rules.channels import CHANNELS, require_identifier


@dataclass(frozen=True)
class ReleaseProgress:
    request_id: str
    page_id: str
    status: str
    outcome: ReleaseOutcome | None = None


def _complete(evidence):
    return (
        evidence is not None
        and evidence.closed is not None
        and all(not channel_coverage(evidence, channel) for channel in CHANNELS)
    )


def check_release_eligibility(store: CaptureStore, page_id):
    require_identifier(page_id, 'page_id')
    return _complete(store.load_evidence(page_id))


def release(store: CaptureStore, device, request_id, page_id, *, lose_reply=False):
    require_identifier(request_id, 'request_id')
    require_identifier(page_id, 'page_id')
    claim = store.load_release(request_id, page_id)
    if claim is None:
        evidence = store.load_evidence(page_id)
        if not _complete(evidence):
            return ReleaseProgress(request_id, page_id, 'awaiting_evidence')
        claim = store.claim_release(request_id, page_id, evidence.layout.slot)
    if claim.confirmed:
        return ReleaseProgress(request_id, page_id, 'released', claim.outcome())
    if device is None:
        return ReleaseProgress(request_id, page_id, 'awaiting_device')
    outcome = read_release_outcome(device, request_id, page_id)
    if outcome is None:
        try:
            outcome = clear_page(device, request_id, page_id, lose_reply=lose_reply)
        except ReleaseReplyLost:
            return ReleaseProgress(request_id, page_id, 'awaiting_device')
    store.confirm_release(outcome)
    return ReleaseProgress(request_id, page_id, 'released', outcome)
