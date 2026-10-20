# BENCHMARKS

Measured numbers for the FairPay Engine, produced by the verification harness
in this repository. Every figure below is copied from a run that actually
happened on the machine and date named in its section. Nothing here is
estimated, rounded, or carried over from an earlier run. Where a measurement
did not run, its section says so and is otherwise empty.

## Machine and toolchain

All measurements in this file were taken on the same machine, on the same day.

```
Date:          2026-10-20 (IST, UTC+05:30)
Model:         HP HP Laptop 15s-du3xxx
CPU:           11th Gen Intel(R) Core(TM) i5-1135G7 @ 2.40GHz
Cores/Threads: 4 / 8
Max clock:     2419 MHz
RAM:           7.76 GB
OS:            Microsoft Windows 11 Home Single Language 10.0.26200 (64-bit)
Docker:        Docker version 28.5.1, build e180ab8
Python:        3.14.7 (tags/v3.14.7:823f032, Aug  5 2026, 10:51:32) [MSC v.1944 64 bit (AMD64)]
Interpreter:   D:\FairPay-Engine\.venv\Scripts\python.exe
pytest:        9.1.1
pytest-cov:    7.1.0
coverage:      7.16.2
```

The laptop was on mains power and otherwise idle apart from the containers
named in each section. Wall-clock timings on a 4-core mobile CPU are not
comparable to CI hardware; they are recorded because the harness prints them,
not as a performance claim.

---

## 1. Test suite runs from any working directory

`Backend/test_calculations.py` previously began with

```python
sys.path.insert(0, 'D:/FairPay-Engine/Backend')
```

an absolute path on one machine, so the file could not be collected from a
fresh clone anywhere else. It now resolves its own location:

```python
sys.path.insert(0, str(Path(__file__).resolve().parent))
```

The other five test and validation modules in `Backend/` already did this, so
no other file needed changing (checked with `grep -rn "sys.path" Backend/*.py`).

### Measurement: collection and run from an unrelated working directory

Command, run from `C:\Users\kisho` rather than from the repository:

```
cd C:\Users\kisho
D:\FairPay-Engine\.venv\Scripts\python.exe -m pytest D:\FairPay-Engine\Backend\test_calculations.py -q -p no:cacheprovider
```

Date: 2026-10-20. Machine: as above. Exit code: 0.

Raw output:

```
.........                                                                [100%]
9 passed in 0.13s
```

Result: 9 passed, 0 failed, 0 skipped. The suite is runnable from a fresh clone
on any machine.

---

## 2. Synthetic verification harness

### What it needs

Worked out by reading the import chain and the call path of
`Backend/synthetic_verification.py`:

- **No services at all.** The script invokes the audit graph's `versioning`,
  `calculation`, `findings` and `controller` nodes directly and deliberately
  skips the `evidence` node, so no document reader and no LLM is called. It
  sets `"use_local_laws": True`, so the labour-law lookup is served from the
  in-process local fixture instead of Qdrant. It never imports `db`, so
  Postgres is not touched. The run below was made with the backend API not
  running.
- **No environment variables are required.** `versioning.py` and
  `evidence_extractor.py` call `load_dotenv()` at import time and read
  `QDRANT_URL`, `QDRANT_API_KEY`, `GROQ_API_KEY`, `FAIRPAY_VISION_MODEL` and
  `TESSERACT_PATH`, but none of those values is used on this code path. The run
  below happened to have `Backend/.env` present; nothing in its output depends
  on that.
- **Python packages**: the whole of `Backend/requirements.txt`, because
  `orchestrator.py` imports `evidence_extractor` (which pulls in `groq`,
  `instructor`, `pdfplumber`, `pytesseract`, `presidio-analyzer`) and
  `law_store` (which pulls in `qdrant-client` and `fastembed`) at module load
  time, even though neither is exercised here. Install with
  `pip install -r Backend/requirements.txt`.
- **Data**: `synthetic_data/` must hold `partners.json`, `settlements.json`,
  `earnings.json`, `ground_truth.json` and `ground_truth_layer2.json`. All five
  are committed.

So: create a virtualenv, `pip install -r Backend/requirements.txt`, and run the
command below. Nothing else to provide.

### Measurement

Command:

```
cd Backend
python synthetic_verification.py --data-root ../synthetic_data
```

Date: 2026-10-20. Machine: as above. Containers running: `fairpay_db` only, and
unused by this script. Exit code: 0.

Headline figures, read off the report below:

| Figure | Value |
| --- | --- |
| Partners processed | 20 |
| Transactions input | 106 |
| Engine success rate | 20/20 (100.0%) |
| Layer 1 reconciliation F1 | 80.96% |
| Layer 1 precision | 84.73% |
| Layer 1 recall | 79.63% |
| Layer 2 findings F1 | 0.00% (0 of 8 expected findings detected) |
| Layer 3 records processed | 114 |
| Layer 3 exception rate | 100.00% |

Raw unedited output, stdout and stderr combined in the order printed. The
`WARNING:root:` lines come from Python's logging handler and reach stderr
before the report is printed.

<details>
<summary>Full output, 1397 lines</summary>

