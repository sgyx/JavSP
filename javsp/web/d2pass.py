"""从D2Pass旗下的官网（一本道、天然むすめ、パコパコママ、ムラムラ）抓取数据"""
import re
import logging
from urllib.parse import urljoin

from javsp.web.base import request_get
from javsp.web.exceptions import *
from javsp.datatype import MovieInfo


logger = logging.getLogger(__name__)

# 各站点的域名和制作商名称（接口数据中不包含片商名称，因此按站点固定填写）
SITES = {
    '1pondo': ('https://www.1pondo.tv', '一本道'),
    '10musume': ('https://www.10musume.com', '天然むすめ'),
    'pacopacomama': ('https://www.pacopacomama.com', 'パコパコママ'),
    'muramura': ('https://www.muramura.tv', 'ムラムラ'),
}
# 未指定片商时，按番号后半部分的位数决定要查询的站点（按顺序查询，先查到的为准）
SITES_BY_SUFFIX_LEN = {
    2: ['10musume'],
    3: ['1pondo', 'pacopacomama'],
    4: ['muramura'],
}
# 旧版画廊（仅有HasGallery标记的影片）的公开样图地址，取自各站点前端代码的movieGallery配置。
# 一本道按序号(从1开始)命名，其他站点直接使用接口返回的Filename；ムラムラ的前端没有旧版画廊
LEGACY_GALLERY_URL = {
    '1pondo': '/assets/sample/{id}/popu/{index}.jpg',
    '10musume': '/assets/sample/{id}/{filename}',
    'pacopacomama': '/assets/sample/{id}/l/{filename}',
}
# UC编号不小于此值的标签是会员等级(VIP/超VIP)和画质(1080p/60fps)，不属于影片分类
_NON_GENRE_UC = 50000

_ID_PATTERN = re.compile(r'^\d{6}_(\d{2,4})$')


def get_json(url):
    """获取接口的JSON数据，资源不存在时返回None"""
    r = request_get(url, delay_raise=True)
    if r.status_code == 404:
        return None
    if r.status_code == 403:
        raise SiteBlocked(f'D2Pass: 403 禁止访问: {url}')
    if r.status_code != 200:
        raise WebsiteError(f'D2Pass: 非预期的状态码: {r.status_code}: {url}')
    try:
        return r.json()
    except ValueError:
        raise WebsiteError(f'D2Pass: 接口返回的不是有效的JSON数据: {url}')


def get_candidate_sites(movie: MovieInfo):
    """根据番号格式和文件名中的片商提示确定要查询的站点"""
    match = _ID_PATTERN.match(movie.dvdid or '')
    if not match:
        return []
    hint = getattr(movie, '_studio_hint', None)
    if hint in SITES:
        # 已知片商时只查询该站点（即使番号位数与该站点的惯例不符）
        return [hint]
    if hint is not None:
        # 其他片商（如加勒比）的影片，同一番号在D2Pass站点上可能是不同的影片，因此不能查询
        return []
    return SITES_BY_SUFFIX_LEN.get(len(match.group(1)), [])


def parse_data(movie: MovieInfo):
    """解析指定番号的影片数据"""
    # 此抓取器会被用于所有普通番号，因此不符合格式的番号必须直接跳过，不能发起网络请求
    sites = get_candidate_sites(movie)
    for site in sites:
        base_url = SITES[site][0]
        data = get_json(f'{base_url}/dyn/phpauto/movie_details/movie_id/{movie.dvdid}.json')
        if data:
            parse_detail(movie, site, data)
            return
    raise MovieNotFoundError(__name__, movie.dvdid)


