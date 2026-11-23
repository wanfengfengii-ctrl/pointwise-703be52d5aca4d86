from dataclasses import dataclass
from fractions import Fraction


def parse_fraction(value):
    if isinstance(value, bool) or not isinstance(value, (int, str, Fraction)):
        raise ValueError('expected an integer or exact rational value')
    try:
        return Fraction(value)
    except (ValueError, ZeroDivisionError):
        raise ValueError(f'invalid rational value {value!r}') from None


def encode_fraction(value):
    return [value.numerator, value.denominator]


def decode_fraction(pair):
    return Fraction(*pair)


_FIELDS = ('scale', 'offset', 'minimum', 'maximum')


@dataclass(frozen=True)
class Calibration:
    raw_unit: str
    unit: str
    scale: Fraction
    offset: Fraction
    minimum: Fraction
    maximum: Fraction

    def __post_init__(self):
        for name in _FIELDS:
            object.__setattr__(self, name, parse_fraction(getattr(self, name)))
        if self.minimum > self.maximum:
            raise ValueError('raw range minimum exceeds maximum')

    def convert(self, raw):
        return self.scale * parse_fraction(raw) + self.offset

    def to_record(self):
        data = {name: encode_fraction(getattr(self, name)) for name in _FIELDS}
        return dict(data, raw_unit=self.raw_unit, unit=self.unit)

    @classmethod
    def from_record(cls, record):
        values = {name: decode_fraction(record[name]) for name in _FIELDS}
        return cls(record['raw_unit'], record['unit'], **values)
