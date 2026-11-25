# 柜件滞留估计台

自提柜运营分析员可在工作台上传来源 CSV，对照规则判断差异，签发合格批次或记录暂缓意见。

用 Kaplan–Meier 估计仍未取件的比例，观察截止未领取件按右删失处理。晚到取件可在批次页补录：不晚于观察截止的补录触发重算，截止后取件本批仍算未取；同一柜件两个取件时刻估值不同则整批暂缓，补录先生效后旧估计不能签发，结论与原因在工作台台账可查。

## 启动

```sh
docker compose up --build -d --wait
```

打开 <http://localhost:18325>。页面预览带异常样例。选中阶段查看计算证据，选择可签发样例并执行批次，然后提交复核意见。

## 核验

```sh
docker compose exec -T app pytest -q
```

[输入字段](docs/DATA-DICTIONARY.md) · [阶段接口](docs/HTTP.md) · [操作步骤](docs/OPERATIONS.md) · [规则与测试](docs/DEPTH.md)
