from am_common.migrations import run_env
from marketdata.db import metadata

run_env(metadata)
