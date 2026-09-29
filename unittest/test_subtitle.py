"""字幕下载相关的离线测试"""
import os
import sys
import json
from types import SimpleNamespace

import lxml.html
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import javsp.subtitle as subtitle
from javsp.config import SubtitleSummarize
from javsp.datatype import Movie
from javsp.lib import list_sidecar_subtitles
from javsp.subtitle import subtitlecat


data_dir = os.path.join(os.path.dirname(__file__), 'data', 'subtitlecat')


def load_html(name):
    with open(os.path.join(data_dir, name), encoding='utf-8') as f:
        return lxml.html.fromstring(f.read())


def make_movie(path, dvdid='SSIS-001'):
    movie = Movie(dvdid)
    movie.files = [str(path)]
    return movie


def touch(path, content=b''):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(content)
    return str(path)


@pytest.fixture
def no_ffprobe(monkeypatch):
    monkeypatch.setattr(subtitle.shutil, 'which', lambda name: None)


@pytest.fixture
def sub_cfg(monkeypatch):
    """替换字幕相关的配置（配置对象不可修改，需要修改时替换返回值的subtitle属性）"""
    summarizer = SimpleNamespace(subtitle=SubtitleSummarize(enabled=True))
    fake = SimpleNamespace(summarizer=summarizer)
    monkeypatch.setattr(subtitle, 'Cfg', lambda: fake)
    return summarizer


def test_parse_search_page():
    urls = subtitlecat.parse_search_page(load_html('search_SSIS-001.html'), 'SSIS-001')
    assert len(urls) == 3
    # 下载量最多的条目排在最前
    assert urls[0] == 'https://www.subtitlecat.com/subs/252/SSIS-001.html'
    assert all(u.startswith('https://www.subtitlecat.com/subs/') for u in urls)


def test_parse_search_page_filter_unrelated():
    # 站点的搜索是模糊匹配，无关的结果应被过滤掉
    assert subtitlecat.parse_search_page(load_html('search_ZZZZ-999.html'), 'ZZZZ-999') == []
    assert subtitlecat.parse_search_page(load_html('search_SSIS-001.html'), 'SSIS-002') == []


def test_parse_download_links():
    links = subtitlecat.parse_download_links(load_html('detail_SSIS-001.html'), ['zh-CN', 'zh-TW', 'da'])
    assert links == {
        'zh-CN': 'https://www.subtitlecat.com/subs/253/SSIS-001-zh-CN.srt',
        'zh-TW': 'https://www.subtitlecat.com/subs/252/SSIS-001-zh-TW.srt',
    }


class FakeRequest:
    def __init__(self, files):
        self.files = files
        self.requested = []

    def get_html(self, url):
        self.requested.append(url)
        if 'search=' in url:
            return load_html('search_SSIS-001.html')
        return load_html('detail_SSIS-001.html')

    def get(self, url):
        self.requested.append(url)
        return SimpleNamespace(content=self.files.get(url, b'<html>404</html>'))


def test_get_subtitle_language_priority(monkeypatch):
    srt = b'1\n00:00:01,000 --> 00:00:02,000\n\xe4\xbd\xa0\xe5\xa5\xbd\n'
    fake = FakeRequest({
        'https://www.subtitlecat.com/subs/253/SSIS-001-zh-CN.srt': srt,
        'https://www.subtitlecat.com/subs/252/SSIS-001-zh-TW.srt': srt,
    })
    monkeypatch.setattr(subtitlecat, 'request', fake)
    assert subtitlecat.get_subtitle('SSIS-001', ['zh-CN', 'zh-TW']) == ('zh-CN', srt)
    # 首个条目就有最优先的语言时，不再请求其他条目的详情页
    assert len([u for u in fake.requested if u.endswith('.html')]) == 1
    assert subtitlecat.get_subtitle('SSIS-001', ['zh-TW', 'zh-CN']) == ('zh-TW', srt)


def test_get_subtitle_skip_invalid_content(monkeypatch):
    fake = FakeRequest({
        'https://www.subtitlecat.com/subs/252/SSIS-001-zh-TW.srt': b'1\n00:00:01,000 --> 00:00:02,000\nok\n',
    })
    monkeypatch.setattr(subtitlecat, 'request', fake)
    # zh-CN返回的是错误页面，应退而选择zh-TW
    lang, _ = subtitlecat.get_subtitle('SSIS-001', ['zh-CN', 'zh-TW'])
    assert lang == 'zh-TW'
    # 没有需要的语言
    assert subtitlecat.get_subtitle('SSIS-001', ['ja']) is None


@pytest.mark.parametrize('relpath, detected', [
    ('SSIS-001.mp4', False),
    ('SSIS-001-C.mp4', True),
    ('SSIS-001C.mp4', True),
    ('SSIS-001ch.mp4', True),
    ('ssis001-chs.mkv', True),
    ('SSIS-001-CD1.mp4', False),
    ('[中文字幕]SSIS-001.mp4', True),
    ('SSIS-001 简中.mp4', True),
    ('中文字幕/SSIS-001.mp4', True),
    ('无中文字幕/SSIS-001.mp4', False),
    ('字幕待下载/SSIS-001.mp4', False),
])
def test_detect_hard_sub_by_name(tmp_path, no_ffprobe, relpath, detected):
    path = tmp_path / relpath
    movie = make_movie(path)
    assert bool(subtitle.detect_hard_sub(movie, str(path))) == detected


