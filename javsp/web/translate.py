"""网页翻译接口"""
# 翻译服务需要自己的错误处理机制，因此不通过base.py来管理网络请求（但同样使用配置的代理服务器）
import re
import ssl
import json
import time
import threading
from typing import Dict, List, Union
import uuid
import random
import logging
from pydantic_core import Url
import certifi
import requests
from requests.adapters import HTTPAdapter
import anthropic
from hashlib import md5


__all__ = ['translate', 'translate_movie_info']


from javsp.config import BaiduTranslateEngine, BingTranslateEngine, Cfg, ClaudeTranslateEngine, GoogleTranslateEngine, OpenAITranslateEngine, TranslateEngine
from javsp.datatype import MovieInfo
from javsp.web.base import headers, read_proxy


logger = logging.getLogger(__name__)

# 大模型生成译文的耗时明显长于普通翻译接口，因此单独设置超时时间
LLM_TIMEOUT = 60
# 大模型翻译使用的提示词。女优名等需要原样保留的内容在请求时附加到用户消息中
LLM_SYSTEM_PROMPT = (
    "You translate metadata of Japanese adult videos for a personal media library. "
    "The user message contains a JSON object. Translate every value from Japanese into {to}. "
    "Keep person names, product codes (such as ABC-123) and any text that is not Japanese unchanged. "
    "Reply with a JSON object that has exactly the same keys, whose values are the translations only."
)


def translate_movie_info(info: MovieInfo):
    """根据配置翻译影片信息。翻译失败时保留原文并记录错误，不影响影片的整理"""
    fields = {}
    if info.title and Cfg().translator.fields.title and info.ori_title is None:
        fields['title'] = info.title
    if info.plot and Cfg().translator.fields.plot:
        fields['plot'] = info.plot
    if not fields:
        return True

    engine = Cfg().translator.engine
    actress = info.actress or []
    if engine.name in ('claude', 'openai'):
        # 大模型可以一次翻译多个字段，并且同时翻译标题和简介有助于它理解上下文
        result = llm_translate(fields, engine, actress)
        if 'error' in result:
            results = {k: result for k in fields}
        else:
            results = {k: {'trans': v} for k, v in result.items()}
    else:
        results = {k: translate(v, engine, actress) for k, v in fields.items()}

    names = {'title': '标题', 'plot': '简介'}
    for key, result in results.items():
        if 'trans' not in result:
            logger.error(f"翻译{names[key]}时出错，将保留原文: {result['error']}")
            continue
        if key == 'title':
            info.ori_title = info.title
            info.title = result['trans']
            # 如果有的话，附加断句信息
            if 'orig_break' in result:
                setattr(info, 'ori_title_break', result['orig_break'])
            if 'trans_break' in result:
                setattr(info, 'title_break', result['trans_break'])
        else:
            # 只有翻译过plot的影片才可能需要ori_plot属性，因此在运行时动态添加，而不添加到类型定义里
            setattr(info, 'ori_plot', info.plot)
            info.plot = result['trans']
    return True