```
D:\FairPay-Engine\Backend\law_store.py:167: UserWarning: Payload indexes have no effect in the local Qdrant. Please use server Qdrant if you need payload indexes.
  client.create_payload_index(name, field_name=field_name, field_schema=schema)
WARNING:root:Contract resolution failed for transaction SETL00011 (platform=UberEats, date=2026-08-20)
WARNING:root:No contract found for transaction SETL00011, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00088 (platform=UberEats, date=2026-07-09)
WARNING:root:No contract found for transaction SETL00088, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00016 (platform=Zomato, date=2026-08-14)
WARNING:root:No contract found for transaction SETL00016, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00060 (platform=Zomato, date=2026-08-03)
WARNING:root:No contract found for transaction SETL00060, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00103 (platform=Zomato, date=2026-07-10)
WARNING:root:No contract found for transaction SETL00103, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00104 (platform=Zomato, date=2026-07-10)
WARNING:root:No contract found for transaction SETL00104, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00004 (platform=Swiggy, date=2026-08-10)
WARNING:root:No contract found for transaction SETL00004, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00012 (platform=Swiggy, date=2026-07-20)
WARNING:root:No contract found for transaction SETL00012, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00020 (platform=Swiggy, date=2026-08-07)
WARNING:root:No contract found for transaction SETL00020, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00028 (platform=Swiggy, date=2026-08-06)
WARNING:root:No contract found for transaction SETL00028, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00029 (platform=Swiggy, date=2026-08-06)
WARNING:root:No contract found for transaction SETL00029, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00036 (platform=Swiggy, date=2026-06-22)
WARNING:root:No contract found for transaction SETL00036, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00063 (platform=Swiggy, date=2026-06-02)
WARNING:root:No contract found for transaction SETL00063, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00044 (platform=Instamart, date=2026-08-26)
WARNING:root:No contract found for transaction SETL00044, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00076 (platform=Instamart, date=2026-09-01)
WARNING:root:No contract found for transaction SETL00076, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00086 (platform=Zomato, date=2026-08-08)
WARNING:root:No contract found for transaction SETL00086, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction EARN00077 (platform=Instamart, date=2026-08-08)
WARNING:root:No contract found for transaction EARN00077, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00058 (platform=UberEats, date=2026-06-29)
WARNING:root:No contract found for transaction SETL00058, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00007 (platform=Instamart, date=2026-06-30)
WARNING:root:No contract found for transaction SETL00007, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00079 (platform=Swiggy, date=2026-07-02)
WARNING:root:No contract found for transaction SETL00079, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction EARN00056 (platform=Instamart, date=2026-06-30)
WARNING:root:No contract found for transaction EARN00056, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00018 (platform=Instamart, date=2026-06-25)
WARNING:root:No contract found for transaction SETL00018, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00043 (platform=Instamart, date=2026-08-23)
WARNING:root:No contract found for transaction SETL00043, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00068 (platform=Instamart, date=2026-08-27)
WARNING:root:No contract found for transaction SETL00068, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00069 (platform=Instamart, date=2026-08-27)
WARNING:root:No contract found for transaction SETL00069, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00077 (platform=Instamart, date=2026-08-11)
WARNING:root:No contract found for transaction SETL00077, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00093 (platform=Instamart, date=2026-06-30)
WARNING:root:No contract found for transaction SETL00093, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00094 (platform=Instamart, date=2026-06-30)
WARNING:root:No contract found for transaction SETL00094, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00102 (platform=Instamart, date=2026-08-21)
WARNING:root:No contract found for transaction SETL00102, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction EARN00019 (platform=Instamart, date=2026-06-21)
WARNING:root:No contract found for transaction EARN00019, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00015 (platform=Swiggy, date=2026-07-29)
WARNING:root:No contract found for transaction SETL00015, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00021 (platform=Swiggy, date=2026-08-15)
WARNING:root:No contract found for transaction SETL00021, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00051 (platform=UberEats, date=2026-07-03)
WARNING:root:No contract found for transaction SETL00051, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00057 (platform=UberEats, date=2026-07-05)
WARNING:root:No contract found for transaction SETL00057, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00070 (platform=Swiggy, date=2026-08-15)
WARNING:root:No contract found for transaction SETL00070, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00006 (platform=Instamart, date=2026-08-29)
WARNING:root:No contract found for transaction SETL00006, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00030 (platform=Zomato, date=2026-06-27)
WARNING:root:No contract found for transaction SETL00030, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00031 (platform=Zomato, date=2026-06-27)
WARNING:root:No contract found for transaction SETL00031, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00061 (platform=Zomato, date=2026-07-03)
WARNING:root:No contract found for transaction SETL00061, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00010 (platform=Instamart, date=2026-06-27)
WARNING:root:No contract found for transaction SETL00010, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00078 (platform=UberEats, date=2026-07-06)
WARNING:root:No contract found for transaction SETL00078, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00099 (platform=Swiggy, date=2026-07-31)
WARNING:root:No contract found for transaction SETL00099, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00017 (platform=Swiggy, date=2026-08-27)
WARNING:root:No contract found for transaction SETL00017, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00056 (platform=Swiggy, date=2026-08-11)
WARNING:root:No contract found for transaction SETL00056, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00091 (platform=Swiggy, date=2026-08-30)
WARNING:root:No contract found for transaction SETL00091, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00092 (platform=Swiggy, date=2026-08-31)
WARNING:root:No contract found for transaction SETL00092, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00096 (platform=Swiggy, date=2026-06-13)
WARNING:root:No contract found for transaction SETL00096, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00097 (platform=Swiggy, date=2026-06-13)
WARNING:root:No contract found for transaction SETL00097, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00106 (platform=Swiggy, date=2026-07-29)
WARNING:root:No contract found for transaction SETL00106, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction EARN00102 (platform=Swiggy, date=2026-07-21)
WARNING:root:No contract found for transaction EARN00102, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00009 (platform=UberEats, date=2026-08-14)
WARNING:root:No contract found for transaction SETL00009, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00046 (platform=Swiggy, date=2026-09-01)
WARNING:root:No contract found for transaction SETL00046, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00080 (platform=Instamart, date=2026-08-04)
WARNING:root:No contract found for transaction SETL00080, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00081 (platform=Instamart, date=2026-08-04)
WARNING:root:No contract found for transaction SETL00081, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00001 (platform=Swiggy, date=2026-07-25)
WARNING:root:No contract found for transaction SETL00001, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00033 (platform=Instamart, date=2026-06-23)
WARNING:root:No contract found for transaction SETL00033, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00049 (platform=Swiggy, date=2026-08-23)
WARNING:root:No contract found for transaction SETL00049, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00065 (platform=Instamart, date=2026-08-10)
WARNING:root:No contract found for transaction SETL00065, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction EARN00040 (platform=Instamart, date=2026-07-30)
WARNING:root:No contract found for transaction EARN00040, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00041 (platform=Instamart, date=2026-08-19)
WARNING:root:No contract found for transaction SETL00041, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00064 (platform=Instamart, date=2026-08-27)
WARNING:root:No contract found for transaction SETL00064, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00072 (platform=Instamart, date=2026-07-13)
WARNING:root:No contract found for transaction SETL00072, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction EARN00032 (platform=Instamart, date=2026-07-30)
WARNING:root:No contract found for transaction EARN00032, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00055 (platform=UberEats, date=2026-07-30)
WARNING:root:No contract found for transaction SETL00055, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction EARN00014 (platform=UberEats, date=2026-06-07)
WARNING:root:No contract found for transaction EARN00014, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00013 (platform=UberEats, date=2026-08-25)
WARNING:root:No contract found for transaction SETL00013, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00062 (platform=Swiggy, date=2026-07-28)
WARNING:root:No contract found for transaction SETL00062, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00098 (platform=Instamart, date=2026-06-24)
WARNING:root:No contract found for transaction SETL00098, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00050 (platform=UberEats, date=2026-07-30)
WARNING:root:No contract found for transaction SETL00050, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00067 (platform=UberEats, date=2026-07-27)
WARNING:root:No contract found for transaction SETL00067, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00032 (platform=UberEats, date=2026-08-31)
WARNING:root:No contract found for transaction SETL00032, using gross_amount as fallback
WARNING:root:Contract resolution failed for transaction SETL00066 (platform=Swiggy, date=2026-07-29)
WARNING:root:No contract found for transaction SETL00066, using gross_amount as fallback
Loading synthetic data from: ../synthetic_data

======================================================================
RUNNING SYNTHETIC VERIFICATION ON 20 PARTNERS
Architecture: Earnings+Settlements -> ENGINE -> PREDICTION <-> GROUND_TRUTH
Layer 1: Transaction Reconciliation (anomaly_type) <- PRIMARY
Layer 2: Contract/Legal/Economic Findings          <- SECONDARY
======================================================================
  Running audit engine on PTR0001...
    [DEBUG] PTR0001: versioning done, applicable_laws=40
    [DEBUG] PTR0001: calculation done, payment_calcs=2
    [DEBUG] PTR0001: findings done, findings=True
    [DEBUG] PTR0001: controller done, decisions=2, summary=True
  [DEBUG] PTR0001: Contract compliance predictions: 0 (expected: 0)
  [DEBUG] PTR0001: Legal risk predictions: 0 (expected: 0)
  [DEBUG] PTR0001: Economic concern predictions: 0 (expected: 0)
  Running audit engine on PTR0002...
    [DEBUG] PTR0002: versioning done, applicable_laws=80
    [DEBUG] PTR0002: calculation done, payment_calcs=4
    [DEBUG] PTR0002: findings done, findings=True
    [DEBUG] PTR0002: controller done, decisions=4, summary=True
  [DEBUG] PTR0002: Contract compliance predictions: 0 (expected: 0)
  [DEBUG] PTR0002: Legal risk predictions: 0 (expected: 0)
  [DEBUG] PTR0002: Economic concern predictions: 0 (expected: 0)
  Running audit engine on PTR0003...
    [DEBUG] PTR0003: versioning done, applicable_laws=140
    [DEBUG] PTR0003: calculation done, payment_calcs=7
    [DEBUG] PTR0003: findings done, findings=True
    [DEBUG] PTR0003: controller done, decisions=7, summary=True
  [DEBUG] PTR0003: Contract compliance predictions: 0 (expected: 0)
  [DEBUG] PTR0003: Legal risk predictions: 0 (expected: 1)
  [DEBUG] PTR0003: Economic concern predictions: 0 (expected: 0)
  Running audit engine on PTR0004...
    [DEBUG] PTR0004: versioning done, applicable_laws=120
    [DEBUG] PTR0004: calculation done, payment_calcs=6
    [DEBUG] PTR0004: findings done, findings=True
    [DEBUG] PTR0004: controller done, decisions=6, summary=True
  [DEBUG] PTR0004: Contract compliance predictions: 0 (expected: 0)
  [DEBUG] PTR0004: Legal risk predictions: 0 (expected: 0)
  [DEBUG] PTR0004: Economic concern predictions: 0 (expected: 0)
  Running audit engine on PTR0005...
    [DEBUG] PTR0005: versioning done, applicable_laws=120
    [DEBUG] PTR0005: calculation done, payment_calcs=6
    [DEBUG] PTR0005: findings done, findings=True
    [DEBUG] PTR0005: controller done, decisions=6, summary=True
  [DEBUG] PTR0005: Contract compliance predictions: 0 (expected: 0)
  [DEBUG] PTR0005: Legal risk predictions: 0 (expected: 0)
  [DEBUG] PTR0005: Economic concern predictions: 0 (expected: 0)
  Running audit engine on PTR0006...
    [DEBUG] PTR0006: versioning done, applicable_laws=140
    [DEBUG] PTR0006: calculation done, payment_calcs=7
    [DEBUG] PTR0006: findings done, findings=True
    [DEBUG] PTR0006: controller done, decisions=7, summary=True
  [DEBUG] PTR0006: Contract compliance predictions: 0 (expected: 1)
  [DEBUG] PTR0006: Legal risk predictions: 0 (expected: 1)
  [DEBUG] PTR0006: Economic concern predictions: 0 (expected: 1)
  Running audit engine on PTR0007...
    [DEBUG] PTR0007: versioning done, applicable_laws=60
    [DEBUG] PTR0007: calculation done, payment_calcs=3
    [DEBUG] PTR0007: findings done, findings=True
    [DEBUG] PTR0007: controller done, decisions=3, summary=True
  [DEBUG] PTR0007: Contract compliance predictions: 0 (expected: 0)
  [DEBUG] PTR0007: Legal risk predictions: 0 (expected: 0)
  [DEBUG] PTR0007: Economic concern predictions: 0 (expected: 0)
  Running audit engine on PTR0008...
    [DEBUG] PTR0008: versioning done, applicable_laws=180
    [DEBUG] PTR0008: calculation done, payment_calcs=9
    [DEBUG] PTR0008: findings done, findings=True
    [DEBUG] PTR0008: controller done, decisions=9, summary=True
  [DEBUG] PTR0008: Contract compliance predictions: 0 (expected: 0)
  [DEBUG] PTR0008: Legal risk predictions: 0 (expected: 0)
  [DEBUG] PTR0008: Economic concern predictions: 0 (expected: 0)
  Running audit engine on PTR0009...
    [DEBUG] PTR0009: versioning done, applicable_laws=160
    [DEBUG] PTR0009: calculation done, payment_calcs=8
    [DEBUG] PTR0009: findings done, findings=True
    [DEBUG] PTR0009: controller done, decisions=8, summary=True
  [DEBUG] PTR0009: Contract compliance predictions: 0 (expected: 0)
  [DEBUG] PTR0009: Legal risk predictions: 0 (expected: 0)
  [DEBUG] PTR0009: Economic concern predictions: 0 (expected: 0)
  Running audit engine on PTR0010...
    [DEBUG] PTR0010: versioning done, applicable_laws=80
    [DEBUG] PTR0010: calculation done, payment_calcs=4
    [DEBUG] PTR0010: findings done, findings=True
    [DEBUG] PTR0010: controller done, decisions=4, summary=True
  [DEBUG] PTR0010: Contract compliance predictions: 0 (expected: 0)
  [DEBUG] PTR0010: Legal risk predictions: 0 (expected: 0)
  [DEBUG] PTR0010: Economic concern predictions: 0 (expected: 0)
  Running audit engine on PTR0011...
    [DEBUG] PTR0011: versioning done, applicable_laws=60
    [DEBUG] PTR0011: calculation done, payment_calcs=3
    [DEBUG] PTR0011: findings done, findings=True
    [DEBUG] PTR0011: controller done, decisions=3, summary=True
  [DEBUG] PTR0011: Contract compliance predictions: 0 (expected: 0)
  [DEBUG] PTR0011: Legal risk predictions: 0 (expected: 0)
  [DEBUG] PTR0011: Economic concern predictions: 0 (expected: 0)
  Running audit engine on PTR0012...
    [DEBUG] PTR0012: versioning done, applicable_laws=160
    [DEBUG] PTR0012: calculation done, payment_calcs=8
    [DEBUG] PTR0012: findings done, findings=True
    [DEBUG] PTR0012: controller done, decisions=8, summary=True
  [DEBUG] PTR0012: Contract compliance predictions: 0 (expected: 0)
  [DEBUG] PTR0012: Legal risk predictions: 0 (expected: 0)
  [DEBUG] PTR0012: Economic concern predictions: 0 (expected: 0)
  Running audit engine on PTR0013...
    [DEBUG] PTR0013: versioning done, applicable_laws=160
    [DEBUG] PTR0013: calculation done, payment_calcs=8
    [DEBUG] PTR0013: findings done, findings=True
    [DEBUG] PTR0013: controller done, decisions=8, summary=True
  [DEBUG] PTR0013: Contract compliance predictions: 0 (expected: 0)
  [DEBUG] PTR0013: Legal risk predictions: 0 (expected: 0)
  [DEBUG] PTR0013: Economic concern predictions: 0 (expected: 0)
  Running audit engine on PTR0014...
    [DEBUG] PTR0014: versioning done, applicable_laws=180
    [DEBUG] PTR0014: calculation done, payment_calcs=9
    [DEBUG] PTR0014: findings done, findings=True
    [DEBUG] PTR0014: controller done, decisions=9, summary=True
  [DEBUG] PTR0014: Contract compliance predictions: 0 (expected: 0)
  [DEBUG] PTR0014: Legal risk predictions: 0 (expected: 0)
  [DEBUG] PTR0014: Economic concern predictions: 0 (expected: 0)
  Running audit engine on PTR0015...
    [DEBUG] PTR0015: versioning done, applicable_laws=200
    [DEBUG] PTR0015: calculation done, payment_calcs=10
    [DEBUG] PTR0015: findings done, findings=True
    [DEBUG] PTR0015: controller done, decisions=10, summary=True
  [DEBUG] PTR0015: Contract compliance predictions: 0 (expected: 1)
  [DEBUG] PTR0015: Legal risk predictions: 0 (expected: 1)
  [DEBUG] PTR0015: Economic concern predictions: 0 (expected: 1)
  Running audit engine on PTR0016...
    [DEBUG] PTR0016: versioning done, applicable_laws=60
    [DEBUG] PTR0016: calculation done, payment_calcs=3
    [DEBUG] PTR0016: findings done, findings=True
    [DEBUG] PTR0016: controller done, decisions=3, summary=True
  [DEBUG] PTR0016: Contract compliance predictions: 0 (expected: 0)
  [DEBUG] PTR0016: Legal risk predictions: 0 (expected: 0)
  [DEBUG] PTR0016: Economic concern predictions: 0 (expected: 1)
  Running audit engine on PTR0017...
    [DEBUG] PTR0017: versioning done, applicable_laws=80
    [DEBUG] PTR0017: calculation done, payment_calcs=4
    [DEBUG] PTR0017: findings done, findings=True
    [DEBUG] PTR0017: controller done, decisions=4, summary=True
  [DEBUG] PTR0017: Contract compliance predictions: 0 (expected: 0)
  [DEBUG] PTR0017: Legal risk predictions: 0 (expected: 0)
  [DEBUG] PTR0017: Economic concern predictions: 0 (expected: 0)
  Running audit engine on PTR0018...
    [DEBUG] PTR0018: versioning done, applicable_laws=120
    [DEBUG] PTR0018: calculation done, payment_calcs=6
    [DEBUG] PTR0018: findings done, findings=True
    [DEBUG] PTR0018: controller done, decisions=6, summary=True
  [DEBUG] PTR0018: Contract compliance predictions: 0 (expected: 0)
  [DEBUG] PTR0018: Legal risk predictions: 0 (expected: 0)
  [DEBUG] PTR0018: Economic concern predictions: 0 (expected: 0)
  Running audit engine on PTR0019...
    [DEBUG] PTR0019: versioning done, applicable_laws=60
    [DEBUG] PTR0019: calculation done, payment_calcs=3
    [DEBUG] PTR0019: findings done, findings=True
    [DEBUG] PTR0019: controller done, decisions=3, summary=True
  [DEBUG] PTR0019: Contract compliance predictions: 0 (expected: 0)
  [DEBUG] PTR0019: Legal risk predictions: 0 (expected: 0)
  [DEBUG] PTR0019: Economic concern predictions: 0 (expected: 0)
  Running audit engine on PTR0020...
    [DEBUG] PTR0020: versioning done, applicable_laws=80
    [DEBUG] PTR0020: calculation done, payment_calcs=4
    [DEBUG] PTR0020: findings done, findings=True
    [DEBUG] PTR0020: controller done, decisions=4, summary=True
  [DEBUG] PTR0020: Contract compliance predictions: 0 (expected: 0)
  [DEBUG] PTR0020: Legal risk predictions: 0 (expected: 0)
  [DEBUG] PTR0020: Economic concern predictions: 0 (expected: 0)

======================================================================
FAIRPAY SYNTHETIC VERIFICATION REPORT
======================================================================
Partners Processed:  20
Transactions Input:  106
Engine Success Rate: 20/20 (100.0%)
----------------------------------------------------------------------
LAYER 1: TRANSACTION RECONCILIATION (anomaly_type)
----------------------------------------------------------------------
  perfect_match                  | Exp:55 Det:62 TP:55 FP: 7 FN: 0 | P:89% R:100% F1:94%
  partial_payment                | Exp:10 Det:13 TP: 7 FP: 6 FN: 3 | P:54% R:70% F1:61%
  missing_payment                | Exp: 8 Det: 8 TP: 8 FP: 0 FN: 0 | P:100% R:100% F1:100%
  duplicate_settlement           | Exp: 6 Det: 6 TP: 6 FP: 0 FN: 0 | P:100% R:100% F1:100%
  missing_incentive              | Exp: 5 Det: 4 TP: 4 FP: 0 FN: 1 | P:100% R:80% F1:89%
  incorrect_deduction            | Exp: 6 Det: 5 TP: 1 FP: 4 FN: 5 | P:20% R:17% F1:18%
  date_out_of_tolerance          | Exp: 2 Det: 2 TP: 2 FP: 0 FN: 0 | P:100% R:100% F1:100%
  ambiguous_match                | Exp: 4 Det: 2 TP: 2 FP: 0 FN: 2 | P:100% R:50% F1:67%
  extra_unexplained_settlement   | Exp: 6 Det: 6 TP: 6 FP: 0 FN: 0 | P:100% R:100% F1:100%
----------------------------------------------------------------------
  OVERALL Reconciliation F1:  80.96%
  OVERALL Precision:          84.73%
  OVERALL Recall:             79.63%
----------------------------------------------------------------------
LAYER 2: CONTRACT/LEGAL/ECONOMIC FINDINGS
----------------------------------------------------------------------
  Overall:
    Expected Findings:  8
    Detected Findings:  0
    True Positives:     0
    False Positives:    0
    False Negatives:    8
    Precision:          100.00%
    Recall:             0.00%
    F1 Score:           0.00%
  By Category:
    Contract Compliance:  Exp:2 Det:0 TP:0 P:100% R:0% F1:0%
    Legal Risks:          Exp:3 Det:0 TP:0 P:100% R:0% F1:0%
    Economic Concerns:    Exp:3 Det:0 TP:0 P:100% R:0% F1:0%
----------------------------------------------------------------------
----------------------------------------------------------------------
Layer 2 Review Queue (reconciliation F1/engine health):  3 partners
  - PTR0006
  - PTR0010
  - PTR0014
----------------------------------------------------------------------
LAYER 3 HUMAN REVIEW QUEUE (controller decisions)
----------------------------------------------------------------------
  (empty)
----------------------------------------------------------------------
LAYER 3 AGGREGATE SUMMARY (controller decisions)
----------------------------------------------------------------------
  Total Records Processed:  114
  Automatically Resolved:   0
  Action Required:          0
  Human Review:             0
  Insufficient Evidence:    114
  Exception Rate:           100.00%
  Resolution Rate:          0.00%
  By Priority:              {'low': 114}
  By Recommended Action:    {'request_explanation': 114}
----------------------------------------------------------------------
LAYER 3: CONTROLLER DECISIONS (per-partner detail)
----------------------------------------------------------------------
  Partner: PTR0001 (Partner 01)
    Transaction: SETL00011
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['0']
    Transaction: SETL00088
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['1']
    Batch Summary:
      Records Processed:  2
      Auto Resolved:      0
      Action Required:    0
      Human Review:       0
      Insufficient Evid:  2
      Exception Rate:     100.00%
      Resolution Rate:    0.00%
      By Priority:        {'low': 2}
      By Action:          {'request_explanation': 2}
  Partner: PTR0002 (Partner 02)
    Transaction: SETL00016
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['0']
    Transaction: SETL00060
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['1']
    Transaction: SETL00103
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['2']
    Transaction: SETL00104
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['3']
    Batch Summary:
      Records Processed:  4
      Auto Resolved:      0
      Action Required:    0
      Human Review:       0
      Insufficient Evid:  4
      Exception Rate:     100.00%
      Resolution Rate:    0.00%
      By Priority:        {'low': 4}
      By Action:          {'request_explanation': 4}
  Partner: PTR0003 (Partner 03)
    Transaction: SETL00004
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['0']
    Transaction: SETL00012
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['1']
    Transaction: SETL00020
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['2']
    Transaction: SETL00028
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['3']
    Transaction: SETL00029
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['4']
    Transaction: SETL00036
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['5']
    Transaction: SETL00063
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['6']
    Batch Summary:
      Records Processed:  7
      Auto Resolved:      0
      Action Required:    0
      Human Review:       0
      Insufficient Evid:  7
      Exception Rate:     100.00%
      Resolution Rate:    0.00%
      By Priority:        {'low': 7}
      By Action:          {'request_explanation': 7}
  Partner: PTR0004 (Partner 04)
    Transaction: SETL00024
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['0']
    Transaction: SETL00047
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['1']
    Transaction: SETL00048
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['2']
    Transaction: SETL00071
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['3']
    Transaction: SETL00082
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['4']
    Transaction: SETL00085
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['5']
    Batch Summary:
      Records Processed:  6
      Auto Resolved:      0
      Action Required:    0
      Human Review:       0
      Insufficient Evid:  6
      Exception Rate:     100.00%
      Resolution Rate:    0.00%
      By Priority:        {'low': 6}
      By Action:          {'request_explanation': 6}
  Partner: PTR0005 (Partner 05)
    Transaction: SETL00044
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['0']
    Transaction: SETL00059
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['1']
    Transaction: SETL00076
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['2']
    Transaction: SETL00086
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['3']
    Transaction: SETL00095
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['4']
    Transaction: EARN00077
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['5']
    Batch Summary:
      Records Processed:  6
      Auto Resolved:      0
      Action Required:    0
      Human Review:       0
      Insufficient Evid:  6
      Exception Rate:     100.00%
      Resolution Rate:    0.00%
      By Priority:        {'low': 6}
      By Action:          {'request_explanation': 6}
  Partner: PTR0006 (Partner 06)
    Transaction: SETL00005
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['0']
    Transaction: SETL00025
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['1']
    Transaction: SETL00026
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['2']
    Transaction: SETL00052
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['3']
    Transaction: SETL00053
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['4']
    Transaction: SETL00058
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['5']
    Transaction: SETL00075
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['6']
    Batch Summary:
      Records Processed:  7
      Auto Resolved:      0
      Action Required:    0
      Human Review:       0
      Insufficient Evid:  7
      Exception Rate:     100.00%
      Resolution Rate:    0.00%
      By Priority:        {'low': 7}
      By Action:          {'request_explanation': 7}
  Partner: PTR0007 (Partner 07)
    Transaction: SETL00007
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['0']
    Transaction: SETL00079
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['1']
    Transaction: EARN00056
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['2']
    Batch Summary:
      Records Processed:  3
      Auto Resolved:      0
      Action Required:    0
      Human Review:       0
      Insufficient Evid:  3
      Exception Rate:     100.00%
      Resolution Rate:    0.00%
      By Priority:        {'low': 3}
      By Action:          {'request_explanation': 3}
  Partner: PTR0008 (Partner 08)
    Transaction: SETL00018
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['0']
    Transaction: SETL00043
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['1']
    Transaction: SETL00068
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['2']
    Transaction: SETL00069
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['3']
    Transaction: SETL00077
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['4']
    Transaction: SETL00093
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['5']
    Transaction: SETL00094
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['6']
    Transaction: SETL00102
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['7']
    Transaction: EARN00019
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['8']
    Batch Summary:
      Records Processed:  9
      Auto Resolved:      0
      Action Required:    0
      Human Review:       0
      Insufficient Evid:  9
      Exception Rate:     100.00%
      Resolution Rate:    0.00%
      By Priority:        {'low': 9}
      By Action:          {'request_explanation': 9}
  Partner: PTR0009 (Partner 09)
    Transaction: SETL00015
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['0']
    Transaction: SETL00021
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['1']
    Transaction: SETL00035
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['2']
    Transaction: SETL00051
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['3']
    Transaction: SETL00057
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['4']
    Transaction: SETL00070
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['5']
    Transaction: SETL00074
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['6']
    Transaction: SETL00101
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['7']
    Batch Summary:
      Records Processed:  8
      Auto Resolved:      0
      Action Required:    0
      Human Review:       0
      Insufficient Evid:  8
      Exception Rate:     100.00%
      Resolution Rate:    0.00%
      By Priority:        {'low': 8}
      By Action:          {'request_explanation': 8}
  Partner: PTR0010 (Partner 10)
    Transaction: SETL00006
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['0']
    Transaction: SETL00030
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['1']
    Transaction: SETL00031
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['2']
    Transaction: SETL00061
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['3']
    Batch Summary:
      Records Processed:  4
      Auto Resolved:      0
      Action Required:    0
      Human Review:       0
      Insufficient Evid:  4
      Exception Rate:     100.00%
      Resolution Rate:    0.00%
      By Priority:        {'low': 4}
      By Action:          {'request_explanation': 4}
  Partner: PTR0011 (Partner 11)
    Transaction: SETL00010
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['0']
    Transaction: SETL00078
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['1']
    Transaction: SETL00099
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['2']
    Batch Summary:
      Records Processed:  3
      Auto Resolved:      0
      Action Required:    0
      Human Review:       0
      Insufficient Evid:  3
      Exception Rate:     100.00%
      Resolution Rate:    0.00%
      By Priority:        {'low': 3}
      By Action:          {'request_explanation': 3}
  Partner: PTR0012 (Partner 12)
    Transaction: SETL00017
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['0']
    Transaction: SETL00056
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['1']
    Transaction: SETL00091
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['2']
    Transaction: SETL00092
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['3']
    Transaction: SETL00096
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['4']
    Transaction: SETL00097
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['5']
    Transaction: SETL00106
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['6']
    Transaction: EARN00102
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['7']
    Batch Summary:
      Records Processed:  8
      Auto Resolved:      0
      Action Required:    0
      Human Review:       0
      Insufficient Evid:  8
      Exception Rate:     100.00%
      Resolution Rate:    0.00%
      By Priority:        {'low': 8}
      By Action:          {'request_explanation': 8}
  Partner: PTR0013 (Partner 13)
    Transaction: SETL00002
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['0']
    Transaction: SETL00003
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['1']
    Transaction: SETL00009
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['2']
    Transaction: SETL00042
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['3']
    Transaction: SETL00046
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['4']
    Transaction: SETL00080
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['5']
    Transaction: SETL00081
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['6']
    Transaction: SETL00100
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['7']
    Batch Summary:
      Records Processed:  8
      Auto Resolved:      0
      Action Required:    0
      Human Review:       0
      Insufficient Evid:  8
      Exception Rate:     100.00%
      Resolution Rate:    0.00%
      By Priority:        {'low': 8}
      By Action:          {'request_explanation': 8}
  Partner: PTR0014 (Partner 14)
    Transaction: SETL00001
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['0']
    Transaction: SETL00022
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['1']
    Transaction: SETL00033
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['2']
    Transaction: SETL00049
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['3']
    Transaction: SETL00065
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['4']
    Transaction: SETL00089
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['5']
    Transaction: SETL00090
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['6']
    Transaction: SETL00105
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['7']
    Transaction: EARN00040
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['8']
    Batch Summary:
      Records Processed:  9
      Auto Resolved:      0
      Action Required:    0
      Human Review:       0
      Insufficient Evid:  9
      Exception Rate:     100.00%
      Resolution Rate:    0.00%
      By Priority:        {'low': 9}
      By Action:          {'request_explanation': 9}
  Partner: PTR0015 (Partner 15)
    Transaction: SETL00019
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['0']
    Transaction: SETL00027
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['1']
    Transaction: SETL00034
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['2']
    Transaction: SETL00037
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['3']
    Transaction: SETL00038
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['4']
    Transaction: SETL00041
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['5']
    Transaction: SETL00064
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['6']
    Transaction: SETL00072
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['7']
    Transaction: SETL00073
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['8']
    Transaction: EARN00032
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['9']
    Batch Summary:
      Records Processed:  10
      Auto Resolved:      0
      Action Required:    0
      Human Review:       0
      Insufficient Evid:  10
      Exception Rate:     100.00%
      Resolution Rate:    0.00%
      By Priority:        {'low': 10}
      By Action:          {'request_explanation': 10}
  Partner: PTR0016 (Partner 16)
    Transaction: SETL00055
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['0']
    Transaction: SETL00084
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['1']
    Transaction: EARN00014
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['2']
    Batch Summary:
      Records Processed:  3
      Auto Resolved:      0
      Action Required:    0
      Human Review:       0
      Insufficient Evid:  3
      Exception Rate:     100.00%
      Resolution Rate:    0.00%
      By Priority:        {'low': 3}
      By Action:          {'request_explanation': 3}
  Partner: PTR0017 (Partner 17)
    Transaction: SETL00008
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['0']
    Transaction: SETL00013
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['1']
    Transaction: SETL00062
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['2']
    Transaction: SETL00098
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['3']
    Batch Summary:
      Records Processed:  4
      Auto Resolved:      0
      Action Required:    0
      Human Review:       0
      Insufficient Evid:  4
      Exception Rate:     100.00%
      Resolution Rate:    0.00%
      By Priority:        {'low': 4}
      By Action:          {'request_explanation': 4}
  Partner: PTR0018 (Partner 18)
    Transaction: SETL00014
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['0']
    Transaction: SETL00023
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['1']
    Transaction: SETL00040
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['2']
    Transaction: SETL00050
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['3']
    Transaction: SETL00054
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['4']
    Transaction: SETL00067
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['5']
    Batch Summary:
      Records Processed:  6
      Auto Resolved:      0
      Action Required:    0
      Human Review:       0
      Insufficient Evid:  6
      Exception Rate:     100.00%
      Resolution Rate:    0.00%
      By Priority:        {'low': 6}
      By Action:          {'request_explanation': 6}
  Partner: PTR0019 (Partner 19)
    Transaction: SETL00032
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['0']
    Transaction: SETL00039
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['1']
    Transaction: SETL00066
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['2']
    Batch Summary:
      Records Processed:  3
      Auto Resolved:      0
      Action Required:    0
      Human Review:       0
      Insufficient Evid:  3
      Exception Rate:     100.00%
      Resolution Rate:    0.00%
      By Priority:        {'low': 3}
      By Action:          {'request_explanation': 3}
  Partner: PTR0020 (Partner 20)
    Transaction: SETL00045
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['0']
    Transaction: SETL00083
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['1']
    Transaction: SETL00087
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['2']
    Transaction: EARN00061
      Resolution:     ResolutionStatus.INSUFFICIENT_EVIDENCE
      Priority:       Priority.LOW
      Action:         RecommendedAction.REQUEST_EXPLANATION
      Human Review:   False
      Reason:         Insufficient evidence for definitive action. Request explanation.
      Evidence Refs:  ['3']
    Batch Summary:
      Records Processed:  4
      Auto Resolved:      0
      Action Required:    0
      Human Review:       0
      Insufficient Evid:  4
      Exception Rate:     100.00%
      Resolution Rate:    0.00%
      By Priority:        {'low': 4}
      By Action:          {'request_explanation': 4}
======================================================================
```

