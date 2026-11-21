import sys
import time

from devflow.db import Database
from devflow.lease import EngineLease
from devflow.state import StateStore


def main() -> None:
    db_path, ttl, owner, hold = sys.argv[1], float(sys.argv[2]), sys.argv[3], float(sys.argv[4])
    db = Database(db_path)
    db.migrate()
    lease = EngineLease(StateStore(db), ttl_seconds=ttl, owner_id=owner)
    lease.acquire()
    print("READY", flush=True)
    try:
        time.sleep(hold)
    finally:
        lease.release()


if __name__ == "__main__":
    main()
