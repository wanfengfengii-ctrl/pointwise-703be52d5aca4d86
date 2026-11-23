from dataclasses import dataclass

from capture.evidence import Evidence, PageEvidence, channel_coverage
from capture.store import CaptureStore
from recorder.pages import PageLayout
from rules.channels import Channel, require_channel, require_identifier


@dataclass(frozen=True)
class ChannelOffer:
    layout: PageLayout
    channel: Channel
    evidence: tuple[Evidence, ...]

    @property
    def pending_beats(self):
        return channel_coverage(PageEvidence(self.layout, self.evidence), self.channel)


@dataclass(frozen=True)
class HandoffProgress:
    vibration: ChannelOffer
    temperature: ChannelOffer


def _offer(evidence, channel):
    by_beat = {fact.origin.beat: fact for fact in evidence.facts if fact.channel == channel}
    ordered = tuple(by_beat[beat] for beat in evidence.layout.beats if beat in by_beat)
    return ChannelOffer(evidence.layout, channel, ordered)


def prepare_offer(store: CaptureStore, page_id, channel):
    channel = require_channel(channel)
    require_identifier(page_id, 'page_id')
    evidence = store.load_evidence(page_id)
    if evidence is None:
        raise KeyError(f'page {page_id!r} has no station evidence')
    return _offer(evidence, channel)


def handoff_progress(evidence: PageEvidence):
    return HandoffProgress(_offer(evidence, Channel.VIBRATION), _offer(evidence, Channel.TEMPERATURE))