</details>

On the two zero rows: Layer 1 transaction reconciliation is the primary
measurement and scores 80.96% F1. Layer 2 detected none of the 8 expected
contract, legal and economic findings on this dataset. That is the harness
reporting a real gap in the engine, and it is recorded here as printed rather
than omitted.

---

## 3. Test suite with coverage

`pytest-cov` was added to `Backend/requirements.txt`. A `Backend/.coveragerc`
was added at the same time, excluding the test modules themselves and
`validate_incentive_audit.py` from the measured set: a test file always runs to
completion, so counting its lines raises the figure without saying anything
about how much of the engine is exercised.

### The tests that need the stack running

`Backend/test_suite.py` holds 3 tests that talk to the running backend over
HTTP and over two WebSocket endpoints, `/stream` and `/stream/ingest`. They
skip themselves with `the backend is not running on 127.0.0.1:8000` when the
port is closed, so they never register as failures. **They need the backend
process running, which in turn needs Postgres**: `api.py` verifies the database
at startup and refuses to start without it.

For the run below the stack was brought up first, so all 3 ran for real:

```
docker compose up -d db          # fairpay_db, Postgres on localhost:54322
cd Backend
python -m uvicorn api:server --host 127.0.0.1 --port 8000
```

The server logged `Database ready at localhost:54322/postgres` and
`Application startup complete`, and the port opened 17 seconds after launch.

