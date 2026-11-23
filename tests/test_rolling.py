from fractions import Fraction

from capture.session import DeviceOffline
from desk.readiness import BeatStatus
from desk.report import PublishInterrupted
from examples.rolling_link import DeliveryLink
from recorder.slots import active_binding
from tests import StationTestCase


class RollingTests(StationTestCase):
    def open_p(self):
        opened = self.station.open_rolling('P', 'S1', 'day', 'r1', [41, 42])
        self.assertIsNone(opened['cover'])
        self.assertEqual(opened['layout']['beats'], [41, 42])
        self.station.feed('P', 'reading', beat=41, channel='vibration', raw=7)
        self.station.feed('P', 'reading', beat=42, channel='vibration', raw=9)
        self.station.feed('P', 'missing', beat=41, channel='temperature')
        self.station.feed('P', 'missing', beat=42, channel='temperature')
        self.station.feed('P', 'close')

    def dispatch_pair(self, link):
        for ticket in ('T1', 'T2'):
            packet = self.station.dispatch(ticket, 'P', 41, 'vibration')
            self.assertEqual(packet['fact']['raw'], [7, 1])
            link.hold(ticket, 'P', 41, 'vibration', packet)

    def cover_p_with_q(self):
        opened = self.station.open_rolling('Q', 'S1', 'day', 'r1', [41])
        cover = opened['cover']
        self.assertEqual(cover['kind'], 'cover')
        self.assertEqual(cover['page_id'], 'P')
        self.assertEqual(cover['body']['layout']['beats'], [41, 42])
        self.assertEqual([ticket['ticket'] for ticket in cover['body']['tickets']], ['T1', 'T2'])
        self.assertTrue(all('raw' not in fact for fact in cover['body']['facts']))
        self.assertEqual(
            [fact['kind'] for fact in cover['body']['facts']],
            ['reading', 'reading', 'missing', 'missing'],
        )
        self.station.feed('Q', 'reading', beat=41, channel='vibration', raw=99)
        return cover

    def test_cover_late_arrival(self):
        self.open_p()
        link = DeliveryLink()
        self.dispatch_pair(link)
        cover = self.cover_p_with_q()
        self.assertEqual(active_binding(self.station.device, 'S1').page_id, 'Q')
        with self.assertRaises(ValueError):
            self.station.dispatch('T3', 'P', 41, 'vibration')
        self.assertIsNone(self.station.dispatch('T1', 'P', 41, 'vibration'))
        self.assertEqual(self.station.page_status('Q'), 'recording')
        self.station.ingest(cover)
        self.assertIsNone(self.station.ingest(cover))
        discarded = link.discard('T1')
        self.station.ingest(discarded)
        self.assertIsNone(self.station.ingest(discarded))
        temperature = self.station.settle_rolling('tP', 'P', 'temperature')
        self.assertEqual(temperature.status, 'published')
        temperature_rows = temperature.publication.report.rows
        self.assertEqual([row.status for row in temperature_rows], [BeatStatus.MISSING, BeatStatus.MISSING])
        self.assertEqual([row.origin.sequence for row in temperature_rows], [3, 4])
        waiting = self.station.settle_rolling('vP', 'P', 'vibration')
        self.assertEqual(waiting.status, 'pending')
        self.assertEqual(waiting.pending_beats, (41,))
        self.assertIsNone(waiting.publication)
        with self.assertRaises(KeyError):
            self.station.lookup_rolling('report:vP')
        arrived = link.arrive('T2')
        self.station.ingest(arrived)
        signed = self.station.settle_rolling('vP', 'P', 'vibration')
        self.assertEqual(signed.status, 'published')
        rows = signed.publication.report.rows
        self.assertEqual(rows[0].status, BeatStatus.READING)
        self.assertEqual(rows[0].raw, Fraction(7))
        self.assertEqual(rows[0].converted, Fraction(15, 4))
        self.assertEqual((rows[0].origin.page_id, rows[0].origin.sequence), ('P', 1))
        self.assertEqual(rows[1].status, BeatStatus.TRANSPORT_LOST)
        self.assertIsNone(rows[1].raw)
        self.assertIsNone(rows[1].converted)
        self.assertEqual((rows[1].origin.page_id, rows[1].origin.sequence), ('P', 2))
        self.assertEqual(signed.publication.receipt.receipt_id, 'receipt:vP')
        self.assertEqual(self.station.settle_rolling('vP', 'P', 'vibration'), signed)
        with self.assertRaises(ValueError):
            self.station.settle_rolling('vP', 'P', 'temperature')
        with self.assertRaises(ValueError):
            self.station.settle_rolling('vP', 'Q', 'vibration')
        with self.assertRaises(KeyError):
            self.station.lookup('report:vP')
        self.reopen(online=False)
        history = self.station.lookup_rolling('report:vP')
        self.assertEqual(history.report, signed.publication.report)
        self.assertEqual(history.receipt, signed.publication.receipt)
        self.assertEqual([origin.sequence for origin in history.origins], [1, 2])
        self.scenario(
            ('rolling', 'rolling.cover_late_arrival'),
            {'covered_page': 'P', 'covering_page': 'Q', 'beats': [41, 42],
             'tickets': {'T1': 'discarded', 'T2': 'late_arrival'}, 'late_raw': 7},
            ['pending', [41], ['missing', 'missing']],
            [waiting.status, list(waiting.pending_beats), [row.status for row in temperature_rows]],
            [['reading', '7', '15/4', 'P', 1], ['transport_lost', None, None, 'P', 2]],
            [[row.status, None if row.raw is None else str(row.raw),
              None if row.converted is None else str(row.converted),
              row.origin.page_id, row.origin.sequence] for row in rows],
            ['recorder/rolling.py', 'capture/rolling.py', 'desk/rolling.py', 'review/rolling.py'],
            'Q takes the slot. T1 is discarded and T2 arrives late, so beat 41 signs as 7 -> 15/4 '
            'while beat 42 had no packet and is transport_lost. Origins stay with P.',
        )

    def test_all_discarded_transport_lost(self):
        self.open_p()
        link = DeliveryLink()
        self.dispatch_pair(link)
        cover = self.cover_p_with_q()
        self.station.ingest(cover)
        self.station.ingest(link.discard('T1'))
        self.station.ingest(link.discard('T2'))
        signed = self.station.settle_rolling('vP', 'P', 'vibration')
        self.assertEqual(signed.status, 'published')
        rows = signed.publication.report.rows
        self.assertEqual([row.status for row in rows], [BeatStatus.TRANSPORT_LOST, BeatStatus.TRANSPORT_LOST])
        self.assertEqual([row.origin.sequence for row in rows], [1, 2])
        self.scenario(
            ('rolling.all_discarded',),
            {'covered_page': 'P', 'tickets': {'T1': 'discarded', 'T2': 'discarded'}},
            [['transport_lost', None], ['transport_lost', None]],
            [[row.status, row.raw] for row in rows],
            [[1, 'P'], [2, 'P']],
            [[row.origin.sequence, row.origin.page_id] for row in rows],
            ['capture/rolling.py', 'desk/rolling.py'],
            'Both tickets for beat 41 are discarded and beat 42 never had one, so every beat is transport_lost.',
        )

    def test_packet_before_cover_same_result(self):
        self.open_p()
        link = DeliveryLink()
        self.dispatch_pair(link)
        cover = self.cover_p_with_q()
        arrived = link.arrive('T2')
        self.station.ingest(arrived)
        self.station.ingest(cover)
        self.station.ingest(link.discard('T1'))
        signed = self.station.settle_rolling('vP', 'P', 'vibration')
        rows = signed.publication.report.rows
        self.assertEqual(rows[0].status, BeatStatus.READING)
        self.assertEqual(rows[0].raw, Fraction(7))
        self.assertEqual(rows[0].converted, Fraction(15, 4))
        self.assertEqual(rows[1].status, BeatStatus.TRANSPORT_LOST)
        self.scenario(
            ('rolling.packet_before_cover',),
            {'covered_page': 'P', 'arrival_order': ['T2_packet', 'cover', 'T1_discard']},
            ['reading', '7', '15/4'], [rows[0].status, str(rows[0].raw), str(rows[0].converted)],
            ['transport_lost', 'P', 2], [rows[1].status, rows[1].origin.page_id, rows[1].origin.sequence],
            ['capture/rolling.py', 'desk/rolling.py'],
            'Delivering the T2 packet before the coverage notification settles to the same report.',
        )

    def test_misbound_and_conflicting_deliveries(self):
        self.open_p()
        link = DeliveryLink()
        self.dispatch_pair(link)
        cover = self.cover_p_with_q()
        self.station.ingest(cover)
        q_packet = self.station.dispatch('TQ', 'Q', 41, 'vibration')
        forged = {'kind': 'reading', 'ticket': 'T2', 'page_id': 'P', 'beat': 41,
                  'channel': 'vibration', 'packet': q_packet}
        with self.assertRaises(ValueError):
            self.station.ingest(forged)
        with self.assertRaises(ValueError):
            self.station.ingest({'kind': 'discarded', 'ticket': 'T1', 'page_id': 'P',
                                 'beat': 42, 'channel': 'vibration'})
        arrived = link.arrive('T2')
        self.station.ingest(arrived)
        tampered = dict(arrived, packet={'ticket': 'T2', 'fact': dict(arrived['packet']['fact'], raw=[8, 1])})
        with self.assertRaises(ValueError):
            self.station.ingest(tampered)
        with self.assertRaises(ValueError):
            self.station.ingest({'kind': 'discarded', 'ticket': 'T2', 'page_id': 'P',
                                 'beat': 41, 'channel': 'vibration'})
        self.assertIsNone(self.station.ingest(arrived))
        signed = self.station.settle_rolling('vP', 'P', 'vibration')
        rows = signed.publication.report.rows
        self.assertEqual(rows[0].raw, Fraction(7))
        self.assertEqual(rows[0].converted, Fraction(15, 4))
        self.scenario(
            ('rolling.misbound_rejected',),
            {'forged_packet': 'Q beat 41 raw 99 presented as P ticket T2',
             'conflicts': ['tampered raw 8', 'discard after arrival']},
            [7, '15/4'], [int(rows[0].raw), str(rows[0].converted)],
            [None, None, 'transport_lost'], [rows[1].raw, rows[1].converted, rows[1].status],
            ['capture/rolling.py', 'desk/rolling.py'],
            'A packet naming another page is rejected, and the correct T2 is still accepted afterwards. '
            'Replays and conflicts on an accepted ticket keep the original receipt.',
        )

    def test_publish_failure_resume_offline(self):
        self.open_p()
        link = DeliveryLink()
        self.dispatch_pair(link)
        cover = self.cover_p_with_q()
        self.station.ingest(cover)
        discarded = link.discard('T1')
        self.station.ingest(discarded)
        arrived = link.arrive('T2')
        self.station.ingest(arrived)
        with self.assertRaises(PublishInterrupted):
            self.station.settle_rolling('vP', 'P', 'vibration', fail_before_commit=True)
        with self.assertRaises(KeyError):
            self.station.lookup_rolling('report:vP')
        self.reopen(online=False)
        with self.assertRaises(KeyError):
            self.station.lookup_rolling('report:vP')
        signed = self.station.settle_rolling('vP', 'P', 'vibration')
        self.assertEqual(signed.status, 'published')
        self.reopen(online=False)
        history = self.station.lookup_rolling('report:vP')
        self.assertEqual(history.report, signed.publication.report)
        self.assertEqual(history.receipt, signed.publication.receipt)
        self.assertIsNone(self.station.ingest(cover))
        self.assertIsNone(self.station.ingest(discarded))
        self.assertIsNone(self.station.ingest(arrived))
        self.assertEqual(self.station.settle_rolling('vP', 'P', 'vibration'), signed)
        self.scenario(
            ('rolling.publish_failure_resume',),
            {'failure': 'interrupted before commit', 'resume': 'offline reopen'},
            [None, 'published'], [None, signed.status],
            ['report:vP', 'receipt:vP', '15/4'],
            [history.report.report_id, history.receipt.receipt_id, str(history.report.rows[0].converted)],
            ['desk/rolling.py', 'review/rolling.py', 'capture/session.py'],
            'An interrupted commit publishes neither report nor receipt; the retained evidence '
            'still signs after an offline reopen and replays change nothing.',
        )

    def test_rolling_flow_guards(self):
        self.open_p()
        for operation in (
            lambda: self.station.collect('P'),
            lambda: self.station.settle('vP', 'P', 'vibration'),
            lambda: self.station.release('clearP', 'P'),
            lambda: self.station.settle_rolling('vP', 'P', 'vibration'),
        ):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                operation()
        self.station.open_page('N', 'S2', 'day', 'r1', [51])
        self.station.feed('N', 'reading', beat=51, channel='vibration', raw=4)
        self.station.feed('N', 'missing', beat=51, channel='temperature')
        self.station.feed('N', 'close')
        with self.assertRaises(ValueError):
            self.station.open_rolling('R', 'S2', 'day', 'r1', [52])
        self.assertEqual(active_binding(self.station.device, 'S2').page_id, 'N')
        with self.assertRaises(ValueError):
            self.station.settle_rolling('vN', 'N', 'vibration')
        with self.assertRaises(ValueError):
            self.station.dispatch('TN', 'N', 51, 'vibration')
        with self.assertRaises(KeyError):
            self.station.lookup_rolling('report:vN')
        with self.assertRaises(KeyError):
            self.station.settle_rolling('vX', 'unknown', 'vibration')
        normal = self.station.collect('N')
        self.assertEqual(normal.progress.vibration.pending_beats, ())
        signed = self.station.settle('vN', 'N', 'vibration').publication
        self.assertEqual(signed.report.rows[0].converted, Fraction(9, 4))
        binding_s2 = active_binding(self.station.device, 'S2').page_id
        status_p, status_n = self.station.page_status('P'), self.station.page_status('N')
        self.reopen(online=False)
        with self.assertRaises(DeviceOffline):
            self.station.open_rolling('X', 'S1', 'day', 'r1', [1])
        with self.assertRaises(DeviceOffline):
            self.station.dispatch('TX', 'P', 41, 'vibration')
        self.scenario(
            ('rolling.flow_guards',),
            {'rolling_page': 'P', 'normal_page': 'N', 'normal_raw': 4},
            ['9/4', 'N'], [str(signed.report.rows[0].converted), binding_s2],
            ['closed', 'closed'], [status_p, status_n],
            ['capture/session.py', 'recorder/rolling.py', 'desk/rolling.py'],
            'Rolling pages refuse the normal collect, settle, and release flow; a normal page keeps its '
            'slot against coverage and still settles normally; offline device writes report DeviceOffline.',
        )

    def test_unsealed_rolling_page_cannot_be_covered(self):
        self.station.open_rolling('U', 'S1', 'day', 'r1', [41])
        self.station.feed('U', 'reading', beat=41, channel='vibration', raw=7)
        with self.assertRaises(ValueError):
            self.station.open_rolling('V', 'S1', 'day', 'r1', [42])
        self.assertEqual(active_binding(self.station.device, 'S1').page_id, 'U')
        self.assertEqual(self.station.page_status('U'), 'recording')
        self.station.feed('U', 'missing', beat=41, channel='temperature')
        self.station.feed('U', 'close')
        self.assertEqual(self.station.page_status('U'), 'closed')
        self.scenario(
            ('rolling.unsealed_cover',),
            {'open_page': 'U', 'attempt': 'cover before close'},
            ['U', 'recording'], [active_binding(self.station.device, 'S1').page_id, 'recording'],
            ['closed'], [self.station.page_status('U')],
            ['recorder/rolling.py', 'recorder/slots.py'],
            'An occupied slot only covers a sealed rolling page; the failed attempt preserves the slot.',
        )

    def test_dispatch_guards(self):
        self.open_p()
        packet = self.station.dispatch('T1', 'P', 41, 'vibration')
        self.assertEqual(packet['fact']['raw'], [7, 1])
        self.assertIsNone(self.station.dispatch('T1', 'P', 41, 'vibration'))
        with self.assertRaises(ValueError):
            self.station.dispatch('T1', 'P', 42, 'vibration')
        with self.assertRaises(ValueError):
            self.station.dispatch('T4', 'P', 41, 'temperature')
        with self.assertRaises(ValueError):
            self.station.dispatch('T5', 'P', 43, 'vibration')
        with self.assertRaises(KeyError):
            self.station.dispatch('T6', 'unknown', 41, 'vibration')
        self.scenario(
            ('rolling.dispatch_guards',),
            {'ticket': 'T1', 'replays': 'same parameters return None', 'conflict': 'same ticket other beat'},
            [[7, 1], None], [packet['fact']['raw'], self.station.dispatch('T1', 'P', 41, 'vibration')],
            ['reading', 1], [packet['fact']['kind'], packet['fact']['origin']['sequence']],
            ['recorder/rolling.py'],
            'Only recorded readings on a still-readable rolling page dispatch; accepted tickets replay '
            'to None and reject different parameters.',
        )
