import argparse
from dataclasses import asdict, is_dataclass
from fractions import Fraction
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from capture.handoff import prepare_offer
from capture.intake import collect
from capture.release import release
from capture.rolling import RollingStore, ingest as ingest_rolling, initialize_rolling_store
from capture.store import CaptureStore, initialize_capture_store
from desk.intake import SettlementRequest
from desk.report import PublishInterrupted, settle
from desk.rolling import (
    RollingDeskStore, initialize_rolling_desk_store, settle_rolling as settle_rolling_desk,
)
from desk.store import DeskStore, initialize_desk_store
from recorder import pages
from recorder import rolling as rolling_device
from recorder.store import DEFAULT_SLOTS, open_connection, open_device_store
from review.missing import follow_up
from review.reports import lookup
from review.rolling import lookup_rolling as lookup_rolling_review
from review.shift_summary import summarize_shift
from rules.calibration import Calibration
from rules.channels import require_channel, require_identifier
from rules.shifts import configure, initialize_shift_store, load_rule


class DeviceOffline(RuntimeError):
    pass


def _interrupt_publish():
    raise PublishInterrupted('publication interrupted before commit')


class StationSession:
    def __init__(self, root, *, online=True, slots=DEFAULT_SLOTS):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.device = None
        self.connection = open_connection(self.root / 'station.sqlite3')
        try:
            initialize_shift_store(self.connection)
            initialize_capture_store(self.connection)
            initialize_desk_store(self.connection)
            initialize_rolling_store(self.connection)
            initialize_rolling_desk_store(self.connection)
            self.capture = CaptureStore(self.connection)
            self.desk = DeskStore(self.connection)
            self.rolling = RollingStore(self.connection)
            self.rolling_desk = RollingDeskStore(self.connection)
            if online:
                self.device = open_device_store(self.root / 'device.sqlite3', slots)
        except BaseException:
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, exception_type, exception, traceback):
        self.close()

    def close(self):
        if self.device is not None:
            self.device.close()
            self.device = None
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def _require_device(self):
        if self.device is None:
            raise DeviceOffline('recorder is offline')
        return self.device

    def configure(self, shift_id, revision, calibrations):
        return configure(self.connection, shift_id, revision, calibrations)

    def load_rule(self, shift_id, revision):
        return load_rule(self.connection, shift_id, revision)

    def open_page(self, page_id, slot, shift_id, revision, beats):
        self.load_rule(shift_id, revision)
        layout = pages.PageLayout(page_id, slot, shift_id, revision, beats)
        return pages.open_page(self._require_device(), layout)

    def feed(self, page_id, kind, *, beat=None, channel=None, raw=None):
        return pages.feed(self._require_device(), page_id, kind, beat=beat, channel=channel, raw=raw)

    def page_status(self, page_id):
        return pages.page_status(self._require_device(), page_id)

    def _is_normal_page(self, page_id):
        if self.capture.load_evidence(page_id) is not None:
            return True
        if self.device is not None:
            return rolling_device.page_kind(self.device, page_id) == 'normal'
        return False

    def _require_normal_flow(self, page_id):
        require_identifier(page_id, 'page_id')
        if self.rolling.has_page(page_id):
            raise ValueError(f'page {page_id!r} belongs to the rolling flow')
        if self.device is not None and rolling_device.page_kind(self.device, page_id) == 'rolling':
            raise ValueError(f'page {page_id!r} belongs to the rolling flow')

    def collect(self, page_id):
        self._require_normal_flow(page_id)
        return collect(self.capture, self.device, page_id)

    def settle(self, request_id, page_id, channel, *, fail_before_commit=False, before_commit=None):
        require_identifier(request_id, 'request_id')
        require_identifier(page_id, 'page_id')
        channel = require_channel(channel)
        previous = self.desk.load_intake(request_id)
        if previous is not None and (previous.request.page_id, previous.request.channel) != (page_id, channel):
            raise ValueError('settlement request was already bound to different parameters')
        self._require_normal_flow(page_id)
        offer = prepare_offer(self.capture, page_id, channel)
        layout = offer.layout
        request = SettlementRequest(request_id, page_id, channel, layout.shift_id, layout.revision)
        if fail_before_commit and before_commit is not None:
            raise ValueError('choose one publication checkpoint')
        checkpoint = _interrupt_publish if fail_before_commit else before_commit
        return settle(self.desk, request, offer, before_commit=checkpoint)

    def release(self, request_id, page_id, *, lose_reply=False):
        self._require_normal_flow(page_id)
        return release(self.capture, self.device, request_id, page_id, lose_reply=lose_reply)

    def open_rolling(self, page_id, slot, shift_id, revision, beats):
        self.load_rule(shift_id, revision)
        layout = pages.PageLayout(page_id, slot, shift_id, revision, beats)
        return rolling_device.open_rolling(self._require_device(), layout)

    def dispatch(self, ticket, page_id, beat, channel):
        return rolling_device.dispatch(self._require_device(), ticket, page_id, beat, channel)

    def ingest(self, event):
        return ingest_rolling(self.rolling, event)

    def settle_rolling(self, request_id, page_id, channel, *, fail_before_commit=False):
        require_identifier(request_id, 'request_id')
        require_identifier(page_id, 'page_id')
        channel = require_channel(channel)
        if not self.rolling.has_page(page_id):
            if self._is_normal_page(page_id):
                raise ValueError(f'page {page_id!r} is not part of the rolling flow')
            if self.device is not None and rolling_device.is_rolling_page(self.device, page_id):
                raise ValueError(f'rolling page {page_id!r} has no coverage evidence yet')
        checkpoint = _interrupt_publish if fail_before_commit else None
        return settle_rolling_desk(
            self.rolling, self.rolling_desk, request_id, page_id, channel, before_commit=checkpoint,
        )

    def lookup_rolling(self, report_id):
        return lookup_rolling_review(self.rolling_desk, report_id)

    def lookup(self, report_id):
        return lookup(self.desk, report_id)

    def origin(self, report_id, beat):
        return self.lookup(report_id).origin(beat)

    def follow_up(self, report_id):
        return follow_up(self.desk, report_id)

    def summarize(self, report_ids):
        return summarize_shift(self.lookup(report_id) for report_id in report_ids)