def translate(texts, engine: Union[
        BaiduTranslateEngine,
        BingTranslateEngine,
        ClaudeTranslateEngine,
        OpenAITranslateEngine,
        GoogleTranslateEngine,
        None
    ], actress=[]):
    """
    翻译入口：对错误进行处理并且统一返回格式

    Returns:
        dict: 翻译正常: {'trans': '译文', 'orig_break':['原句1', ...], 'trans_break': ['译句1', ...]}
              仅在能判断分句时有breaks字段，子句末尾可能有换行符\n
              翻译出错: {'error': 'baidu: 54000: PARAM_FROM_TO_OR_Q_EMPTY'}
    """
    if engine is None:
        return {'trans': texts}
    rtn = {}
    err_msg = ''
    # 注意: 错误信息中只能使用engine.name，直接格式化engine会把api_key等凭据写进日志
    try:
        if engine.name == 'baidu':
            result = baidu_translate(texts, engine.app_id, engine.api_key)
            if 'error_code' not in result:
                # 百度翻译的结果中的组表示的是按换行符分隔的不同段落，而不是句子
                paragraphs = [i['dst'] for i in result['trans_result']]
                rtn = {'trans': '\n'.join(paragraphs)}
            else:
                err_msg = "{}: {}: {}".format(engine.name, result['error_code'], result.get('error_msg'))
        elif engine.name == 'bing':
            # 使用动态词典保护原文中的女优名，防止翻译后认不出来
            marked = texts
            for i in actress:
                marked = marked.replace(i, f'<mstrans:dictionary translation="{i}">{i}</mstrans:dictionary>')
            result = bing_translate(marked, api_key=engine.api_key)
            if 'error' not in result:
                sentLen = result[0]['translations'][0]['sentLen']
                orig_break, trans_break = [], []
                # 对原文进行断句。Bing返回的句子长度是基于带有动态词典标记的文本计算的，
                # 因此先按标记后的文本断句，再移除每个句子中的标记（断句结果会被用于生成文件名）
                remaining = marked
                for i in sentLen['srcSentLen']:
                    orig_break.append(_remove_bing_markup(remaining[:i]))
                    remaining = remaining[i:]
                # 对译文进行断句
                remaining = result[0]['translations'][0]['text']
                for i in sentLen['transSentLen']:
                    # Bing会在译文的每个句尾添加一个空格，这并不符合中文的标点习惯，所以去掉这个空格
                    trans_break.append(remaining[:i].rstrip(' '))
                    remaining = remaining[i:]
                trans = ''.join(trans_break)
                rtn = {'trans': trans, 'orig_break': orig_break, 'trans_break': trans_break}
            else:
                err_msg = "{}: {}: {}".format(engine.name, result['error']['code'], result['error']['message'])
        elif engine.name in ('claude', 'openai'):
            result = llm_translate({'text': texts}, engine, actress)
            if 'error' not in result:
                rtn = {'trans': result['text']}
            else:
                err_msg = result['error']
        elif engine.name == 'google':
            result = google_trans(texts)
            # 经测试，翻译成功时会带有'sentences'字段；失败时不带，也没有故障码
            if 'sentences' in result:
                # Google会对句子分组，完整的译文需要自行拼接
                orig_break = [i['orig'] for i in result['sentences']]
                trans_break = [i['trans'] for i in result['sentences']]
                trans = ''.join(trans_break)
                rtn = {'trans': trans, 'orig_break': orig_break, 'trans_break': trans_break}
            else:
                err_msg = "{}: {}: {}".format(engine.name, result.get('error_code'), result.get('error_msg'))
        else:
            return {'trans': texts}
    except Exception as e:
        err_msg = "{}: Exception: {!r}".format(engine.name, e)

    if rtn == {}:
        rtn['error'] = err_msg

    return rtn


def _remove_bing_markup(text: str) -> str:
    """移除Bing动态词典的标记，还原为原文"""
    return re.sub(r'<mstrans:dictionary translation="[^"]*">(.*?)</mstrans:dictionary>', r'\1', text)


def _request_timeout():
    return Cfg().network.timeout.total_seconds()


def _throttle(key: str, interval: float):
    """确保对同一服务的两次请求之间至少间隔interval秒（并行整理时各线程会依次等待）"""
    with _throttle_lock:
        now = time.perf_counter()
        last_access = _last_access.get(key)
        if last_access is not None:
            wait = interval - (now - last_access)
            if wait > 0:
                time.sleep(wait)
        _last_access[key] = time.perf_counter()

_last_access: Dict[str, float] = {}
_throttle_lock = threading.Lock()


def baidu_translate(texts, app_id, api_key, to='zh'):
    """使用百度翻译文本（默认翻译为简体中文）"""
    api_url = "https://api.fanyi.baidu.com/api/trans/vip/translate"
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    salt = random.randint(0, 0x7FFFFFFF)
    sign_input = app_id + texts + str(salt) + api_key
    sign = md5(sign_input.encode('utf-8')).hexdigest()
    payload = {'appid': app_id, 'q': texts, 'from': 'auto', 'to': to, 'salt': salt, 'sign': sign}
    # 由于百度标准版限制QPS为1，连续翻译标题和简介会超限，因此需要添加延时
    _throttle('baidu', 1.0)
    r = requests.post(api_url, data=payload, headers=headers, proxies=read_proxy(), timeout=_request_timeout())
    result = r.json()
    return result