`Backend/test_incentive_kb_live.py` (14 tests) is likewise written to skip when
the Qdrant cluster is unreachable. On this run it did not skip; all 14 ran.

### Measurement

Command:

```
cd Backend
python -m pytest --cov=. --cov-report=term -q -rs
```

Date: 2026-10-20. Machine: as above. Containers running: `fairpay_db`
(healthy). Backend API running on 127.0.0.1:8000. Exit code: 0.

| Figure | Value |
| --- | --- |
| Passed | 113 |
| Failed | 0 |
| Skipped | 0 |
| Wall clock | 106.52 s |
| Coverage, application code, per `Backend/.coveragerc` | **58%** |
| Statements measured | 7020 |
| Statements missed | 2935 |

Raw unedited output:

```
........................................................................ [ 63%]
.........................................                                [100%]
============================== warnings summary ===============================
test_audit_determinism.py::test_fifty_runs_of_one_evidence_set_give_one_verdict
  D:\FairPay-Engine\Backend\law_store.py:167: UserWarning: Payload indexes have no effect in the local Qdrant. Please use server Qdrant if you need payload indexes.
    client.create_payload_index(name, field_name=field_name, field_schema=schema)

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
=============================== tests coverage ================================
_______________ coverage: platform win32, python 3.14.7-final-0 _______________

Name                        Stmts   Miss  Cover
-----------------------------------------------
api.py                        344    206    40%
api_errors.py                  44     22    50%
audit_context.py               54     33    39%
calculations.py               146     16    89%
controller.py                 150     54    64%
db.py                          50     14    72%
debate.py                     207    147    29%
earnings_opportunity.py       353    300    15%
evidence_extractor.py         366    263    28%
evidence_modes.py             218    186    15%
findings.py                   290     82    72%
incentive_audit.py            381     55    86%
incentive_engine.py           638     63    90%
incentive_evidence.py         412    117    72%
incentive_extraction.py       169     72    57%
incentive_fit.py              255     22    91%
incentive_kb_ingest.py         77     37    52%
incentive_rules.py            312     29    91%
law_kb_ingest.py               30     30     0%
law_store.py                  186     14    92%
models.py                     230      1    99%
orchestrator.py                99     18    82%
platforms.py                   27      4    85%
reader_fallback.py            122     16    87%
real_data.py                  304    270    11%
reconciliation.py              34     18    47%
sanitizer.py                    9      3    67%
synthetic_verification.py     584    347    41%
telegram_audit.py             346    162    53%
telegram_bot.py               372    263    29%
telegram_store.py             144     63    56%
versioning.py                  67      8    88%
-----------------------------------------------
TOTAL                        7020   2935    58%
113 passed, 1 warning in 106.52s (0:01:46)
```

