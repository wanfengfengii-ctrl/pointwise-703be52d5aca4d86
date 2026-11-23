from copy import deepcopy
import json


class DeliveryLink:
    """Keep individual transmissions in flight until the caller picks their outcome."""

    def __init__(self):
        self._flight = {}
        self._used = set()

    def hold(self, ticket, page_id, beat, channel, packet):
        if ticket in self._used:
            raise ValueError(f"ticket {ticket!r} is already on this link")
        if not isinstance(packet, dict):
            raise TypeError("packet must be a dictionary")
        self._flight[ticket] = {
            "kind": "reading", "ticket": ticket, "page_id": page_id,
            "beat": beat, "channel": channel, "packet": deepcopy(packet),
        }
        self._used.add(ticket)

    def arrive(self, ticket):
        return self._flight.pop(ticket)

    def discard(self, ticket):
        event = self._flight.pop(ticket)
        del event["packet"]
        event["kind"] = "discarded"
        return event


def main():
    link = DeliveryLink()
    packet = {"example_body": [7]}
    link.hold("T1", "P", 41, "vibration", packet)
    link.hold("T2", "P", 41, "vibration", packet)
    packet["example_body"][0] = 99
    dropped = link.discard("T1")
    delivered = link.arrive("T2")
    if dropped != {"kind": "discarded", "ticket": "T1", "page_id": "P",
                   "beat": 41, "channel": "vibration"}:
        raise AssertionError("discard must carry only the original delivery identity")
    if delivered != dict(dropped, kind="reading", ticket="T2", packet={"example_body": [7]}):
        raise AssertionError("one ticket was dropped, but the other must deliver its original packet")
    for event in (dropped, delivered):
        print(json.dumps(event, ensure_ascii=False))


if __name__ == "__main__":
    main()
