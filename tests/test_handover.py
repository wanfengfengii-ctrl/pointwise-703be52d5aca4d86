from fractions import Fraction

from capture.release import check_release_eligibility
from recorder.pages import PageCleared, read_page
from tests import StationTestCase, calibrations


class HandoverTests(StationTestCase):
    def test_late_evidence_handoff(self):
        vibration = self.start_41()
        signed = self.station.settle('v41', 'P41', 'vibration').publication
        self.assertEqual(signed.report.rows[0].raw, 7)
        self.assertEqual(self.station.lookup('report:v41').receipt, signed.receipt)
        waiting = self.station.settle('t41', 'P41', 'temperature')
        self.assertEqual(waiting.pending_beats, (41,))
        self.assertIsNone(waiting.publication)
        with self.assertRaises(KeyError):
            self.station.lookup('report:t41')
        self.assertEqual(self.station.release('clear41', 'P41').status, 'awaiting_evidence')
        temperature = self.finish_41()
        before_capture = self.station.release('clear41', 'P41')
        self.assertEqual(read_page(self.station.device, 'P41').facts[-1], temperature)
        self.assertFalse(check_release_eligibility(self.station.capture, 'P41'))
        self.assertEqual(self.station.settle('t41', 'P41', 'temperature').pending_beats, (41,))
        collected = self.station.collect('P41')
        self.assertEqual(collected.progress.temperature.evidence, (temperature,))
        self.assertTrue(check_release_eligibility(self.station.capture, 'P41'))
        released = self.station.release('clear41', 'P41')
        self.assertEqual(released.outcome.slot, 'S1')
        with self.assertRaises(PageCleared):
            read_page(self.station.device, 'P41')
        with self.assertRaises(KeyError):
            self.station.lookup('report:t41')
        self.reopen(online=False)
        self.assertEqual(self.station.collect('P41').source_status, 'offline')
        self.assertEqual(self.station.release('clear41', 'P41'), released)
        result = self.station.settle('t41', 'P41', 'temperature')
        self.assertEqual(self.station.settle('t41', 'P41', 'temperature'), result)
        self.assertEqual(self.station.settle('v41', 'P41', 'vibration').publication, signed)
        self.assertEqual(self.station.origin('report:v41', 41), vibration.origin)
        row = self.station.lookup('report:t41').report.rows[0]
        self.assertEqual(row.origin, temperature.origin)
        self.assertEqual(self.station.lookup('report:t41').receipt, result.publication.receipt)
        self.scenario(
            ('late', 'late.evidence_handoff'),
            {'page': 'P41', 'beat': 41, 'first_vibration': 7, 'late_temperature': 11},
            ['pending', 'awaiting_evidence', 'released'], [waiting.status, before_capture.status, released.status],
            ['11', '29/2', '°C', 2, 'receipt:t41'],
            [str(row.raw), str(row.converted), row.unit, row.origin.sequence, result.publication.receipt.receipt_id],
            ['capture/intake.py', 'capture/release.py', 'desk/intake.py', 'desk/report.py', 'review/reports.py'],
            'Temperature on device is the second fact. Its conversion is 3/2 times 11 minus 2.',
        )

    def test_late_exact_conversion(self):
        self.station.configure('precise', 'r1', calibrations(scale='1/3', offset='1/10'))
        self.station.open_page('P41', 'S1', 'precise', 'r1', [41])
        self.station.feed('P41', 'reading', beat=41, channel='vibration', raw=7)
        self.station.collect('P41')
        self.assertEqual(self.station.settle('t41', 'P41', 'temperature').pending_beats, (41,))
        signed = self.station.settle('v41', 'P41', 'vibration').publication
        self.finish_41()
        self.station.collect('P41')
        self.assertEqual(self.station.release('clear41', 'P41').status, 'released')
        self.reopen(online=False)
        old = self.station.lookup('report:v41')
        self.assertEqual(old.report, signed.report)
        self.assertEqual(old.receipt, signed.receipt)
        value = old.report.rows[0].converted
        self.assertEqual(value, Fraction(73, 30))
        resumed = self.station.settle('t41', 'P41', 'temperature')
        self.scenario(
            ('late.exact_conversion',), {'raw': 7, 'scale': '1/3', 'offset': '1/10'},
            [73, 30], [value.numerator, value.denominator],
            ['73/30', '29/2'], [str(value), str(resumed.publication.report.rows[0].converted)],
            ['rules/calibration.py', 'desk/report.py', 'review/reports.py'],
            'Use common denominator 30. The numerators 70 and 3 add to 73.',
        )

    def test_late_request_binding(self):
        self.start_41()
        signed = self.station.settle('v41', 'P41', 'vibration').publication
        self.finish_41()
        self.station.collect('P41')
        with self.assertRaises(ValueError):
            self.station.settle('v41', 'P41', 'temperature')
        with self.assertRaises(ValueError):
            self.station.settle('v41', 'other-page', 'vibration')
        repeated = self.station.settle('v41', 'P41', 'vibration').publication
        self.assertEqual(repeated, signed)
        temperature = self.station.settle('t41', 'P41', 'temperature').publication
        self.scenario(
            ('late.request_binding',), {'original': ['v41', 'P41', 'vibration'], 'altered_channel': 'temperature'},
            ['report:v41', 'receipt:v41'], [repeated.report.report_id, repeated.receipt.receipt_id],
            ['report:t41', 'receipt:t41', '11'],
            [temperature.report.report_id, temperature.receipt.receipt_id, str(temperature.report.rows[0].raw)],
            ['desk/intake.py', 'desk/store.py', 'desk/report.py'],
            'Request identifiers determine separate fixed report and receipt numbers.',
        )

    def test_missing_explicit_terminal(self):
        self.start_41()
        vibration = self.station.settle('v41', 'P41', 'vibration').publication
        temperature_wait = self.station.settle('t41', 'P41', 'temperature')
        self.assertEqual(temperature_wait.pending_beats, (41,))
        with self.assertRaises(KeyError):
            self.station.follow_up('report:t41')
        explicitly_missing = self.finish_41(missing=True)
        self.station.collect('P41')
        self.assertEqual(self.station.release('clear41', 'P41').status, 'released')
        self.reopen(online=False)
        temperature = self.station.settle('t41', 'P41', 'temperature').publication
        row = temperature.report.rows[0]
        self.assertEqual(row.origin, explicitly_missing.origin)
        follow_up = self.station.follow_up('report:t41')
        self.assertEqual(self.station.follow_up('report:v41'), ())
        self.assertEqual(self.station.lookup('report:v41').receipt, vibration.receipt)
        self.assertEqual(vibration.report.rows[0].raw, 7)
        self.scenario(
            ('missing', 'missing.explicit_terminal'),
            {'page': 'P41', 'beat': 41, 'vibration': 7, 'temperature_terminal': 'explicit_missing'},
            ['pending', 'missing', None, None, 2],
            [temperature_wait.status, row.status, row.raw, row.converted, row.origin.sequence],
            [['temperature', 41, 'P41', 2]],
            [[item.channel, item.beat, item.origin.page_id, item.origin.sequence] for item in follow_up],
            ['recorder/events.py', 'desk/readiness.py', 'desk/report.py', 'review/missing.py'],
            'Only the second device event explicitly confirms missing temperature at beat 41.',
        )