@pytest.mark.parametrize('streams, detected', [
    ([], False),
    ([{'tags': {'language': 'chi'}}], True),
    ([{'tags': {'language': 'zh-Hans'}}], True),
    ([{'tags': {'language': 'zh-CN'}}], True),
    ([{'tags': {'language': 'eng', 'title': 'English'}}], False),
    ([{'tags': {'language': 'und', 'title': '简体中文'}}], True),
    ([{'tags': {'language': 'und'}}], True),
    ([{}], True),
    ([{'tags': {'language': 'jpn'}}], False),
])
def test_detect_hard_sub_by_stream(tmp_path, monkeypatch, streams, detected):
    path = tmp_path / 'SSIS-001.mkv'
    monkeypatch.setattr(subtitle.shutil, 'which', lambda name: '/usr/bin/ffprobe')
    stdout = json.dumps({'streams': streams})
    monkeypatch.setattr(subtitle.subprocess, 'run', lambda *a, **kw: SimpleNamespace(stdout=stdout))
    assert bool(subtitle.detect_hard_sub(make_movie(path), str(path))) == detected


def test_list_sidecar_subtitles(tmp_path):
    for name in ('ABC-123.mp4', 'ABC-123.srt', 'ABC-123.zh-CN.ASS', 'ABC-123-C.srt', 'ABC-1234.srt', 'ABC-123.nfo'):
        touch(tmp_path / name)
    found = sorted(suffix for _, suffix in list_sidecar_subtitles(str(tmp_path), 'ABC-123'))
    assert found == ['.srt', '.zh-CN.ASS']
    assert list_sidecar_subtitles(str(tmp_path / 'missing'), 'ABC-123') == []


def test_rename_files_moves_subtitles(tmp_path):
    src = tmp_path / 'src'
    video = touch(src / 'SSIS-001.mp4')
    touch(src / 'SSIS-001.srt')
    touch(src / 'SSIS-001.zh-CN.ass')
    touch(src / 'SSIS-001-C.srt')
    movie = make_movie(video)
    movie.save_dir = str(tmp_path / 'out')
    movie.basename = 'SSIS-001 标题'
    os.makedirs(movie.save_dir)
    movie.rename_files()
    assert sorted(os.listdir(movie.save_dir)) == ['SSIS-001 标题.mp4', 'SSIS-001 标题.srt', 'SSIS-001 标题.zh-CN.ass']
    # 不属于这部影片的字幕保留在原处
    assert os.listdir(src) == ['SSIS-001-C.srt']


def test_download_subtitle(tmp_path, monkeypatch, no_ffprobe, sub_cfg):
    video = touch(tmp_path / 'SSIS-001 标题.mp4')
    movie = make_movie(tmp_path / 'SSIS-001.mp4')
    movie.new_paths = [video]
    calls = []
    def fake_get_subtitle(avid, languages):
        calls.append((avid, languages))
        return 'zh-TW', b'srt content'
    monkeypatch.setattr(subtitlecat, 'get_subtitle', fake_get_subtitle)

    assert subtitle.download_subtitle(movie)
    assert calls == [('SSIS-001', ['zh-CN', 'zh-TW'])]
    with open(tmp_path / 'SSIS-001 标题.zh-TW.srt', 'rb') as f:
        assert f.read() == b'srt content'

    # 已有字幕时不再下载
    assert not subtitle.download_subtitle(movie)
    assert len(calls) == 1
    # 关闭检查时覆盖下载，并按配置的文件名后缀命名
    sub_cfg.subtitle = SubtitleSummarize(enabled=True, skip_if_exists=False, filename_suffix='')
    assert subtitle.download_subtitle(movie)
    assert os.path.exists(tmp_path / 'SSIS-001 标题.srt')


def test_download_subtitle_skipped(tmp_path, monkeypatch, no_ffprobe, sub_cfg):
    def fail(*args):
        raise AssertionError('不应搜索字幕')
    monkeypatch.setattr(subtitlecat, 'get_subtitle', fail)
    # 带有内嵌字幕
    assert not subtitle.download_subtitle(make_movie(touch(tmp_path / 'SSIS-001-C.mp4')))
    # 多分片影片
    movie = make_movie(tmp_path / 'SSIS-001-CD1.mp4')
    movie.files.append(str(tmp_path / 'SSIS-001-CD2.mp4'))
    assert not subtitle.download_subtitle(movie)
    # 没有番号
    assert not subtitle.download_subtitle(Movie(cid='ssis00001'))


def test_download_subtitle_provider_error(tmp_path, monkeypatch, no_ffprobe, sub_cfg):
    def raise_error(*args):
        raise ConnectionError('network down')
    monkeypatch.setattr(subtitlecat, 'get_subtitle', raise_error)
    assert not subtitle.download_subtitle(make_movie(touch(tmp_path / 'SSIS-001.mp4')))
