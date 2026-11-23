from dataclasses import dataclass

from recorder.events import (
    MissingEvent, Origin, PageClosed, ReadingEvent, event_identity, fact_from_record, fact_to_record,
)
from recorder.pages import PageLayout
from rules.channels import CHANNELS, require_channel


Evidence = ReadingEvent | MissingEvent


@dataclass(frozen=True)
class PageEvidence:
    layout: PageLayout
    facts: tuple[Evidence, ...]
    closed: PageClosed | None = None

    def to_record(self):
        return {
            'layout': self.layout.to_record(),
            'facts': [fact_to_record(fact) for fact in self.facts],
            'closed': None if self.closed is None else self.closed.origin.to_record(),
        }

    @classmethod
    def from_record(cls, record):
        closed = record['closed']
        return cls(
            PageLayout.from_record(record['layout']),
            tuple(fact_from_record(fact) for fact in record['facts']),
            None if closed is None else PageClosed(Origin.from_record(closed)),
        )


def _validate_origin(layout, origin):
    if (origin.page_id, origin.slot) != (layout.page_id, layout.slot):
        raise ValueError('evidence origin disagrees with the original page')


def merge_evidence(current, layout, facts, closed=None):
    if current is not None and current.layout != layout:
        raise ValueError('original page layout changed')
    previous = () if current is None else current.facts
    by_identity = {event_identity(fact): fact for fact in previous}
    by_sequence = {fact.origin.sequence: fact for fact in previous}
    expected_beats = set(layout.beats)
    for fact in facts:
        if not isinstance(fact, (ReadingEvent, MissingEvent)):
            raise ValueError('unsupported device evidence')
        origin = fact.origin
        _validate_origin(layout, origin)
        if origin.beat not in expected_beats:
            raise ValueError('evidence beat is outside the original layout')
        identity = event_identity(fact)
        if identity in by_identity and by_identity[identity] != fact:
            raise ValueError('conflicting evidence for the same page, beat, and channel')
        if origin.sequence in by_sequence and by_sequence[origin.sequence] != fact:
            raise ValueError('device sequence belongs to another fact')
        by_identity[identity] = fact
        by_sequence[origin.sequence] = fact

    previous_close = None if current is None else current.closed
    if closed is not None and previous_close is not None and closed != previous_close:
        raise ValueError('conflicting device close evidence')
    terminal = closed if closed is not None else previous_close
    if terminal is not None:
        _validate_origin(layout, terminal.origin)
        if len(by_identity) != len(layout.beats) * len(CHANNELS):
            raise ValueError('device close evidence requires both channels for every beat')
        if terminal.origin.sequence <= max(by_sequence):
            raise ValueError('device close sequence follows the page facts')
    ordered = tuple(by_sequence[sequence] for sequence in sorted(by_sequence))
    return PageEvidence(layout, ordered, terminal)


def channel_coverage(evidence: PageEvidence, channel):
    channel = require_channel(channel)
    received = {fact.origin.beat for fact in evidence.facts if fact.channel == channel}
    return tuple(beat for beat in evidence.layout.beats if beat not in received)
