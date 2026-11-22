""": jobs the worker will skip (a company, hCaptcha) must not occupy queue slots."""
import inspect
from apply import runner


def test_prepare_queue_drops_known_bad_hosts_unless_the_offscreen_run():
    src = inspect.getsource(runner.prepare_queue)
    assert 'jpmc.fa.oraclecloud.com" not in' in src and "JOBHUNT_OFFSCREEN" in src
