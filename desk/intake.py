from dataclasses import dataclass
from typing import TYPE_CHECKING

from capture.evidence import Evidence, PageEvidence, merge_evidence
from capture.handoff import ChannelOffer
from recorder.events import fact_from_record, fact_to_record
from recorder.pages import PageLayout
from rules.channels import Channel, require_channel, require_identifier

if TYPE_CHECKING:
    from desk.store import DeskStore


@dataclass(frozen=True)
class SettlementRequest:
    request_id: str
    page_id: str
    channel: Channel
    shift_id: str
    revision: str

    def __post_init__(self):
        for name in ('request_id', 'page_id', 'shift_id', 'revision'):
            require_identifier(getattr(self, name), name)
        object.__setattr__(self, 'channel', require_channel(self.channel))

    def to_record(self):
        return {
            'request_id': self.request_id, 'page_id': self.page_id, 'channel': self.channel.value,
            'shift_id': self.shift_id, 'revision': self.revision,
        }


@dataclass(frozen=True)
class IntakeState:
    request: SettlementRequest
    layout: PageLayout
    evidence: tuple[Evidence, ...]

    def to_record(self):
        return {
            'request': self.request.to_record(), 'layout': self.layout.to_record(),
            'evidence': [fact_to_record(fact) for fact in self.evidence],
        }

    @classmethod
    def from_record(cls, data):
        return cls(
            SettlementRequest(**data['request']), PageLayout.from_record(data['layout']),
            tuple(fact_from_record(fact) for fact in data['evidence']),
        )


def bind_request(request: SettlementRequest, offer: ChannelOffer, existing=None):
    layout = offer.layout
    offered = (layout.page_id, offer.channel, layout.shift_id, layout.revision)
    bound = (request.page_id, request.channel, request.shift_id, request.revision)
    if offered != bound:
        raise ValueError('settlement request disagrees with the evidence offer')
    if existing is None:
        return IntakeState(request, layout, ())
    if existing.request != request or existing.layout != layout:
        raise ValueError('settlement request was already bound to different parameters')
    return existing


def merge_intake(request, offer, existing):
    bound = bind_request(request, offer, existing)
    if any(fact.channel != request.channel for fact in offer.evidence):
        raise ValueError('offer contains evidence from another channel')
    previous = PageEvidence(bound.layout, bound.evidence)
    merged = merge_evidence(previous, offer.layout, offer.evidence)
    return IntakeState(request, bound.layout, merged.facts)


def accept_evidence(store: 'DeskStore', request: SettlementRequest, offer: ChannelOffer):
    return store.save_intake(request, offer)
