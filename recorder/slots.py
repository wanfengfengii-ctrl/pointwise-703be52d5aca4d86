from dataclasses import dataclass


@dataclass(frozen=True)
class SlotBinding:
    slot: str
    page_id: str


def active_binding(store, slot):
    row = store.connection.execute(
        'SELECT page_id FROM device_slots WHERE slot = ?', (slot,),
    ).fetchone()
    if row is None:
        raise KeyError(f'unknown slot {slot!r}')
    return None if row['page_id'] is None else SlotBinding(slot, row['page_id'])


def occupy_slot(store, slot, page_id):
    """Bind an empty slot within the caller's device transaction."""
    if active_binding(store, slot) is not None:
        raise ValueError(f'slot {slot!r} is occupied')
    changed = store.connection.execute(
        'UPDATE device_slots SET page_id = ? WHERE slot = ? AND page_id IS NULL',
        (page_id, slot),
    ).rowcount
    if changed != 1:
        raise ValueError(f'slot {slot!r} is occupied')
    return SlotBinding(slot, page_id)


def vacate_slot(store, binding):
    """Vacate only the exact occupant inside the device clear transaction."""
    changed = store.connection.execute(
        'UPDATE device_slots SET page_id = NULL WHERE slot = ? AND page_id = ?',
        (binding.slot, binding.page_id),
    ).rowcount
    if changed != 1:
        raise ValueError('physical slot no longer contains the requested page')
