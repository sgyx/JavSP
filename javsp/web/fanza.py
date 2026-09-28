"""从fanza抓取数据"""
import re
import logging
from datetime import datetime, timedelta, timezone
from html import unescape

import requests

from javsp.web.base import headers, read_proxy
from javsp.web.exceptions import *
from javsp.config import Cfg
from javsp.datatype import MovieInfo


logger = logging.getLogger(__name__)
# FANZA视频已改版为 video.dmm.co.jp，网页由前端渲染，数据全部来自这个GraphQL接口
api_url = 'https://api.video.dmm.co.jp/graphql'
base_url = 'https://video.dmm.co.jp'

_api_headers = headers.copy()
_api_headers.update({
    'Content-Type': 'application/json',
    'Accept-Language': 'ja,en-US;q=0.9',
    'Origin': base_url,
    'Referer': base_url + '/',
})

# 只请求需要用到的字段（字段名参照网站前端的ContentPageData查询）
_CONTENT_QUERY = """
query JavspContent($id: ID!) {
  ppvContent(id: $id) {
    id
    floor
    title
    description
    packageImage { largeUrl mediumUrl }
    sampleImages { number imageUrl largeImageUrl }
    sample2DMovie { highestMovieUrl }
    deliveryStartDate
    duration
    actresses { name imageUrl }
    directors { name }
    series { name }
    maker { name }
    label { name }
    genres { id name }
  }
  reviewSummary(contentId: $id) { average }
}
"""

_JST = timezone(timedelta(hours=9))


def query_content(cid: str):
    """查询指定cid的影片数据，影片不存在时返回None"""
    payload = {'operationName': 'JavspContent', 'query': _CONTENT_QUERY, 'variables': {'id': cid}}
    r = requests.post(api_url, json=payload, headers=_api_headers, proxies=read_proxy(),
                      timeout=Cfg().network.timeout.total_seconds())
    if r.status_code in (403, 451):
        raise SiteBlocked(f'FANZA: {r.status_code} 禁止访问，请检查你的网络和代理服务器设置')
    r.raise_for_status()
    resp = r.json()
    data = resp.get('data') or {}
    if resp.get('errors') and not data.get('ppvContent'):
        msgs = '; '.join(e.get('message', '') for e in resp['errors'])
        raise WebsiteError(f'FANZA: 接口返回错误: {msgs}')
    content = data.get('ppvContent')
    if content is None:
        return None
    content['reviewSummary'] = data.get('reviewSummary')
    return content


def parse_data(movie: MovieInfo):
    """解析指定番号的影片数据"""
    content = query_content(movie.cid)
    # 新版网站只包含视频配信的作品，DVD、同人等商品查询不到
    if content is None:
        raise MovieNotFoundError(__name__, movie.cid)
    parse_content(movie, content)


def parse_content(movie: MovieInfo, content: dict):
    """将接口返回的数据转换到MovieInfo"""
    cid = content['id']
    movie.cid = cid
    movie.url = f"{base_url}/{content['floor'].lower()}/content/?id={cid}"
    movie.title = content['title']
    if content.get('description'):
        movie.plot = _html_to_text(content['description'])
    package = content.get('packageImage') or {}
    movie.cover = package.get('largeUrl') or package.get('mediumUrl')
    # 优先使用大图作为剧照
    movie.preview_pics = [i.get('largeImageUrl') or i['imageUrl'] for i in (content.get('sampleImages') or [])]
    video = content.get('sample2DMovie') or {}
    if video.get('highestMovieUrl'):
        movie.preview_video = video['highestMovieUrl']
    if content.get('deliveryStartDate'):
        # 接口返回的是UTC时间，而发布日期应按日本时间计算（采用'配信開始日'作为发布日期）
        start = datetime.fromisoformat(content['deliveryStartDate'].replace('Z', '+00:00'))
        movie.publish_date = start.astimezone(_JST).strftime('%Y-%m-%d')
    if content.get('duration'):
        # 接口返回的是秒数，换算为分钟（与原网页显示的分钟数保持一致: 恰好半分钟时舍去）
        movie.duration = str((content['duration'] + 29) // 60)
    actresses = content.get('actresses') or []
    movie.actress = [i['name'] for i in actresses]
    actress_pics = {i['name']: i['imageUrl'] for i in actresses if i.get('imageUrl')}
    if actress_pics:
        movie.actress_pics = actress_pics
    if content.get('directors'):
        movie.director = content['directors'][0]['name']
    if content.get('series'):
        movie.serial = content['series']['name']
    if content.get('maker'):
        movie.producer = content['maker']['name']
    genres = content.get('genres') or []
    movie.genre = [i['name'] for i in genres]
    movie.genre_id = [i['id'] for i in genres]
    review = content.get('reviewSummary') or {}
    if review.get('average'):
        # 评分为5分制，转换为10分制
        movie.score = f"{float(review['average']) * 2:.2f}"
    movie.uncensored = False    # 服务器在日本且面向日本国内公开发售，不会包含无码片


def _html_to_text(text: str) -> str:
    """简介中可能含有HTML换行和转义字符，转换为纯文本"""
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.I)
    text = re.sub(r'<[^>]+>', '', text)
    return unescape(text).strip()


if __name__ == "__main__":
    import pretty_errors
    pretty_errors.configure(display_link=True)
    logger.root.handlers[1].level = logging.DEBUG

    movie = MovieInfo(cid='hjmo00214')
    try:
        parse_data(movie)
        print(movie)
    except CrawlerError as e:
        logger.error(e, exc_info=1)