def bing_translate(texts, api_key, to='zh-Hans'):
    """使用Bing翻译文本（默认翻译为简体中文）"""
    api_url = "https://api.cognitive.microsofttranslator.com/translate"
    params = {'api-version': '3.0', 'to': to, 'includeSentenceLength': True}
    headers = {
        'Ocp-Apim-Subscription-Key': api_key,
        'Ocp-Apim-Subscription-Region': 'global',
        'Content-type': 'application/json',
        'X-ClientTraceId': str(uuid.uuid4())
    }
    body = [{'text': texts}]
    r = requests.post(api_url, params=params, headers=headers, json=body, proxies=read_proxy(), timeout=_request_timeout())
    result = r.json()
    return result


class _StdlibSSLAdapter(HTTPAdapter):
    """使用Python标准库默认的SSL上下文建立连接

    Google翻译会对urllib3默认SSL配置的TLS指纹直接返回429，而标准库默认的SSL上下文可以正常访问
    """
    def init_poolmanager(self, *args, **kwargs):
        kwargs['ssl_context'] = ssl.create_default_context(cafile=certifi.where())
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, proxy, **proxy_kwargs):
        proxy_kwargs['ssl_context'] = ssl.create_default_context(cafile=certifi.where())
        return super().proxy_manager_for(proxy, **proxy_kwargs)

_google_session = requests.Session()
_google_session.mount('https://', _StdlibSSLAdapter())
_google_session.headers.update(headers)

# Google翻译的API有QPS限制，两次请求之间需要间隔一段时间
_GOOGLE_INTERVAL = 4
# 请求超限(HTTP 429)时的最大重试次数，以及每次重试前等待的基础时间
_GOOGLE_MAX_RETRY = 3
_GOOGLE_RETRY_WAIT = 60
def google_trans(texts, to='zh_CN'):
    """使用Google翻译文本（默认翻译为简体中文）"""
    # API: https://www.jianshu.com/p/ce35d89c25c3
    # client参数的选择: https://github.com/lmk123/crx-selection-translate/issues/223#issue-184432017
    url = "https://translate.google.com.hk/translate_a/single"
    # 通过params传递参数，由requests负责URL编码（文本中可能含有&、#、+等字符）
    params = {'client': 'gtx', 'dt': 't', 'dj': '1', 'ie': 'UTF-8', 'sl': 'auto', 'tl': to, 'q': texts}
    for retry in range(_GOOGLE_MAX_RETRY + 1):
        _throttle('google', _GOOGLE_INTERVAL)
        r = _google_session.get(url, params=params, proxies=read_proxy(), timeout=_request_timeout())
        if r.status_code != 429 or retry == _GOOGLE_MAX_RETRY:
            break
        wait = _GOOGLE_RETRY_WAIT * (retry + 1) + random.randint(0, 30)
        logger.warning(f"HTTP {r.status_code}: {r.reason}: Google翻译请求超限，将等待{wait}秒后重试 ({retry+1}/{_GOOGLE_MAX_RETRY})")
        time.sleep(wait)
    if r.status_code == 200:
        result = r.json()
    else:
        result = {'error_code': r.status_code, 'error_msg': r.reason}
    return result


def llm_translate(fields: Dict[str, str], engine: Union[ClaudeTranslateEngine, OpenAITranslateEngine],
                  actress: List[str] = [], to='Simplified Chinese'):
    """使用大模型一次性翻译多个字段

    Args:
        fields: 要翻译的字段，如 {'title': '...', 'plot': '...'}

    Returns:
        dict: 翻译正常: 与fields键名相同的译文字典；翻译出错: {'error': '错误信息'}
    """
    system = LLM_SYSTEM_PROMPT.format(to=to)
    content = json.dumps(fields, ensure_ascii=False)
    if actress:
        content += '\n\nPerformer names that must be kept exactly as written: ' + ', '.join(actress)
    try:
        if engine.name == 'claude':
            data = claude_translate(fields, content, system, engine.api_key, engine.model)
        else:
            data = openai_translate(content, system, engine.url, engine.api_key, engine.model)
    except LLMTranslateError as e:
        return {'error': f'{engine.name}: {e}'}
    except anthropic.APIStatusError as e:
        error = e.body.get('error') if isinstance(e.body, dict) else None
        msg = error.get('message') if isinstance(error, dict) else e.message
        return {'error': f'{engine.name}: {e.status_code}: {msg}'}
    except (anthropic.APIConnectionError, requests.exceptions.RequestException) as e:
        return {'error': f'{engine.name}: 网络错误: {e!r}'}
    except Exception as e:
        return {'error': f'{engine.name}: Exception: {e!r}'}
    # 检查返回的字段是否齐全
    missing = [k for k in fields if not isinstance(data.get(k), str) or not data[k].strip()]
    if missing:
        return {'error': f"{engine.name}: 译文中缺少字段: {', '.join(missing)}"}
    return {k: data[k].strip() for k in fields}


