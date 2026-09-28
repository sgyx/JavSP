"""从airav抓取数据"""
import re
import json
import logging


from javsp.web.base import Request, resp2html
from javsp.web.exceptions import *
from javsp.config import Cfg
from javsp.datatype import MovieInfo

# 初始化Request实例
request = Request(use_scraper=True)
request.headers['Accept-Language'] = 'zh-TW,zh;q=0.9'
# 近期airav服务器似乎不稳定，时好时坏，单次查询平均在17秒左右，timeout时间增加到20秒
request.timeout = 20


logger = logging.getLogger(__name__)
# airav已从www.airav.wiki迁移到airav.io，旧的JSON API也随之下线，现在只能解析网页
base_url = 'https://airav.io'
# airav上部分影片会被标记为'馬賽克破壞版'等，这些影片的title、plot和genre都不再准确
bad_keywords = ('馬賽克破壞版', '馬賽克破解版', '無碼流出版')


def get_html(url):
    """获取网页并检查是否被CloudFlare拦截"""
    r = request.get(url, delay_raise=True)
    if r.status_code == 403 and b'>Just a moment...<' in r.content:
        raise SiteBlocked(f"403 Forbidden: 无法通过CloudFlare检测: {url}")
    r.raise_for_status()
    return resp2html(r)


def search_movie(dvdid):
    """通过搜索番号获取指定的影片在网站上的ID(hid)"""
    # 影片的hid与番号无关，只能通过搜索来寻找影片。搜索是模糊匹配的（如搜索RED-096会得到PRED-096），需要自行筛选
    page = 1
    max_page = 1
    result = []
    while page <= max_page:
        html = get_html(f'{base_url}/search_result?kw={dvdid}&idx={page}')
        for item in html.xpath("//div[contains(@class,'oneVideo-top')]/a[contains(@href,'/video?hid=')]/../.."):
            hid = item.xpath("div[contains(@class,'oneVideo-top')]/a/@href")[0].split('hid=')[-1]
            # 搜索结果的标题形如'番號 标题'，部分影片没有番号
            name = item.xpath("string(div[contains(@class,'oneVideo-body')]/h5)").strip()
            barcode = name.split(maxsplit=1)[0] if name else ''
            result.append((hid, barcode, name))
        idx_max = html.xpath("//div[@class='page']//input[@name='idx']/@max")
        max_page = int(idx_max[0]) if idx_max else 0
        page += 1
    target = dvdid.upper().replace('-', '_')
    matched = [i for i in result if i[1].upper().replace('-', '_') == target]
    # 只在番号是纯数字时，才允许番号部分匹配（如'012717_472'对应的'1pondo_012717_472'），否则可能导致匹配到错误的影片
    if not matched and re.match(r'\d{6}[-_]\d{2,3}', dvdid):
        matched = [i for i in result if target in i[1].upper().replace('-', '_')]
    if not matched:
        raise MovieNotFoundError(__name__, dvdid, result)
    # 排序，优先选择没有'馬賽克破壞版'等标记的影片，其次选择更符合预期的番号（如'1pondo_012717_472'优先于'_1pondo_012717_472'）
    # 排序是稳定的，番号相同时（如同一影片的有无字幕版本）保持网站的搜索结果顺序
    matched.sort(key=lambda x: (any(k in x[2] for k in bad_keywords), x[1]))
    return matched[0][0]


def parse_data(movie: MovieInfo):
    """解析指定番号的影片数据"""
    # airav也提供简体（/cn/路径下），但是为了尽量保持女优名等与其他站点一致，抓取繁体的数据
    hid = search_movie(movie.dvdid)
    url = f'{base_url}/video?hid={hid}'
    html = get_html(url)
    container = html.xpath("//div[@class='video-info']")[0]
    info = {}
    for li in container.xpath(".//ul/li"):
        # 各项信息形如'番號：<span>IPX-177</span>'或'女優：<a>...</a><a>...</a>'
        key = (li.text or '').strip().rstrip('：')
        info[key] = li
    dvdid = info['番號'].xpath("string(span)").strip()
    movie.dvdid = dvdid
    movie.url = url
    movie.plot = container.xpath("string(p)").strip() or None
    cover = html.xpath("//meta[@property='og:image']/@content")
    if cover:
        movie.cover = cover[0]
    # airav的genre是以搜索关键词的形式组织的，没有特定的genre_id
    movie.genre = [i.strip() for i in info['標籤'].xpath("a/text()")] if '標籤' in info else []
    # 网页上的标题带有番号前缀，需要去掉
    title = html.xpath("string(//div[contains(@class,'video-title')]/h1)").strip()
    if title.startswith(dvdid):
        title = title[len(dvdid):].strip()
    movie.title = title or None
    movie.actress = [i.strip() for i in info['女優'].xpath("a/text()")] if '女優' in info else []
    # 日期形如'2018-07-19 00:00:00'
    date = re.search(r'\d{4}-\d{2}-\d{2}', html.xpath("string(//div[@class='video-item']/div[1])"))
    if date:
        movie.publish_date = date.group()
    if '廠商' in info:
        producer = info['廠商'].xpath("a/text()")
        if producer:
            movie.producer = producer[0].strip()
    # 网页中已不再提供预览图片

    if Cfg().crawler.hardworking:
        # 预览视频的地址在网页的JSON-LD数据中，是带有签名和时间戳的m3u8链接，部分影片的链接可能已失效
        ld_json = html.xpath("//script[@type='application/ld+json']/text()")
        if ld_json:
            movie.preview_video = json.loads(ld_json[0]).get('contentUrl') or None

    # airav上部分影片会被标记为'馬賽克破壞版'等，这些影片的title、plot和genre都不再准确
    for keyword in bad_keywords:
        if movie.title and keyword in movie.title:
            movie.title = None
            movie.genre = []
        if movie.plot and keyword in movie.plot:
            movie.plot = None
            movie.genre = []
        if not any([movie.title, movie.plot, movie.genre]):
            break


if __name__ == "__main__":
    import pretty_errors
    pretty_errors.configure(display_link=True)
    logger.root.handlers[1].level = logging.DEBUG

    movie = MovieInfo('DSAD-938')
    try:
        parse_data(movie)
        print(movie)
    except CrawlerError as e:
        logger.error(e, exc_info=1)
