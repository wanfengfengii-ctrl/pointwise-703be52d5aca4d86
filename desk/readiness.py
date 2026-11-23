from dataclasses import dataclass
from enum import StrEnum

from capture.evidence import Evidence
from desk.intake import IntakeState
from recorder.events import MissingEvent, ReadingEvent


class BeatStatus(StrEnum):
    WAITING = 'waiting'
    READING = 'reading'
    MISSING = 'missing'


@dataclass(frozen=True)
class Readiness:
    pending_beats: tuple[int, ...]
    ordered_evidence: tuple[Evidence, ...]


def classify_beat(fact: Evidence | None):
    if fact is None:
        return BeatStatus.WAITING
    if isinstance(fact, ReadingEvent):
        return BeatStatus.READING
    if isinstance(fact, MissingEvent):
        return BeatStatus.MISSING
    raise ValueError('unknown beat evidence')


def evaluate_readiness(state: IntakeState):
    by_beat = {fact.origin.beat: fact for fact in state.evidence}
    pending, ordered = [], []
    for beat in state.layout.beats:
        fact = by_beat.get(beat)
        if classify_beat(fact) is BeatStatus.WAITING:
            pending.append(beat)
        else:
            ordered.append(fact)
    return Readiness(tuple(pending), tuple(ordered))
