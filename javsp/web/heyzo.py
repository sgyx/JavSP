"""从HEYZO官网抓取数据"""
import re
import json
import logging
from urllib.parse import urljoin


from javsp.web.base import *
from javsp.web.exceptions import *
from javsp.datatype import MovieInfo


logger = logging.getLogger(__name__)
base_url = 'https://www.heyzo.com'
# 番号形如'HEYZO-1234'，但大小写和分隔符会保留文件名中的写法（如'Heyzo-1009', 'heyzo_1380'）
avid_pattern = re.compile(r'^HEYZO[-_]?(\d{1,4})$', re.I)


def parse_data(movie: MovieInfo):
    """从网页抓取并解析指定番号的数据
    Args:
        movie (MovieInfo): 要解析的影片信息，解析后的信息直接更新到此变量内
    """
    # 此抓取器位于normal列表中，所有常规番号都会调用到这里，因此不是HEYZO的番号必须在联网之前就直接排除
    match = avid_pattern.match(movie.dvdid or '')
    if not match:
        raise MovieNotFoundError(__name__, movie.dvdid)
    num = match.group(1).zfill(4)
    url = f'{base_url}/moviepages/{num}/index.html'
    resp = request_get(url, delay_raise=True)
    # 不存在的影片会直接返回404；以防万一，被重定向到其他页面时也视为未找到
    if resp.status_code == 404 or '/moviepages/' not in resp.url:
        raise MovieNotFoundError(__name__, movie.dvdid)
    elif resp.status_code != 200:
        raise WebsiteError(f'HEYZO: 非预期的状态码: {resp.status_code}: {url}')
    html = resp2html(resp)
    movie.url = url
    parse_movie_page(movie, html, resp.text, num)


def _load_ld_json(html) -> dict:
    """提取网页中@type为Movie的ld+json数据，不存在或格式错误时返回空字典（后续从HTML中提取）"""
    for text in html.xpath("//script[@type='application/ld+json']/text()"):
        try:
            # 简介中可能含有未转义的换行等控制字符，因此使用strict=False
            data = json.loads(text, strict=False)
        except ValueError:
            logger.debug('HEYZO: 无法解析ld+json数据', exc_info=True)
            continue
        if isinstance(data, dict) and data.get('@type') == 'Movie':
            return data
    return {}


def _str_value(d, *keys):
    """按顺序获取嵌套字典中的字符串值，路径中任意一级缺失或值为空时返回None"""
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d.strip() if (isinstance(d, str) and d.strip()) else None


def _abs_url(url, page_url):
    """将'//www.heyzo.com/...'等形式的地址转换为完整的URL"""
    return urljoin(page_url, url) if url else None


def _row_text(html, row_class):
    """获取信息表格中指定行的第二个单元格的全部文本（已去除首尾空白）"""
    cell = xpath_first(html, f"//table[@class='movieInfo']//tr[@class='{row_class}']/td[2]")
    if cell is None:
        return None
    text = cell.text_content().strip()
    return text or None


