from dataclasses import dataclass

from recorder.pages import layout_from_row, page_row
from recorder.slots import SlotBinding, vacate_slot
from recorder.store import DeviceStore
from rules.channels import require_identifier


class ReleaseReplyLost(RuntimeError):
    pass


@dataclass(frozen=True)
class ReleaseOutcome:
    request_id: str
    page_id: str
    slot: str


def read_release_outcome(store: DeviceStore, request_id, page_id):
    require_identifier(request_id, 'request_id')
    require_identifier(page_id, 'page_id')
    row = store.connection.execute(
        'SELECT request_id, page_id, slot FROM device_releases WHERE request_id = ?', (request_id,),
    ).fetchone()
    if row is None:
        return None
    if row['page_id'] != page_id:
        raise ValueError('device release request belongs to another page')
    return ReleaseOutcome(row['request_id'], row['page_id'], row['slot'])


def clear_page(store: DeviceStore, request_id, page_id, *, lose_reply=False):
    with store.transaction() as db:
        previous = read_release_outcome(store, request_id, page_id)
        if previous is not None:
            return previous
        row = page_row(db, page_id)
        if row['cleared']:
            raise ValueError('page was cleared under another request')
        if row['closed_sequence'] is None:
            raise ValueError('device can only clear a closed page')
        layout = layout_from_row(row)
        vacate_slot(store, SlotBinding(layout.slot, page_id))
        db.execute('DELETE FROM device_events WHERE page_id = ?', (page_id,))
        db.execute('UPDATE device_pages SET cleared = 1 WHERE page_id = ?', (page_id,))
        db.execute(
            'INSERT INTO device_releases(request_id, page_id, slot) VALUES (?, ?, ?)',
            (request_id, page_id, layout.slot),
        )
        outcome = ReleaseOutcome(request_id, page_id, layout.slot)
    if lose_reply:
        raise ReleaseReplyLost(f'device committed clear for {page_id!r}')
    return outcome