class LLMTranslateError(Exception):
    """大模型没有返回可用的译文"""


_claude_client = None
_claude_client_lock = threading.Lock()
def _get_claude_client(api_key: str) -> anthropic.Anthropic:
    """创建并复用Claude客户端（SDK会自动对429、5xx等错误进行重试）"""
    global _claude_client
    with _claude_client_lock:
        if _claude_client is not None:
            return _claude_client
        kwargs = {}
        # 未配置代理时，SDK会像requests一样读取HTTP(S)_PROXY环境变量
        if Cfg().network.proxy_server is not None:
            kwargs['http_client'] = anthropic.DefaultHttpxClient(proxy=str(Cfg().network.proxy_server))
        _claude_client = anthropic.Anthropic(api_key=api_key, timeout=LLM_TIMEOUT, max_retries=2, **kwargs)
        return _claude_client


def claude_translate(fields: Dict[str, str], content: str, system: str, api_key: str, model: str) -> dict:
    """使用Claude翻译，通过结构化输出保证返回的是字段齐全的JSON"""
    client = _get_claude_client(api_key)
    schema = {
        'type': 'object',
        'properties': {k: {'type': 'string'} for k in fields},
        'required': list(fields),
        'additionalProperties': False,
    }
    response = client.messages.create(
        model=model,
        max_tokens=8192,
        system=system,
        messages=[{'role': 'user', 'content': content}],
        output_config={'format': {'type': 'json_schema', 'schema': schema}},
    )
    if response.stop_reason == 'refusal':
        raise LLMTranslateError('模型拒绝翻译此内容')
    if response.stop_reason == 'max_tokens':
        raise LLMTranslateError('译文超出长度限制')
    text = next((b.text for b in response.content if b.type == 'text'), None)
    if text is None:
        raise LLMTranslateError('响应中没有文本内容')
    return json.loads(text)


def openai_translate(content: str, system: str, url: Url, api_key: str, model: str) -> dict:
    """使用 OpenAI 兼容的接口翻译"""
    api_url = str(url)
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    data = {
         "messages": [
           {"role": "system", "content": system},
           {"role": "user", "content": content},
         ],
         "model": model,
         "temperature": 0,
         "max_tokens": 4096,
    }
    r = requests.post(api_url, headers=headers, json=data, proxies=read_proxy(), timeout=LLM_TIMEOUT)
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code != 200 or 'error' in body:
        error = body.get('error')
        msg = error.get('message', '') if isinstance(error, dict) else (error or r.reason)
        raise LLMTranslateError(f'{r.status_code}: {msg}')
    try:
        text = body['choices'][0]['message']['content']
    except (KeyError, IndexError, TypeError):
        raise LLMTranslateError('响应格式无法识别')
    return _parse_json_reply(text)


def _parse_json_reply(text: str) -> dict:
    """从模型的回复中提取JSON对象（兼容模型用```json代码块包裹回复的情况）"""
    start, end = text.find('{'), text.rfind('}')
    if start == -1 or end < start:
        raise LLMTranslateError('回复中没有JSON')
    try:
        data = json.loads(text[start:end+1])
    except json.JSONDecodeError:
        raise LLMTranslateError('回复中的JSON无法解析')
    if not isinstance(data, dict):
        raise LLMTranslateError('回复中的JSON不是对象')
    return data