def _perform(session, operation, arguments):
    methods = {
        'configure': session.configure, 'load_rule': session.load_rule, 'open_page': session.open_page,
        'feed': session.feed, 'page_status': session.page_status, 'collect': session.collect,
        'settle': session.settle, 'release': session.release, 'lookup': session.lookup,
        'origin': session.origin, 'follow_up': session.follow_up, 'summarize': session.summarize,
        'open_rolling': session.open_rolling, 'dispatch': session.dispatch, 'ingest': session.ingest,
        'settle_rolling': session.settle_rolling, 'lookup_rolling': session.lookup_rolling,
    }
    if operation not in methods:
        raise ValueError(f'unknown station operation {operation!r}')
    if operation == 'configure':
        arguments['calibrations'] = {
            channel: Calibration(**params) for channel, params in arguments['calibrations'].items()
        }
    return methods[operation](**arguments)


def run_scenario(root, document, *, online=True):
    session = StationSession(root, online=online)
    outcomes = []
    try:
        for row in document['operations']:
            arguments = dict(row)
            operation = arguments.pop('op')
            expected_failure = arguments.pop('expect_publish_failure', False)
            if operation == 'reopen':
                session.close()
                session = StationSession(root, **arguments)
                result = {'online': session.device is not None}
            else:
                try:
                    result = _perform(session, operation, arguments)
                except PublishInterrupted as error:
                    if not expected_failure:
                        raise
                    result = {'error': type(error).__name__, 'message': str(error)}
                else:
                    if expected_failure:
                        raise ValueError('expected publication checkpoint did not fail')
            outcomes.append({'op': operation, 'result': result})
    finally:
        session.close()
    return outcomes


