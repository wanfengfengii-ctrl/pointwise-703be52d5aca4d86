from dataclasses import dataclass

from rules.channels import Channel, require_channel


@dataclass(frozen=True)
class FirstReceipt:
    receipt_id: str
    request_id: str
    report_id: str
    page_id: str
    channel: Channel
    shift_id: str
    revision: str

    def to_record(self):
        return {
            'receipt_id': self.receipt_id, 'request_id': self.request_id, 'report_id': self.report_id,
            'page_id': self.page_id, 'channel': self.channel.value,
            'shift_id': self.shift_id, 'revision': self.revision,
        }


def report_number(request_id):
    return f'report:{request_id}'


def make_first_receipt(request, report_id):
    return FirstReceipt(
        f'receipt:{request.request_id}', request.request_id, report_id,
        request.page_id, request.channel, request.shift_id, request.revision,
    )


def restore_first_receipt(data):
    return FirstReceipt(**dict(data, channel=require_channel(data['channel'])))