The 113 includes the 50-run determinism test from section 4, the 3 live
API and WebSocket tests, and the 14 live Qdrant retrieval tests.

### The same suite with the backend down, for comparison

Taken earlier the same day, before the API server was started and before
`.coveragerc` existed. That earlier figure therefore counts the test modules
too and is not comparable to the 58% above.

```
cd Backend
python -m pytest --cov=. --cov-report=term -q -rs
```

Result: 109 passed, 3 skipped, 0 failed in 107.31 s; TOTAL coverage 59% over
8540 statements with 3540 missed. The 3 skips were the `test_suite.py` live
tests, each reporting `the backend is not running on 127.0.0.1:8000`. That run
also predates `test_audit_determinism.py`, which is why its passed count is
109 rather than 112.

---

## 4. Determinism: 50 runs, one verdict

New test: `Backend/test_audit_determinism.py`. It loads the synthetic dataset,
runs partner `PTR0015`'s evidence through the audit pipeline 50 times, and
serialises the full verdict after each run — status, error, payment
calculations, business-cost calculations, audit findings, applicable laws,
evidence gaps, classification probability, controller decisions and batch
summary — with `json.dumps(..., sort_keys=True, default=str)`. It asserts that
the set of SHA-256 digests over those 50 serialisations has exactly one
element.

