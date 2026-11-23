from fractions import Fraction

from tests import StationTestCase, calibrations


class ReviewTests(StationTestCase):
    def test_shift_history_reopen_initialization(self):
        first = self.start_41()
        self.finish_41()
        self.station.collect('P41')
        self.station.settle('v41', 'P41', 'vibration')
        self.assertEqual(self.station.release('clear41', 'P41').status, 'released')
        old = self.station.lookup('report:v41')
        next_rule = self.station.configure('night', 'r2', calibrations(scale='2', offset='0'))
        self.station.open_page('P42', 'S1', 'night', 'r2', [42])
        self.station.feed('P42', 'reading', beat=42, channel='vibration', raw=7)
        self.station.feed('P42', 'missing', beat=42, channel='temperature')
        self.station.feed('P42', 'close')
        self.station.collect('P42')
        self.station.settle('v42', 'P42', 'vibration')
        self.assertEqual(self.station.release('clear42', 'P42').status, 'released')
        self.reopen()
        self.assertEqual(self.station.configure('night', 'r2', calibrations(scale='2', offset='0')), next_rule)
        self.assertEqual(self.station.page_status('P41'), 'cleared')
        self.reopen(online=False)
        self.assertEqual(self.station.load_rule('night', 'r2'), next_rule)
        self.assertEqual(self.station.load_rule('day', 'r1').vibration.scale, Fraction(1, 2))
        old_read = self.station.lookup('report:v41')
        new_read = self.station.lookup('report:v42')
        self.assertEqual(old, old_read)
        self.assertEqual(old_read.origin(41), first.origin)
        self.assertEqual(self.station.origin('report:v42', 42).page_id, 'P42')
        totals = self.station.summarize(['report:v42', 'report:v41'])
        self.scenario(
            ('shift_history', 'shift_history.reopen_initialization'),
            {'old_scale_offset': ['1/2', '1/4'], 'new_scale_offset': ['2', '0'], 'raw_in_both': 7},
            [['day', 'r1', '15/4', 'receipt:v41'], ['night', 'r2', '14', 'receipt:v42']],
            [[item.report.request.shift_id, item.report.request.revision, str(item.report.rows[0].converted),
              item.receipt.receipt_id] for item in (old_read, new_read)],
            [['day', 'vibration', 1, 0], ['night', 'vibration', 1, 0]],
            [[item.shift_id, item.channel, item.measured, item.missing] for item in totals],
            ['rules/shifts.py', 'desk/report.py', 'review/reports.py', 'review/shift_summary.py'],
            'Day converts 7 to 15/4, night converts 7 to 14. Each signed report contains one measured beat.',
        )

    def test_review_order_zero_vs_missing(self):
        beats = [55, 41, 47]
        self.station.open_page('review', 'S1', 'day', 'r1', beats)
        tail = self.station.feed('review', 'missing', beat=47, channel='temperature')
        zero = self.station.feed('review', 'reading', beat=41, channel='temperature', raw=0)
        head = self.station.feed('review', 'missing', beat=55, channel='temperature')
        for beat in beats:
            self.station.feed('review', 'reading', beat=beat, channel='vibration', raw=7)
        self.station.feed('review', 'close')
        self.station.collect('review')
        self.station.settle('review-t', 'review', 'temperature')
        self.assertEqual(self.station.release('clear-review', 'review').status, 'released')
        self.reopen(online=False)
        history = self.station.lookup('report:review-t')
        self.assertEqual(history.origins, (head.origin, zero.origin, tail.origin))
        self.assertEqual(self.station.origin('report:review-t', 41), zero.origin)
        with self.assertRaises(KeyError):
            self.station.origin('report:review-t', 50)
        items = self.station.follow_up('report:review-t')
        self.assertEqual([item.origin for item in items], [head.origin, tail.origin])
        total, = self.station.summarize(['report:review-t', 'report:review-t'])
        self.scenario(
            ('review_order', 'review_order.zero_vs_missing'),
            {'layout': beats, 'arriving_temperature': [[47, 'missing'], [41, 0], [55, 'missing']]},
            [[55, 'missing', None, None], [41, 'reading', '0', '-2'], [47, 'missing', None, None]],
            [[row.beat, row.status, None if row.raw is None else str(row.raw),
              None if row.converted is None else str(row.converted)] for row in history.report.rows],
            {'follow_up': [[55, 3], [47, 1]], 'counts': [1, 2]},
            {'follow_up': [[item.beat, item.origin.sequence] for item in items], 'counts': [total.measured, total.missing]},
            ['desk/report.py', 'review/provenance.py', 'review/missing.py', 'review/shift_summary.py'],
            'The page order is 55, 41, 47. Zero converts to -2 and remains measured. Only sequences 3 and 1 are missing.',
        )
