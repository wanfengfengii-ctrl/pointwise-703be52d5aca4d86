from rules.calibration import Calibration, parse_fraction


def parse_raw_reading(value):
    return parse_fraction(value)


def validate_raw_reading(calibration: Calibration, value):
    raw = parse_raw_reading(value)
    if not calibration.minimum <= raw <= calibration.maximum:
        raise ValueError(f'raw reading {raw} is outside [{calibration.minimum}, {calibration.maximum}]')
    return raw
