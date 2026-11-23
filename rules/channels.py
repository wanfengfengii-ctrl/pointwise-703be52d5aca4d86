from enum import StrEnum


class Channel(StrEnum):
    VIBRATION = 'vibration'
    TEMPERATURE = 'temperature'


CHANNELS = tuple(Channel)
_UNITS = {Channel.VIBRATION: ('count', 'mm/s'), Channel.TEMPERATURE: ('count', '°C')}


def require_identifier(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be a nonempty string')
    return value


def require_channel(value):
    try:
        return Channel(value)
    except (TypeError, ValueError):
        raise ValueError(f'unknown channel {value!r}') from None


def validate_units(channel, raw_unit, unit):
    channel = require_channel(channel)
    if (raw_unit, unit) != _UNITS[channel]:
        raise ValueError(f'invalid unit pair for {channel}: {raw_unit!r}, {unit!r}')
