# 滚动页采录

普通班继续按 README.md 办理。以下调用属于 `capture.session.StationSession` 的滚动页流程。使用现有班次规程、页号、槽位、原拍序和两路事件。采录站暂停接件时，记录器仍要复用已封滚动页的槽位。

## 设备发件和覆盖

`open_rolling(page_id, slot, shift_id, revision, beats)` 返回字典，`layout` 是 `PageLayout.to_record()` 的记录，`cover` 是覆盖通知。空槽开页时 `cover` 为 `None`。占用中的槽只允许覆盖已封的滚动页。页号仍只使用一次，`feed` 的实测、缺测、结页接口和检查仍适用。非法开页应保全原槽。

实际覆盖须让新页占槽，并在同一设备提交边界截止旧页发件、销毁旧页正文和固定覆盖事实。覆盖通知是可保存和重投的 JSON 字典，包含 `kind='cover'`、被覆盖的 `page_id` 和字典 `body`。覆盖记录须保留旧页原序、逐位原出处及源端是实测还是明确缺测的事实。通知不携带旧读值。设备发件和覆盖按设备实际受理先后判定，站库暂停接件不得阻止覆盖。

`dispatch(ticket, page_id, beat, channel)` 为仍可读取的滚动页上的一条实测发包。首次受理返回交给传输夹具的 JSON 字典，包含 `ticket` 和 `fact`。`fact` 沿用 `recorder.events.fact_to_record` 的实测格式，原值是约分后的分子分母对。包和通知可以带所需的业务证据。已受理票号同参重投返回 `None`，沿用原投递，异参拒绝。覆盖后向旧页申请新票拒绝。同一原读的不同票各有独立的传输结果。包在站端通过 `ingest` 接纳前只算在途，发件过程不得另存一份可用于实收的原读正文。

## 接件和出表

`ingest(event)` 接收覆盖通知，或 `examples.rolling_link.DeliveryLink` 生成的单包事件。单包事件含 `kind`、`ticket`、`page_id`、`beat`、`channel`。`kind='reading'` 还含原字典 `packet`，`kind='discarded'` 只有投递身份。夹具只决定指定票的送达或丢弃。覆盖、丢弃和原包可以任意先后到站。核对原页、拍、通道、投递身份和原出处，错绑或冲突拒收并保全已接纳证据，同件重投无新增实收。只有 `ingest` 接纳的合格原包可作实测依据。

`settle_rolling(request_id, page_id, channel, *, fail_before_commit=False)` 沿用 `SettlementResult` 的 `status`、`pending_beats` 和 `publication`。无覆盖依据不得签发。已接纳覆盖通知后，`pending` 携带本路仍待回的拍，按原页顺序排列，`publication` 为 `None`。本路齐证就返回 `published`，无需等待另一通道。源端明确缺测记 `missing`。源端实测只要有一份合格原包已实收就记 `reading`。源端实测尚无合格实收时，仅在能确认没有任何合格原包仍可到达后记 `transport_lost`，其余继续待回。

报告和首执沿用普通班表的字段及编号约定，按原拍序列出各拍终态及原出处。`reading` 给出原值及冻结规程的精确换算，另两种终态的原值与换算值为 `None`。同一请求号同页同路复领原件，异参拒绝。`fail_before_commit=True` 在新报告和首执提交前抛出 `desk.report.PublishInterrupted`，两者均不可领取，已接纳证据仍可续签。`lookup_rolling(report_id)` 返回与普通领取一样含 `report`、`receipt`、`origins` 的固定结果，未知或未签成果报 `KeyError`。已接齐证据后，可以用 `StationSession(root, online=False)` 续签和领取。普通 `collect`、`release` 和 `settle` 不得用于滚动页，滚动报告用 `lookup_rolling` 领取。非法操作和绑定冲突报 `ValueError`，设备写入停源时沿用 `DeviceOffline`。

## 交班样例

沿用 README.md 的 `day/r1` 规程。S1 开滚动页 P，原序为 41、42。振动分别实测 7、9，温度两拍均由设备报明确缺测，随后结页。依次发 T1、T2，均指向 P 的 41 拍振动，42 不发包。每次首次发件用 `link.hold(ticket, page_id, beat, channel, packet)` 暂存。站库尚未接件时在同槽开滚动页 Q，令 Q 的 41 拍实写 99。Q 开页返回 P 的覆盖通知。

1. 先 `ingest` P 的覆盖通知和 `link.discard('T1')`。温度可签两条 `missing`，振动仍待 41。42 无票可到，终态为 `transport_lost`。
2. 再 `ingest(link.arrive('T2'))`。振动可签，41 为 `reading`、原值 7、换算 `15/4`、出处仍属 P，42 为 `transport_lost` 并保留 P 的原出处。若改为丢弃 T2，41 也为 `transport_lost`。在送达分支把 T2 的原包先于覆盖通知送交，定稿结果相同。
3. 把实际 Q 原包错接到 P 的投递时应拒收，后续正确 T2 仍可接纳。覆盖后向 P 发新票也应拒绝，Q 照常采集。
4. 振动已齐证时注入发布失败，原号查不到新班表或首执。停源重开后续签，再次重开领同一份报告与首执。重复投递先前的覆盖、丢弃或送达事件不改变原件。

保留 `check_project.py` 的完整原回归。沿现有 `.pointwise-runtime.json` 的 CLI 清单接入滚动交班核验和演示。`verify` 运行原回归并核验样例中的覆盖、迟到、丢弃及失败续签，核验失败须非零退出。`demo` 展示原槽已由 Q 接用后，P 仍能签出迟到原读、传输丢失并在停源后按号领取。已有清单的 `version`、`kind` 和 `setup` 约定沿用。
