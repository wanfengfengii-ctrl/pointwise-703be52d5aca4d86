from dataclasses import dataclass
from fractions import Fraction

from rules.calibration import decode_fraction, encode_fraction
from rules.channels import Channel, require_channel, require_identifier
from rules.readings import parse_raw_reading


def require_beat(beat):
    if type(beat) is not int or beat < 0:
        raise ValueError('beat must be a nonnegative integer')
    return beat


@dataclass(frozen=True)
class Origin:
    page_id: str
    slot: str
    sequence: int
    beat: int | None

    def __post_init__(self):
        require_identifier(self.page_id, 'page_id')
        require_identifier(self.slot, 'slot')
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError('device sequence must be a positive integer')
        if self.beat is not None:
            require_beat(self.beat)

    def to_record(self):
        return {
            'page_id': self.page_id, 'slot': self.slot,
            'sequence': self.sequence, 'beat': self.beat,
        }

    @classmethod
    def from_record(cls, data):
        return cls(**data)


@dataclass(frozen=True)
class ReadingEvent:
    origin: Origin
    channel: Channel
    raw: Fraction

    def __post_init__(self):
        require_beat(self.origin.beat)
        object.__setattr__(self, 'channel', require_channel(self.channel))
        object.__setattr__(self, 'raw', parse_raw_reading(self.raw))


@dataclass(frozen=True)
class MissingEvent:
    origin: Origin
    channel: Channel

    def __post_init__(self):
        require_beat(self.origin.beat)
        object.__setattr__(self, 'channel', require_channel(self.channel))


@dataclass(frozen=True)
class PageClosed:
    origin: Origin

    def __post_init__(self):
        if self.origin.beat is not None:
            raise ValueError('close event belongs to the whole page')


def event_identity(event):
    if isinstance(event, PageClosed):
        return event.origin.page_id, None, None
    return event.origin.page_id, event.origin.beat, event.channel


def fact_to_record(event):
    data = {'origin': event.origin.to_record(), 'channel': event.channel.value}
    if isinstance(event, ReadingEvent):
        return dict(data, kind='reading', raw=encode_fraction(event.raw))
    if isinstance(event, MissingEvent):
        return dict(data, kind='missing')
    raise ValueError('expected a device reading or missing fact')


def fact_from_record(data):
    origin = Origin.from_record(data['origin'])
    channel = require_channel(data['channel'])
    if data['kind'] == 'reading':
        return ReadingEvent(origin, channel, decode_fraction(data['raw']))
    if data['kind'] == 'missing':
        return MissingEvent(origin, channel)
    raise ValueError('unknown device fact type')
