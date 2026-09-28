"""从avsox抓取数据"""
import re
import logging

import requests

from javsp.web.base import headers, read_proxy
from javsp.web.exceptions import *
from javsp.config import Cfg, CrawlerID
from javsp.datatype import MovieInfo


logger = logging.getLogger(__name__)
base_url = str(Cfg().network.proxy_free[CrawlerID.avsox]).rstrip('/')
# 抓取中文数据（女优名等在各语言下相同，genre则可以得到中文名称）
lang = 'cn'

# avsox已改版为前端渲染的网页，数据通过 /javu/data/api/<方法名> 接口获取：
# 请求为POST，参数以JSON数组的形式传递，并且需要携带网页中的CSRF Token
_session = requests.Session()
_session.headers.update(headers)
_csrf_token = None


def _refresh_csrf_token():
    global _csrf_token
    r = _session.get(base_url + '/', proxies=read_proxy(), timeout=Cfg().network.timeout.total_seconds())
    r.raise_for_status()
    match = re.search(r'<meta name="csrf-token" content="([^"]+)"', r.text)
    if not match:
        raise WebsiteError('avsox: 无法从网页中获取CSRF Token')
    _csrf_token = match.group(1)


def call_api(method: str, *args):
    """调用avsox的数据接口"""
    if _csrf_token is None:
        _refresh_csrf_token()
    api_headers = {
        'X-Requested-With': 'XMLHttpRequest',
        'X-CSRF-Token': _csrf_token,
        'Referer': base_url + '/',
    }
    r = _session.post(f'{base_url}/javu/data/api/{method}', json=list(args), headers=api_headers,
                      proxies=read_proxy(), timeout=Cfg().network.timeout.total_seconds())
    r.raise_for_status()
    resp = r.json()
    if resp.get('code') != 200:
        raise WebsiteError(f"avsox: 接口返回错误: {resp.get('code')}: {resp.get('message')}")
    return resp.get('data')


def parse_data(movie: MovieInfo):
    """解析指定番号的影片数据"""
    # avsox无法直接跳转到影片的网页，因此先搜索再从搜索结果中寻找目标影片
    full_id = movie.dvdid
    if full_id.startswith('FC2-'):
        full_id = full_id.replace('FC2-', 'FC2-PPV-')
    results = call_api('search', {'search': full_id, 'lang': lang}, 60, 1) or []
    ids = [i['movieFanHao'] for i in results]
    ids_lower = [i.lower() for i in ids]
    if full_id.lower() not in ids_lower:
        raise MovieNotFoundError(__name__, movie.dvdid, ids)
    movie_id = results[ids_lower.index(full_id.lower())]['movieId']

    # 提取影片信息
    data = call_api('getMovie', movie_id, lang)
    if not data:
        raise MovieNotFoundError(__name__, movie.dvdid)
    dvdid = data['movieFanHao']
    movie.dvdid = dvdid.replace('FC2-PPV-', 'FC2-')
    movie.url = f'{base_url}/{lang}/movies/{movie_id}'
    movie.title = data['title'].replace(dvdid, '').strip()
    # 简介通常只有日文版本
    plot = data.get(f'description_{lang}') or data.get('description_ja')
    if plot:
        movie.plot = plot.strip()
    movie.cover = data.get('posterLarge') or data.get('posterSmall')
    movie.publish_date = data.get('releaseDate')
    if data.get('length'):
        movie.duration = str(data['length'])
    movie.genre = [i['genreName'] for i in (data.get('genre') or [])]
    movie.actress = [i['starName'] for i in (data.get('star') or [])]
    serial = (data.get('series') or {}).get('seriesName')
    studio = data.get('studio') or {}
    producer = studio.get('studioName')
    # 保持与旧版网页相同的制作商名称格式，如'カリビアンコム( Caribbeancom )'，避免已整理的影片目录名称发生变化
    if producer and studio.get('studioName_en') and studio['studioName_en'] != producer:
        producer = f"{producer}( {studio['studioName_en']} )"
    if full_id.startswith('FC2-'):
        # avsox把FC2作品的拍摄者归类到'系列'而制作商固定为'FC2-PPV'，这既不合理也与其他的站点不兼容，因此进行调整
        movie.producer = serial
    else:
        movie.producer = producer
        movie.serial = serial


if __name__ == "__main__":
    import pretty_errors
    pretty_errors.configure(display_link=True)
    logger.root.handlers[1].level = logging.DEBUG

    movie = MovieInfo('082713-417')
    try:
        parse_data(movie)
        print(movie)
    except CrawlerError as e:
        logger.error(e, exc_info=1)