def _json_default(value):
    if isinstance(value, Fraction):
        return str(value)
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f'unsupported scenario result {type(value).__name__}')


def run_rolling_demo(root):
    """Hand over a sealed rolling page's slot while its packets are still in flight."""
    try:
        from examples.rolling_link import DeliveryLink
    except ImportError:  # allow running away from the repository root
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from examples.rolling_link import DeliveryLink
    outcomes = []

    def record(operation, result):
        outcomes.append({'op': operation, 'result': result})

    link = DeliveryLink()
    with StationSession(root) as station:
        record('configure', station.configure('day', 'r1', {
            'vibration': Calibration('count', 'mm/s', '1/2', '1/4', -100, 100),
            'temperature': Calibration('count', '°C', '3/2', -2, -100, 100),
        }))
        record('open_rolling', station.open_rolling('P', 'S1', 'day', 'r1', [41, 42]))
        record('feed', station.feed('P', 'reading', beat=41, channel='vibration', raw=7))
        record('feed', station.feed('P', 'reading', beat=42, channel='vibration', raw=9))
        record('feed', station.feed('P', 'missing', beat=41, channel='temperature'))
        record('feed', station.feed('P', 'missing', beat=42, channel='temperature'))
        record('feed', station.feed('P', 'close'))
        for ticket in ('T1', 'T2'):
            packet = station.dispatch(ticket, 'P', 41, 'vibration')
            link.hold(ticket, 'P', 41, 'vibration', packet)
            record('dispatch', packet)
        opened = station.open_rolling('Q', 'S1', 'day', 'r1', [41])
        record('open_rolling', opened)
        record('feed', station.feed('Q', 'reading', beat=41, channel='vibration', raw=99))
        record('ingest', station.ingest(opened['cover']))
        record('ingest', station.ingest(link.discard('T1')))
        record('settle_rolling', station.settle_rolling('tP', 'P', 'temperature'))
        record('settle_rolling', station.settle_rolling('vP', 'P', 'vibration'))
        record('ingest', station.ingest(link.arrive('T2')))
        record('settle_rolling', station.settle_rolling('vP', 'P', 'vibration'))
    with StationSession(root, online=False) as station:
        record('lookup_rolling', station.lookup_rolling('report:vP'))
        record('lookup_rolling', station.lookup_rolling('report:tP'))
    return outcomes


def main(argv=None):
    parser = argparse.ArgumentParser(description='Run the station recorder and shift desks.')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('demo', help='run the deterministic station shift in a fresh directory')
    commands.add_parser('rolling-demo', help='run the rolling slot handover in a fresh directory')
    run = commands.add_parser('run', help='apply a JSON operation list to a data directory')
    run.add_argument('data_dir', type=Path)
    run.add_argument('scenario', type=Path)
    run.add_argument('--offline', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.command == 'demo':
            scenario = Path(__file__).resolve().parents[1] / 'examples' / 'shift.json'
            document = json.loads(scenario.read_text(encoding='utf-8'))
            with TemporaryDirectory(prefix='qingting-demo-') as directory:
                outcomes = run_scenario(directory, document)
        elif args.command == 'rolling-demo':
            with TemporaryDirectory(prefix='qingting-rolling-demo-') as directory:
                outcomes = run_rolling_demo(directory)
        else:
            document = json.loads(args.scenario.read_text(encoding='utf-8'))
            outcomes = run_scenario(args.data_dir, document, online=not args.offline)
    except (KeyError, LookupError, ValueError, TypeError, OSError, DeviceOffline, PublishInterrupted) as error:
        parser.exit(1, f'{type(error).__name__}: {error}\n')
    for outcome in outcomes:
        print(json.dumps(outcome, ensure_ascii=False, default=_json_default))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
