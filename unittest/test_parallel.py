"""并行整理相关的离线测试：站点请求数限制、各模块的线程安全、并行整理流程"""
import os
import sys
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import javsp.web.base as base


class _SlowHandler(BaseHTTPRequestHandler):
    """每个请求耗时0.2秒，并记录同时处理的请求数的最大值"""
    lock = threading.Lock()
    active = 0
    max_active = 0

    def do_GET(self):
        cls = type(self)
        with cls.lock:
            cls.active += 1
            cls.max_active = max(cls.max_active, cls.active)
        time.sleep(0.2)
        with cls.lock:
            cls.active -= 1
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'ok')

    def log_message(self, *args):
        pass


class _LocalServer(ThreadingHTTPServer):
    def server_bind(self):
        # 跳过HTTPServer.server_bind中的反向DNS查询（getfqdn），它在部分系统上非常耗时
        self.socket.bind(self.server_address)
        self.server_address = self.socket.getsockname()
        self.server_name, self.server_port = self.server_address[:2]


@pytest.fixture
def server():
    _SlowHandler.active = _SlowHandler.max_active = 0
    httpd = _LocalServer(('127.0.0.1', 0), _SlowHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{httpd.server_port}/'
    httpd.shutdown()


@pytest.fixture
def host_limit(monkeypatch):
    """将对127.0.0.1的请求数限制设置为指定值"""
    def set_limit(n):
        monkeypatch.setitem(base._host_semaphores, '127.0.0.1', threading.BoundedSemaphore(n))
    return set_limit


def _get(url):
    # 本地测试服务器不经过代理
    return requests.get(url, proxies={'http': None, 'https': None}, timeout=10)


def test_requests_per_host_are_limited(server, host_limit):
    host_limit(2)
    threads = [threading.Thread(target=_get, args=(server,)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert _SlowHandler.max_active == 2


def test_nested_request_on_same_host_does_not_deadlock(server, host_limit):
    host_limit(1)
    result = []
    def nested():
        # 模拟cloudscraper在一个请求中再次向同一站点发起请求
        with base.host_slot(server):
            result.append(_get(server).status_code)
    t = threading.Thread(target=nested, daemon=True)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive(), '同一线程的嵌套请求发生了死锁'
    assert result == [200]


def test_translate_throttle_is_thread_safe():
    import javsp.web.translate as tr
    tr._last_access.clear()
    times = []
    lock = threading.Lock()
    def call():
        tr._throttle('test', 0.2)
        with lock:
            times.append(time.perf_counter())
    threads = [threading.Thread(target=call) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    times.sort()
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert all(g >= 0.19 for g in gaps), gaps


def test_javdb_switches_cookies_once_for_concurrent_logins(monkeypatch):
    import javsp.web.javdb as javdb

    class FakeRequest:
        """初始的请求会被重定向到登录页，更换Cookies后的请求可以正常访问"""
        def __init__(self, logged_in=False):
            self.logged_in = logged_in
            self.headers = {}
            self.cookies = {}
        def get(self, url, delay_raise=False):
            time.sleep(0.05)    # 让多个线程几乎同时遇到登录页
            if self.logged_in:
                return SimpleNamespace(status_code=200, history=[], url=url)
            return SimpleNamespace(status_code=200, history=[object()], url='https://javdb.com/login')

    pool = [{'cookies': {'c': str(i)}, 'profile': 'p', 'site': 's'} for i in range(3)]
    monkeypatch.setattr(javdb, 'request', FakeRequest())
    monkeypatch.setattr(javdb, 'cookies_pool', pool, raising=False)
    monkeypatch.setattr(javdb, 'Request', lambda: FakeRequest(logged_in=True))
    monkeypatch.setattr(javdb, 'resp2html', lambda r: 'html')

    results, errors = [], []
    def call():
        try:
            results.append(javdb.get_html_wrapper('https://javdb.com/v/x'))
        except Exception as e:
            errors.append(e)
    threads = [threading.Thread(target=call) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not errors
    assert results == ['html'] * 5
    # 5个线程同时遇到登录页，但只应消耗一份Cookies
    assert len(pool) == 2


def test_run_parallel_continues_after_failures(monkeypatch):
    import javsp.__main__ as main
    from javsp.datatype import Movie

    def fake_process(movie, inner_bar, show_download_progress=True):
        assert show_download_progress is False
        time.sleep(0.1)
        if movie.dvdid.endswith('3'):
            raise Exception('模拟的整理失败')

    monkeypatch.setattr(main, 'process_movie', fake_process)
    movies = []
    for i in range(6):
        m = Movie(f'TEST-00{i}')
        m.files = [f'/tmp/TEST-00{i}.mp4']
        movies.append(m)
    start = time.perf_counter()
    done = main.run_parallel(movies, 3)
    elapsed = time.perf_counter() - start
    assert sorted(m.dvdid for m in done) == ['TEST-000', 'TEST-001', 'TEST-002', 'TEST-004', 'TEST-005']
    # 6部影片、3个线程，应明显快于逐部整理（sleep_after_scraping也会在各线程中执行）
    sleep_after = main.Cfg().crawler.sleep_after_scraping.total_seconds()
    assert elapsed < 6 * (0.1 + sleep_after)