def parse_detail(movie: MovieInfo, site: str, data: dict):
    """将接口返回的影片详情转换到MovieInfo"""
    base_url, producer = SITES[site]
    title = (data.get('Title') or '').strip()
    if not title:
        raise WebsiteError(f"D2Pass: {site}的影片数据中缺少标题: '{movie.dvdid}'")
    movie_id = data.get('MovieID') or movie.dvdid
    movie.url = f'{base_url}/movies/{movie_id}/'
    movie.title = title
    if data.get('Desc'):
        movie.plot = data['Desc'].replace('\r\n', '\n').strip()
    # 各尺寸的缩略图通常指向同一张图片，按清晰度从高到低选取
    for key in ('ThumbUltra', 'ThumbHigh', 'ThumbMed', 'ThumbLow', 'MovieThumb'):
        if data.get(key):
            movie.cover = _abs_url(base_url, data[key])
            break
    movie.preview_pics = get_preview_pics(site, data) or None
    movie.preview_video = _best_sample(data.get('SampleFiles'))
    actresses = data.get('ActressesJa')
    if not actresses and data.get('Actor'):
        actresses = data['Actor'].split(',')
    movie.actress = [i.strip() for i in (actresses or []) if i and i.strip()]
    movie.genre = _get_genres(data)
    if data.get('Series'):
        movie.serial = data['Series'].strip()
    movie.producer = producer
    if data.get('Release'):
        movie.publish_date = data['Release']
    if data.get('Duration'):
        # 接口返回的是秒数，换算为分钟
        movie.duration = str((int(data['Duration']) + 29) // 60)
    if data.get('AvgRating'):
        # 评分为5分制，转换为10分制
        movie.score = f"{float(data['AvgRating']) * 2:.2f}"
    movie.uncensored = True


def get_preview_pics(site: str, data: dict):
    """获取画廊中公开的样图（Protected的图片仅对会员开放，不予采用）"""
    base_url = SITES[site][0]
    movie_id = data.get('MovieID')
    if not movie_id:
        return []
    # 与网站前端的逻辑一致: Gallery为新版画廊，仅有HasGallery时为旧版画廊
    if data.get('Gallery'):
        gallery = get_json(f'{base_url}/dyn/dla/json/movie_gallery/{movie_id}.json') or {}
        rows = gallery.get('Rows') or []
        return [f"{base_url}/dyn/dla/images/{i['Img']}" for i in rows
                if i.get('Img') and not i.get('Protected')]
    elif data.get('HasGallery') and site in LEGACY_GALLERY_URL:
        gallery = get_json(f'{base_url}/dyn/phpauto/movie_galleries/movie_id/{movie_id}.json') or {}
        rows = gallery.get('Rows') or []
        pattern = LEGACY_GALLERY_URL[site]
        return [base_url + pattern.format(id=movie_id, index=idx, filename=i['Filename'])
                for idx, i in enumerate(rows, start=1) if i.get('Filename') and not i.get('Protected')]
    return []


def _best_sample(samples):
    """选取文件最大（即清晰度最高）的预览视频"""
    samples = [i for i in (samples or []) if i.get('URL')]
    if not samples:
        return None
    return max(samples, key=lambda i: i.get('FileSize') or 0)['URL']


def _get_genres(data: dict):
    """提取日文的分类标签，并排除会员等级、画质等非分类标签"""
    names = data.get('UCNAME') or []
    ids = data.get('UC') or []
    if len(ids) == len(names):
        return [name for uc, name in zip(ids, names) if int(uc) < _NON_GENRE_UC]
    # 两个列表无法对应时，退而使用UcNameList
    uc_list = data.get('UcNameList') or {}
    if uc_list:
        return [i['NameJa'] for k, i in uc_list.items() if int(k) < _NON_GENRE_UC and i.get('NameJa')]
    return list(names)


def _abs_url(base_url: str, url: str):
    """转换为绝对地址，并统一使用https（部分旧影片的图片地址为http）"""
    url = urljoin(base_url + '/', url)
    if url.startswith('http://'):
        url = 'https://' + url[len('http://'):]
    return url


if __name__ == "__main__":
    import pretty_errors
    pretty_errors.configure(display_link=True)
    logger.root.handlers[1].level = logging.DEBUG

    movie = MovieInfo('012717_472')
    try:
        parse_data(movie)
        print(movie)
    except CrawlerError as e:
        logger.error(e, exc_info=1)
