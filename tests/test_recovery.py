from capture.session import StationSession
from desk.readiness import evaluate_readiness
from desk.report import PublishInterrupted
from recorder.pages import PageCleared, read_page
from recorder.slots import active_binding
from recorder.terminals import read_release_outcome
from tests import StationTestCase


class RecoveryTests(StationTestCase):
    def test_failed_commit_atomic_reopen(self):
        self.start_41()
        vibration = self.station.settle('v41', 'P41', 'vibration').publication
        temperature = self.finish_41()
        self.station.collect('P41')
        self.assertEqual(self.station.release('clear41', 'P41').status, 'released')
        observations = []
        with StationSession(self.root, online=False) as observer:
            def fail_at_commit():
                self.assertIsNotNone(self.station.desk.load_publication('report:t41'))
                self.assertIsNotNone(self.station.desk.load_receipt('report:t41'))
                self.assertIsNone(observer.desk.load_publication('report:t41'))
                self.assertIsNone(observer.desk.load_receipt('report:t41'))
                self.assertEqual(observer.lookup('report:v41').receipt, vibration.receipt)
                observations.append('private_before_commit')
                raise PublishInterrupted('test checkpoint after both inserts')

            with self.assertRaises(PublishInterrupted):
                self.station.settle('t41', 'P41', 'temperature', before_commit=fail_at_commit)
            self.assertEqual(observations, ['private_before_commit'])
        self.reopen(online=False)
        with self.assertRaises(KeyError):
            self.station.lookup('report:t41')
        failed_publication = self.station.desk.load_publication('report:t41')
        failed_receipt = self.station.desk.load_receipt('report:t41')
        self.assertEqual(self.station.lookup('report:v41').receipt, vibration.receipt)
        retained = self.station.desk.load_intake('t41')
        self.assertEqual(retained.evidence, (temperature,))
        pending = evaluate_readiness(retained).pending_beats
        resumed = self.station.settle('t41', 'P41', 'temperature')
        replay = self.station.settle('t41', 'P41', 'temperature', fail_before_commit=True)
        self.assertEqual(replay, resumed)
        report = self.station.lookup('report:t41')
        self.assertEqual(report.report.rows[0].origin, temperature.origin)
        self.scenario(
            ('failed_commit', 'failed_commit.atomic_reopen'),
            {'temperature': 11, 'failure': 'after both inserts before commit', 'device_after_reopen': 'offline'},
            [None, None, []], [failed_publication, failed_receipt, list(pending)],
            ['11', '29/2', 'receipt:t41', 'receipt:v41'],
            [str(report.report.rows[0].raw), str(report.report.rows[0].converted), report.receipt.receipt_id,
             self.station.lookup('report:v41').receipt.receipt_id],
            ['capture/store.py', 'desk/store.py', 'desk/report.py', 'review/reports.py'],
            'An aborted transaction keeps neither publication row. The prior intake and vibration receipt already committed.',
        )

    def test_lost_reply_reused_slot(self):
        self.start_41()
        temperature = self.finish_41()
        self.station.collect('P41')
        lost = self.station.release('clear41', 'P41', lose_reply=True)
        self.assertIsNone(lost.outcome)
        outcome = read_release_outcome(self.station.device, 'clear41', 'P41')
        self.assertEqual(outcome.page_id, 'P41')
        with self.assertRaises(PageCleared):
            read_page(self.station.device, 'P41')
        self.station.open_page('P42', 'S1', 'day', 'r1', [42])
        later = self.station.feed('P42', 'reading', beat=42, channel='vibration', raw=9)
        self.reopen()
        confirmed = self.station.release('clear41', 'P41', lose_reply=True)
        self.assertEqual(confirmed.outcome, outcome)
        with self.assertRaises(ValueError):
            self.station.release('clear41', 'P42')
        with self.assertRaises(ValueError):
            self.station.open_page('P41', 'S2', 'day', 'r1', [41])
        self.assertEqual(self.station.page_status('P42'), 'recording')
        binding = active_binding(self.station.device, 'S1')
        collected = self.station.collect('P42')
        self.assertEqual(collected.progress.vibration.evidence, (later,))
        self.assertEqual(self.station.collect('P41').source_status, 'cleared')
        self.reopen(online=False)
        self.assertEqual(self.station.release('clear41', 'P41'), confirmed)
        old = self.station.settle('t41', 'P41', 'temperature').publication.report.rows[0]
        new = self.station.settle('v42', 'P42', 'vibration').publication.report.rows[0]
        self.assertEqual(old.origin, temperature.origin)
        self.scenario(
            ('lost_reply', 'lost_reply.reused_slot'),
            {'first_page': 'P41', 'next_page': 'P42', 'slot': 'S1', 'request': 'clear41'},
            ['awaiting_device', 'released', 'P41', 'P42'],
            [lost.status, confirmed.status, confirmed.outcome.page_id, binding.page_id],
            [['P41', '11', 2], ['P42', '9', 1]],
            [[row.origin.page_id, str(row.raw), row.origin.sequence] for row in (old, new)],
            ['recorder/slots.py', 'recorder/terminals.py', 'capture/release.py', 'desk/report.py'],
            'Clearing P41 was committed before P42 occupied S1. The old request can only resolve to P41.',
        )
