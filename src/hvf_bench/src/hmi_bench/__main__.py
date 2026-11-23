import sys
import time

sys._hmi_bench_entry_perf = time.perf_counter()
from .cli import main

raise SystemExit(main())
