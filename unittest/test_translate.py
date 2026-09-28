"""翻译模块的离线测试：通过替换网络请求来检查请求格式和结果解析"""
import os
import sys
import json
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import javsp.web.translate as tr
from javsp.config import Cfg, BingTranslateEngine, ClaudeTranslateEngine, GoogleTranslateEngine, OpenAITranslateEngine
from javsp.datatype import MovieInfo


class FakeResponse:
    def __init__(self, status_code=200, data=None, reason='OK'):
        self.status_code = status_code
        self._data = data
        self.reason = reason

    def json(self):
        if self._data is None:
            raise ValueError('no json')
        return self._data


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    sleeps = []
    monkeypatch.setattr(tr.time, 'sleep', lambda s: sleeps.append(s))
    tr._last_access.clear()
    return sleeps


def use_engine(monkeypatch, engine, title=True, plot=True):
    """让translate_movie_info使用指定的翻译引擎"""
    fake_cfg = SimpleNamespace(
        translator=SimpleNamespace(engine=engine, fields=SimpleNamespace(title=title, plot=plot)),
        network=Cfg().network,
    )
    monkeypatch.setattr(tr, 'Cfg', lambda: fake_cfg)


def make_info():
    info = MovieInfo('ABC-123')
    info.title = 'タイトル & テスト #1 + 100%'
    info.plot = 'あらすじです。'
    info.actress = ['相沢みなみ']
    return info


def test_google_encodes_text_and_parses_sentences(monkeypatch):
    calls = []
    def fake_get(url, params=None, **kw):
        calls.append((url, params, kw))
        return FakeResponse(data={'sentences': [{'orig': '文1。', 'trans': '句1。'}, {'orig': '文2', 'trans': '句2'}]})
    monkeypatch.setattr(tr._google_session, 'get', fake_get)
    text = 'タイトル & テスト #1 + 100%'
    result = tr.translate(text, GoogleTranslateEngine(name='google'))
    assert result == {'trans': '句1。句2', 'orig_break': ['文1。', '文2'], 'trans_break': ['句1。', '句2']}
    url, params, kw = calls[0]
    # 文本必须通过params传递（由requests编码），不能直接拼接在URL中
    assert '?' not in url and params['q'] == text
    assert kw['timeout'] > 0


def test_google_429_retries_are_bounded(monkeypatch, no_sleep):
    count = []
    def fake_get(url, **kw):
        count.append(1)
        return FakeResponse(status_code=429, reason='Too Many Requests')
    monkeypatch.setattr(tr._google_session, 'get', fake_get)
    result = tr.translate('テスト', GoogleTranslateEngine(name='google'))
    assert len(count) == tr._GOOGLE_MAX_RETRY + 1
    assert result['error'].startswith('google: 429')


def test_bing_breaks_do_not_contain_dictionary_markup(monkeypatch):
    sent = {}
    def fake_post(url, params=None, headers=None, json=None, **kw):
        text = json[0]['text']
        sent['text'] = text
        first = text.index('。') + 1
        return FakeResponse(data=[{'translations': [{
            'text': '相沢みなみ的第一句。 第二句 ',
            'sentLen': {'srcSentLen': [first, len(text) - first], 'transSentLen': [11, 4]},
        }]}])
    monkeypatch.setattr(tr.requests, 'post', fake_post)
    engine = BingTranslateEngine(name='bing', api_key='secret-key')
    result = tr.translate('相沢みなみの一文目。二文目', engine, actress=['相沢みなみ'])
    assert '<mstrans:dictionary' in sent['text']
    assert result['orig_break'] == ['相沢みなみの一文目。', '二文目']
    assert result['trans_break'] == ['相沢みなみ的第一句。', '第二句']


def test_error_message_does_not_leak_api_key(monkeypatch):
    monkeypatch.setattr(tr.requests, 'post', lambda *a, **kw: FakeResponse(data={'error': {'code': 401, 'message': 'denied'}}))
    result = tr.translate('テスト', BingTranslateEngine(name='bing', api_key='secret-key'))
    assert 'secret-key' not in result['error']
    assert result['error'] == 'bing: 401: denied'


class FakeClaudeClient:
    def __init__(self, text=None, stop_reason='end_turn'):
        self.requests = []
        self.text = text
        self.stop_reason = stop_reason
        self.messages = self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        content = [SimpleNamespace(type='text', text=self.text)] if self.text is not None else []
        return SimpleNamespace(stop_reason=self.stop_reason, content=content)


def test_claude_translates_title_and_plot_in_one_request(monkeypatch):
    client = FakeClaudeClient(text=json.dumps({'title': '标题', 'plot': '简介'}, ensure_ascii=False))
    monkeypatch.setattr(tr, '_get_claude_client', lambda api_key: client)
    use_engine(monkeypatch, ClaudeTranslateEngine(name='claude', api_key='sk-test'))
    info = make_info()
    assert tr.translate_movie_info(info) is True
    assert (info.title, info.ori_title) == ('标题', 'タイトル & テスト #1 + 100%')
    assert (info.plot, info.ori_plot) == ('简介', 'あらすじです。')
    assert len(client.requests) == 1
    req = client.requests[0]
    assert req['model'] == 'claude-haiku-4-5'
    assert req['output_config']['format']['schema']['required'] == ['title', 'plot']
    assert '相沢みなみ' in req['messages'][0]['content']


def test_claude_refusal_keeps_original_text(monkeypatch):
    client = FakeClaudeClient(text=None, stop_reason='refusal')
    monkeypatch.setattr(tr, '_get_claude_client', lambda api_key: client)
    use_engine(monkeypatch, ClaudeTranslateEngine(name='claude', api_key='sk-test'))
    info = make_info()
    assert tr.translate_movie_info(info) is True
    assert info.title == 'タイトル & テスト #1 + 100%' and info.ori_title is None
    assert info.plot == 'あらすじです。' and not hasattr(info, 'ori_plot')


def test_openai_parses_fenced_json(monkeypatch):
    reply = '```json\n{"title": "标题", "plot": "简介"}\n```'
    sent = {}
    def fake_post(url, headers=None, json=None, **kw):
        sent.update(json)
        return FakeResponse(data={'choices': [{'message': {'content': reply}}]})
    monkeypatch.setattr(tr.requests, 'post', fake_post)
    engine = OpenAITranslateEngine(name='openai', url='https://api.example.com/v1/chat/completions', api_key='k', model='m')
    use_engine(monkeypatch, engine)
    info = make_info()
    assert tr.translate_movie_info(info) is True
    assert info.title == '标题' and info.plot == '简介'
    assert sent['model'] == 'm'


def test_openai_missing_field_keeps_original(monkeypatch):
    monkeypatch.setattr(tr.requests, 'post', lambda *a, **kw: FakeResponse(data={'choices': [{'message': {'content': '{"title": "标题"}'}}]}))
    engine = OpenAITranslateEngine(name='openai', url='https://api.example.com/v1/chat/completions', api_key='k', model='m')
    use_engine(monkeypatch, engine)
    info = make_info()
    assert tr.translate_movie_info(info) is True
    assert info.title == 'タイトル & テスト #1 + 100%'


def test_translation_failure_does_not_fail_movie(monkeypatch):
    def fake_get(*a, **kw):
        raise tr.requests.exceptions.ConnectionError('offline')
    monkeypatch.setattr(tr._google_session, 'get', fake_get)
    use_engine(monkeypatch, GoogleTranslateEngine(name='google'))
    info = make_info()
    assert tr.translate_movie_info(info) is True
    assert info.title == 'タイトル & テスト #1 + 100%'