def _iso_duration_to_minutes(value):
    """将ISO 8601格式的时长（如'PT1H1M33S'）转换为分钟数（四舍五入），无法解析或时长为0时返回None"""
    match = re.fullmatch(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', value or '', re.I)
    if not match:
        return None
    h, m, s = [int(i) if i else 0 for i in match.groups()]
    seconds = h * 3600 + m * 60 + s
    return (seconds + 30) // 60 if seconds else None


def _hms_to_minutes(value):
    """将'01:02:03'形式的时长转换为分钟数（四舍五入），无法解析或时长为0时返回None"""
    match = re.fullmatch(r'(\d+):(\d{1,2}):(\d{1,2})', (value or '').strip())
    if not match:
        return None
    h, m, s = [int(i) for i in match.groups()]
    seconds = h * 3600 + m * 60 + s
    return (seconds + 30) // 60 if seconds else None


def _to_score(value, best):
    """将评分转换为10分制的字符串，评分无效（包括0分即尚无评价）时返回None"""
    try:
        score = float(value) * 10 / float(best)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return f'{score:.2f}' if score > 0 else None


def parse_movie_page(movie: MovieInfo, html, text, num):
    """解析影片页面。只有标题是必需的，其他字段在网页中缺失时直接跳过"""
    ld = _load_ld_json(html)
    page_url = movie.url
    # 标题: ld+json中的标题不含女优名，h1中的标题则形如'标题\n - 女优名'，此时仅取第一行
    title = _str_value(ld, 'name')
    if not title:
        h1 = xpath_first(html, "//div[@id='movie']/h1")
        lines = [i.strip() for i in h1.text_content().splitlines() if i.strip()] if h1 is not None else []
        title = lines[0] if lines else None
    if not title:
        raise WebsiteError(f'HEYZO: 网页结构可能已变化，找不到标题: {page_url}')
    # 部分标题可能带有番号前缀，移除它
    title = re.sub(r'^\s*HEYZO[-_ ]?\d{4}\s*', '', title, flags=re.I).strip() or title
    # 简介: 网页中的p.memo和ld+json中的description内容相同，优先使用前者（能够保留原本的换行）
    memo = xpath_first(html, "//p[@class='memo']")
    plot = '\n'.join(i.strip() for i in memo.itertext() if i.strip()) if memo is not None else None
    plot = plot or _str_value(ld, 'description')
    # 封面: player_thumbnail.jpg (800x450) 是公开的最大尺寸的封面，另有player_thumbnail_450.jpg等更小的版本
    cover = _str_value(ld, 'image') or xpath_first(html, "//meta[@property='og:image']/@content")
    if not cover:
        cover = f'/contents/3000/{num}/images/player_thumbnail.jpg'
    # 发布日期
    publish_date = _row_text(html, 'table-release-day')
    if not (publish_date and re.fullmatch(r'\d{4}-\d{2}-\d{2}', publish_date)):
        publish_date = (_str_value(ld, 'dateCreated') or _str_value(ld, 'releasedEvent', 'startDate')
                        or _str_value(ld, 'video', 'uploadDate'))
    # 时长: 早期影片ld+json中的时长为'PT0H0M0S'，此时改用网页脚本中的'heyzo.duration'
    duration = _iso_duration_to_minutes(_str_value(ld, 'duration'))
    if not duration:
        match = re.search(r'o\s*=\s*\{\s*"full"\s*:\s*"([\d:]+)"', text)
        duration = _hms_to_minutes(match.group(1)) if match else None
    # 女优: 优先使用信息表格中的女优列表，找不到时再使用ld+json中的数据（可能是单个对象或者列表）
    actress = [i.strip() for i in html.xpath("//tr[@class='table-actor']/td[2]//a/span/text()") if i.strip()]
    if not actress:
        actors = ld.get('actor')
        actors = actors if isinstance(actors, list) else [actors]
        actress = [i for i in (_str_value(a, 'name') for a in actors) if i]
    # 系列: 没有系列时显示为'-----'
    serial = _row_text(html, 'table-series')
    if serial and set(serial) == {'-'}:
        serial = None
    # 评分: ld+json中的ratingValue目前总是为空，因此还要从网页中提取（均为5分制）
    rating = ld.get('aggregateRating') if isinstance(ld.get('aggregateRating'), dict) else {}
    score = _to_score(rating.get('ratingValue'), rating.get('bestRating') or 5)
    if not score:
        score = _to_score(xpath_first(html, "//tr[@class='table-estimate']//span[@itemprop='ratingValue']/text()"), 5)
    # 分类: 'タグキーワード'在网页中有大小两个版本（内容相同），只取其中一个；'女優タイプ'（如熟女、美脚）
    # 是站点的分类，同样用来描述影片内容，因此一并加入分类中
    tags = html.xpath("//tr[@class='table-tag-keyword-big']//ul[@class='tag-keyword-list']/li/a/text()")
    if not tags:
        tags = html.xpath("//ul[@class='tag-keyword-list']/li/a/text()")
    actor_types = html.xpath("//tr[@class='table-actor-type']/td[2]//a/text()")
    genre = []
    for i in actor_types + tags:
        i = i.strip()
        if i and i not in genre:
            genre.append(i)
    # 预览图片: 非会员也能查看前几张图片的大图，这些图片的链接位于脚本中（'/member/'开头的链接需要登录，不能使用）
    preview_pics = []
    for pic in re.findall(rf'href="(/contents/3000/{num}/gallery/\d+\.jpg)"', text):
        pic = _abs_url(pic, page_url)
        if pic not in preview_pics:
            preview_pics.append(pic)
    # 预览视频: 优先使用嵌入播放器所用的高清样片，其次是低画质的样片
    match = (re.search(r'movie_src\s*:\s*"([^"]+\.mp4)"', text)
             or re.search(r'emvideo\s*=\s*"([^"]+\.mp4)"', text))
    preview_video = _abs_url(match.group(1), page_url) if match else None

    # 整理数据并更新movie的相应属性
    movie.dvdid = f'HEYZO-{num}'
    movie.title = title
    movie.plot = plot or None
    movie.cover = _abs_url(cover, page_url)
    movie.preview_pics = preview_pics or None
    movie.preview_video = preview_video
    movie.actress = actress
    movie.genre = genre
    movie.serial = serial
    movie.producer = 'HEYZO'
    movie.publish_date = publish_date
    movie.duration = str(duration) if duration else None
    movie.score = score
    movie.uncensored = True


if __name__ == "__main__":
    import pretty_errors
    pretty_errors.configure(display_link=True)
    logger.root.handlers[1].level = logging.DEBUG

    movie = MovieInfo('HEYZO-2500')
    try:
        parse_data(movie)
        print(movie)
    except CrawlerError as e:
        logger.error(e, exc_info=1)
