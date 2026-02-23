import signal
import time

import scheduler as scheduler_module
from app import create_app


def main():
    app = create_app()
    with app.app_context():
        scheduler = scheduler_module.start_scheduler(app)

    def _shutdown(*_args):
        if scheduler:
            scheduler.shutdown(wait=False)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