`PTR0015` was chosen because it carries seeded anomalies in all three Layer 2
categories and has the largest transaction count in the dataset, so it
exercises the widest slice of the pipeline reachable from a single partner.

### Measurement

Command:

```
cd Backend
python -m pytest test_audit_determinism.py -q -s
```

Date: 2026-10-20. Machine: as above. Exit code: 0.

| Figure | Value |
| --- | --- |
| Runs | 50 |
| Distinct verdicts | 1 |
| Verdict SHA-256 | `16259d46986e988bc11cc81c15b913ddd2703e83bbd402fd4c7903679e2c975a` |
| Serialised verdict size | 178382 bytes |
| Result | **passed** |
| Wall clock | 37.12 s |

Raw unedited output:

```

50 runs of PTR0015: 1 distinct verdict, sha256=16259d46986e988bc11cc81c15b913ddd2703e83bbd402fd4c7903679e2c975a, 178382 bytes
.
============================== warnings summary ===============================
test_audit_determinism.py::test_fifty_runs_of_one_evidence_set_give_one_verdict
  D:\FairPay-Engine\Backend\law_store.py:167: UserWarning: Payload indexes have no effect in the local Qdrant. Please use server Qdrant if you need payload indexes.
    client.create_payload_index(name, field_name=field_name, field_schema=schema)

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
1 passed, 1 warning in 37.12s
```

