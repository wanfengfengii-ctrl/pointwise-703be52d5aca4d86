# 青汀环境监测站双路班档

巡检员开启设备页，采录员将振动和温度原据送入各自班档，夜班按号领表。设备槽有限，清槽后原物理记录即不可读。站端接管整页证据后可释放槽位，两路班表仍分别按各自进度签发。

## 运行

要求 Python 3.11，使用标准库。项目根目录运行完整回归和演示。

```sh
python3 -B check_project.py
python3 -B -m capture.session demo
```

`check_project.py` 由运行平台物化。四个测试模块也可直接运行 `python3 -B -m unittest discover`。演示用临时数据目录，按 `examples/shift.json` 的顺序输出逐步 JSON 结果。将样例导入指定的新目录时使用以下命令。该目录可供随后的操作重开，样例的开页编号为一次性编号。

```sh
python3 -B -m capture.session run station-data examples/shift.json
```

`run` 读取含 `operations` 数组的 JSON。每个对象含 `op` 和该操作的具名参数。`--offline` 以停源方式打开站端。脚本也可用 `reopen` 和布尔参数 `online` 在步骤间重开。有效操作见下面的会话接口，另有 `page_status`、`origin` 和 `summarize`。`configure` 操作的 `calibrations` 对象以 `vibration` 和 `temperature` 为键，值含 `raw_unit`、`unit`、`scale`、`offset`、`minimum` 和 `maximum`。文件内有理数可写成整数或字符串，CLI 输出有理数以约分字符串表示。

## 一次交班

1. 配置本班的两路固定修订，开页时指定槽、唯一原页号、规程引用和有序拍列表。
2. 第 41 拍先到振动 7。采入后振动可签，换算为 15/4 mm/s。温度请求仍待第 41 拍。
3. 设备送到第 41 拍温度 11 并结页。设备上看得见温度时，站端仍需完成一次采录才有清槽资格。
4. 双路证据及结页均已接管后，清槽可先于温度签发。演示让设备清槽后失答，随后把原槽用于下一页。
5. 重开凭旧页号和清槽请求号重查清槽终局。温度发布前注入一次失败，站端保持原据和待办。
6. 停源后用原温度请求续签，得到温度 11、29/2 °C、原出处和首执。振动照原号领取。
7. 下一页含温度明确缺测和振动零读。夜班只复查温度缺测，振动零读计为实测。
8. 夜班按领取的班表核对各路实测和缺测拍数。下一班另登记规程，既签班表继续使用原修订的快照。

## Python 接口

```python
from capture.session import StationSession
from rules.calibration import Calibration

with StationSession('station-data') as station:
    station.configure('day', 'r1', {
        'vibration': Calibration('count', 'mm/s', '1/2', '1/4', -100, 100),
        'temperature': Calibration('count', '°C', '3/2', -2, -100, 100),
    })
    station.open_page('P41', 'S1', 'day', 'r1', [41])
    station.feed('P41', 'reading', beat=41, channel='vibration', raw=7)
    progress = station.collect('P41')
    signed = station.settle('v41', 'P41', 'vibration')
    history = station.lookup(signed.publication.report.report_id)
```

- `configure(shift_id, revision, calibrations)` 登记不可变规程。同参可重放。`load_rule(shift_id, revision)` 读取原修订。
- `open_page(page_id, slot, shift_id, revision, beats)` 绑定设备原序。拍号为非负整数，布局至少含一拍且拍号唯一。默认槽为 `S1` 和 `S2`，新设备可在第一次打开时指定 `slots`。原页号一次使用。
- `feed(page_id, kind, *, beat=None, channel=None, raw=None)` 接受 `reading`、`missing` 和 `close`。实测需要原值，明确缺测只需要拍和通道，结页仅需页号。仍可读的相同事实重放给出原出处。`page_status(page_id)` 返回 `recording`、`closed` 或 `cleared`。
- `collect(page_id)` 返回持久采录证据和两路 `ChannelOffer`。已知页停源时返回已留存证据及 `offline` 状态，清槽后从站端留存返回 `cleared`。同一批设备读数全部校验后才并入。闭区间内的有理数均可采，浮点和布尔值拒绝。
- `settle(request_id, page_id, channel)` 返回 `pending` 及待补拍，或 `published` 及完整班表与首执。请求绑定原页、通道和规程。成果号为 `report:` 加原请求号。发布前失败可使用 `fail_before_commit=True` 注入，脚本中可用 `expect_publish_failure` 核对该失败并继续。
- `release(request_id, page_id)` 返回 `awaiting_evidence`、`awaiting_device` 或 `released`。证据齐备后绑定清槽请求，每页使用同一清槽号。`lose_reply=True` 在设备实际提交之后注入失答。后续调用可查询已完成终局。
- `lookup(report_id)` 领取固定快照和首执。`origin(report_id, beat)` 定位原出处。`follow_up(report_id)` 依原序列出正式缺测。`summarize(report_ids)` 按班次和通道汇总所领班表，同原页同路计一次。

未知页、规程或未签成果用 `KeyError` 报告。非法输入及绑定冲突用 `ValueError` 报告。停源时设备写操作用 `DeviceOffline` 报告，班档和历史读取继续可用。设备写入、站端采录、班档接纳与班档发布各有独立提交边界。`device.sqlite3` 与 `station.sqlite3` 存于运行数据目录，重复打开只补缺失结构并保留记录。设备原始值和换算值以约分的分子分母保存，原序与先后到达次序分别保留。
