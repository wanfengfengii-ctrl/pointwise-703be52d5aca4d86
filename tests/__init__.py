from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from capture.session import StationSession
from rules.calibration import Calibration


def calibrations(*, scale='1/2', offset='1/4'):
    return {
        'vibration': Calibration('count', 'mm/s', scale, offset, -100, 100),
        'temperature': Calibration('count', '°C', '3/2', -2, -100, 100),
    }


class StationTestCase(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory(prefix='qingting-test-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.station = StationSession(self.root)
        self.addCleanup(lambda: self.station.close())
        self.station.configure('day', 'r1', calibrations())

    def reopen(self, *, online=True):
        self.station.close()
        self.station = StationSession(self.root, online=online)

    def start_41(self):
        self.station.open_page('P41', 'S1', 'day', 'r1', [41])
        event = self.station.feed('P41', 'reading', beat=41, channel='vibration', raw=7)
        self.station.collect('P41')
        return event

    def finish_41(self, *, missing=False):
        if missing:
            event = self.station.feed('P41', 'missing', beat=41, channel='temperature')
        else:
            event = self.station.feed('P41', 'reading', beat=41, channel='temperature', raw=11)
        self.station.feed('P41', 'close')
        return event

    def scenario(self, case_ids, inputs, expected, actual, downstream_expected, downstream_actual, paths, oracle):
        self.assertEqual(actual, expected)
        self.assertEqual(downstream_actual, downstream_expected)
        for case_id in case_ids:
            print('POINTWISE_SCENARIO ' + json.dumps({
                'case_id': case_id, 'input': inputs, 'expected': expected, 'actual': actual,
                'downstream_expected': downstream_expected, 'downstream_actual': downstream_actual,
                'code_paths': paths, 'oracle': oracle,
            }, ensure_ascii=False), flush=True)
