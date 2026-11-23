from dataclasses import replace
from fractions import Fraction

from recorder.events import event_identity
from recorder.pages import PhysicalPage, read_page
from rules.calibration import Calibration
from tests import StationTestCase, calibrations


class RecordingTests(StationTestCase):
    def test_recorded_distinct_origins(self):
        self.station.open_page('pairs', 'S1', 'day', 'r1', [42, 41])
        first = self.station.feed('pairs', 'reading', beat=41, channel='vibration', raw=7)
        second = self.station.feed('pairs', 'reading', beat=42, channel='vibration', raw='14/2')
        with self.assertRaises(ValueError):
            self.station.feed('pairs', 'close')
        self.assertEqual(self.station.page_status('pairs'), 'recording')
        with self.assertRaises(ValueError):
            self.station.feed('pairs', 'reading', beat=43, channel='temperature', raw=0)
        self.station.feed('pairs', 'reading', beat=41, channel='temperature', raw=-100)
        self.station.feed('pairs', 'reading', beat=42, channel='temperature', raw=100)
        closed = self.station.feed('pairs', 'close')
        self.assertEqual(closed.origin.sequence, 5)
        self.assertEqual(self.station.feed('pairs', 'close'), closed)
        initial = self.station.collect('pairs')
        replay = self.station.feed('pairs', 'reading', beat=41, channel='vibration', raw='7/1')
        actual = self.station.collect('pairs')
        self.assertEqual(replay, first)
        self.assertNotEqual(event_identity(first), event_identity(second))
        self.assertEqual(initial, actual)
        self.assertEqual([e.raw for e in actual.progress.temperature.evidence], [100, -100])
        self.assertEqual(actual.progress.vibration.pending_beats, ())
        signed = self.station.settle('v-pairs', 'pairs', 'vibration').publication
        self.scenario(
            ('recorded', 'recorded.distinct_origins'),
            {'layout': [42, 41], 'vibration_arrival': [[41, 7], [42, 7], [41, 7]]},
            [[42, '7', 2], [41, '7', 1]],
            [[e.origin.beat, str(e.raw), e.origin.sequence] for e in actual.progress.vibration.evidence],
            [[42, '15/4', 2], [41, '15/4', 1]],
            [[r.beat, str(r.converted), r.origin.sequence] for r in signed.report.rows],
            ['recorder/pages.py', 'capture/evidence.py', 'capture/handoff.py', 'desk/report.py'],
            'Device layout gives 42 then 41. Arrival gives source sequences 2 then 1. Each 7 converts to 15/4.',
        )

    def test_invalid_preserve_first(self):
        first = self.start_41()
        for kind, raw in [('reading', 8), ('missing', None)]:
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.station.feed('P41', kind, beat=41, channel='vibration', raw=raw)
        self.assertEqual(read_page(self.station.device, 'P41').facts, (first,))
        later = self.station.feed('P41', 'reading', beat=41, channel='temperature', raw=11)
        snapshot = read_page(self.station.device, 'P41')
        conflict = replace(first, raw=Fraction(8))
        with self.assertRaises(ValueError):
            self.station.capture.save_evidence(PhysicalPage(snapshot.layout, (later, conflict), None))
        self.assertEqual(self.station.capture.load_evidence('P41').facts, (first,))
        actual = self.station.collect('P41')
        report = self.station.settle('v41', 'P41', 'vibration').publication.report
        self.scenario(
            ('invalid', 'invalid.preserve_first'),
            {'first_vibration': 7, 'conflicts': [8, 'explicit_missing'], 'later_temperature': 11},
            [7, 1], [int(actual.progress.vibration.evidence[0].raw), actual.progress.vibration.evidence[0].origin.sequence],
            ['7', '15/4', 'reading', 1],
            [str(report.rows[0].raw), str(report.rows[0].converted), report.rows[0].status, report.rows[0].origin.sequence],
            ['recorder/pages.py', 'capture/store.py', 'capture/evidence.py', 'desk/report.py'],
            'The first physical fact is sequence 1 at value 7. Both conflicting alternatives are rejected.',
        )

    def test_invalid_rule_and_range(self):
        for scale, lower, upper in [('1/0', -100, 100), ('2', 5, 4), (0.5, -100, 100)]:
            with self.subTest(scale=scale, lower=lower, upper=upper), self.assertRaises(ValueError):
                Calibration('count', 'mm/s', scale, 0, lower, upper)
        wrong_units = dict(calibrations(), vibration=Calibration('count', '°C', 1, 0, -100, 100))
        with self.assertRaises(ValueError):
            self.station.configure('bad', 'r1', wrong_units)
        with self.assertRaises(KeyError):
            self.station.load_rule('bad', 'r1')
        with self.assertRaises(ValueError):
            self.station.configure('day', 'r1', calibrations(scale='2'))
        self.assertEqual(self.station.load_rule('day', 'r1').vibration.scale, Fraction(1, 2))
        with self.assertRaises(KeyError):
            self.station.collect('unknown')
        first = self.start_41()
        with self.assertRaises(ValueError):
            self.station.feed('P41', 'reading', beat=41, channel='temperature', raw=False)
        self.station.feed('P41', 'reading', beat=41, channel='temperature', raw=101)
        self.station.feed('P41', 'close')
        with self.assertRaises(ValueError):
            self.station.collect('P41')
        retained = self.station.capture.load_evidence('P41')
        self.assertEqual(retained.facts, (first,))
        self.assertIsNone(retained.closed)
        wait = self.station.settle('t41', 'P41', 'temperature')
        self.scenario(
            ('invalid.rule_and_range',),
            {'unit_pair_attempt': ['count', '°C'], 'channel': 'vibration', 'temperature': 101, 'bounds': [-100, 100]},
            ['7', False], [str(retained.facts[0].raw), retained.closed is not None],
            ['pending', [41]], [wait.status, list(wait.pending_beats)],
            ['rules/shifts.py', 'rules/readings.py', 'capture/intake.py', 'desk/readiness.py'],
            '101 exceeds the closed upper bound of 100. The last legal capture contains only vibration.',
        )
