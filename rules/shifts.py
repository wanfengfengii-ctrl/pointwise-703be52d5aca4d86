from dataclasses import dataclass
import json

from recorder.store import transaction
from rules.calibration import Calibration
from rules.channels import CHANNELS, Channel, require_channel, require_identifier, validate_units


@dataclass(frozen=True)
class ShiftRule:
    shift_id: str
    revision: str
    vibration: Calibration
    temperature: Calibration

    def __post_init__(self):
        require_identifier(self.shift_id, 'shift_id')
        require_identifier(self.revision, 'revision')
        for channel in CHANNELS:
            rule = self.for_channel(channel)
            if not isinstance(rule, Calibration):
                raise ValueError(f'invalid calibration for {channel}')
            validate_units(channel, rule.raw_unit, rule.unit)

    def for_channel(self, channel):
        if require_channel(channel) is Channel.VIBRATION:
            return self.vibration
        return self.temperature


def initialize_shift_store(connection):
    connection.execute('''
        CREATE TABLE IF NOT EXISTS shift_rules (
            shift_id TEXT NOT NULL,
            revision TEXT NOT NULL,
            calibrations TEXT NOT NULL,
            PRIMARY KEY (shift_id, revision)
        )
    ''')


def _find_rule(connection, shift_id, revision):
    row = connection.execute(
        'SELECT calibrations FROM shift_rules WHERE shift_id = ? AND revision = ?',
        (shift_id, revision),
    ).fetchone()
    if row is None:
        return None
    data = json.loads(row[0])
    return ShiftRule(shift_id, revision, **{
        channel.value: Calibration.from_record(data[channel.value]) for channel in CHANNELS
    })


def configure(connection, shift_id, revision, calibrations):
    values = {require_channel(channel): value for channel, value in calibrations.items()}
    if set(values) != set(CHANNELS):
        raise ValueError('both fixed channel calibrations are required')
    rule = ShiftRule(shift_id, revision, values[Channel.VIBRATION], values[Channel.TEMPERATURE])
    with transaction(connection):
        previous = _find_rule(connection, shift_id, revision)
        if previous is not None:
            if previous != rule:
                raise ValueError('shift rule revision already has different parameters')
            return previous
        data = {channel.value: rule.for_channel(channel).to_record() for channel in CHANNELS}
        connection.execute(
            'INSERT INTO shift_rules(shift_id, revision, calibrations) VALUES (?, ?, ?)',
            (shift_id, revision, json.dumps(data)),
        )
    return rule


def load_rule(connection, shift_id, revision):
    require_identifier(shift_id, 'shift_id')
    require_identifier(revision, 'revision')
    rule = _find_rule(connection, shift_id, revision)
    if rule is None:
        raise KeyError(f'unknown shift rule {(shift_id, revision)!r}')
    return rule