It passes. 50 runs of identical evidence produced a byte-identical verdict
every time.

---

## 5. Composition of `synthetic_data/ground_truth.json`

`synthetic_data/ground_truth.json` holds **102 records. Of those, 55 carry a
clean match (`anomaly_type: "perfect_match"`) and 47 carry a seeded anomaly.**

Command:

```
python -c "import json,collections; d=json.load(open('synthetic_data/ground_truth.json')); print(len(d)); print(collections.Counter(r['anomaly_type'] for r in d))"
```

Date: 2026-10-20. Machine: as above.

| `anomaly_type` | Records |
| --- | --- |
| `perfect_match` (clean) | 55 |
| `partial_payment` | 10 |
| `missing_payment` | 8 |
| `duplicate_settlement` | 6 |
| `incorrect_deduction` | 6 |
| `extra_unexplained_settlement` | 6 |
| `missing_incentive` | 5 |
| `ambiguous_match` | 4 |
| `date_out_of_tolerance` | 2 |
| **Total** | **102** |
| **Seeded anomaly, total minus clean** | **47** |

`synthetic_data/ground_truth_layer2.json` is a separate file holding the 8
expected contract, legal and economic findings counted in section 2. It is not
part of the 102.

---

## 6. Measurements that did not run

None. Every measurement described in this file was executed, and its output is
pasted above.
