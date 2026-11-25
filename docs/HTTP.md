# 阶段接口

`POST /api/stages/<stage>` 接收 `multipart/form-data`。字段 `sample=dirty`、`sample=clean` 或 `file=<CSV>` 三选一。阶段为 `intake`、`prepare`、`calculate`、`policy`、`balance`。预览不入库。

`POST /api/runs` 接收同样的输入，执行全批并返回运行报告。`GET /api/runs` 返回最近 50 批。`GET /api/runs/<id>` 查询一批和当前复核状态。

`PATCH /api/runs/<id>` 接收 `revision`、`status` 和 `note`。状态使用 `APPROVED` 或 `HELD`。冲突返回 409，客户端读取新版本后重试。补录先生效且改变估计后，仍用旧 `revision` 签发返回 409 `ESTIMATE_SUPERSEDED`，区别于普通并发冲突 `STALE_REVIEW`。

`POST /api/runs/<id>/backfills` 接收 JSON `revision`、`parcel`、`pickup_time`、`note`，登记晚到取件并重算：取件时刻不晚于观察截止按截止前补录（`IN_WINDOW`）重算滞留率；晚于截止按截止后补录（`POST_CUTOFF`）登记，本批仍算未取。同一柜件出现两个取件时刻且估值得出不同滞留率时，整批置为 `HELD`，原因为 `BACKFILL_AMBIGUOUS_HOLD`。每次补录都追加一条 revision 审计记录。

`GET /api/runs/<id>` 返回批次当前结论，并附 `revisions`（含状态、原因码、说明与补录细节）和 `backfills` 台账；`GET /api/runs` 列表每项给出最近一次结论原因 `reason`。

`GET /api/runs/<id>/evidence` 下载内含运行清单、行号隔离、业务证据、补录与 revision 台账和复核信息的 JSON。
