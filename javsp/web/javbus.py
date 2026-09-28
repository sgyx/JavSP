"""从JavBus抓取数据"""
import logging
import lxml.html


from javsp.web.base import *
from javsp.web.exceptions import *
from javsp.func import *
from javsp.config import Cfg, CrawlerID
from javsp.datatype import MovieInfo, GenreMap


logger = logging.getLogger(__name__)
genre_map = GenreMap('data/genre_javbus.csv')
permanent_url = 'https://www.javbus.com'
if Cfg().network.proxy_server is not None:
    base_url = permanent_url
else:
    base_url = str(Cfg().network.proxy_free[CrawlerID.javbus])


def parse_data(movie: MovieInfo):
    """从网页抓取并解析指定番号的数据
    Args:
        movie (MovieInfo): 要解析的影片信息，解析后的信息直接更新到此变量内
    """
    url = f'{base_url}/{movie.dvdid}'
    resp = request_get(url, delay_raise=True)
    # 疑似JavBus检测到类似爬虫的行为时会要求登录，不过发现目前不需要登录也可以从重定向前的网页中提取信息
    if resp.history and resp.history[0].status_code == 302:
        html = resp2html(resp.history[0])
    else:
        html = resp2html(resp)
    # 引入登录验证后状态码不再准确，因此还要额外通过检测标题来确认是否发生了404
    page_title = html.xpath('/html/head/title/text()')
    if page_title and page_title[0].startswith('404 Page Not Found!'):
        raise MovieNotFoundError(__name__, movie.dvdid)

    movie.url = f'{permanent_url}/{movie.dvdid}'
    parse_movie_page(movie, html)


def _label_next_text(info, label):
    """获取'導演:'等标签后面紧跟的元素的文本，不存在时返回None"""
    tag = xpath_first(info, f"p/span[text()='{label}']")
    next_tag = tag.getnext() if tag is not None else None
    return next_tag.text if next_tag is not None else None


def _label_tail_text(info, label):
    """获取'發行日期:'等标签后面的文本，不存在时返回None"""
    tag = xpath_first(info, f"p/span[text()='{label}']")
    return tag.tail.strip() if (tag is not None and tag.tail) else None


def parse_movie_page(movie: MovieInfo, html):
    """解析影片页面。只有标题是必需的，其他字段在网页中缺失时直接跳过"""
    container = xpath_first(html, "//div[@class='container']")
    title = xpath_first(container, "h3/text()") if container is not None else None
    if title is None:
        raise WebsiteError(f'JavBus: 网页结构可能已变化，找不到标题: {movie.url}')
    cover = xpath_first(container, "//a[@class='bigImage']/img/@src")
    preview_pics = container.xpath("//div[@id='sample-waterfall']/a/@href")
    info = xpath_first(container, "//div[@class='col-md-3 info']")
    # 找不到信息栏时用一个空元素代替，使后续的字段都按缺失处理
    if info is None:
        info = lxml.html.fromstring('<div></div>')
    dvdid = _label_next_text(info, '識別碼:') or movie.dvdid
    publish_date = _label_tail_text(info, '發行日期:')
    duration = _label_tail_text(info, '長度:')
    if duration:
        duration = duration.replace('分鐘', '').strip()
    director = _label_next_text(info, '導演:')
    if director:
        movie.director = director.strip()
    producer = _label_next_text(info, '製作商:')
    if producer:
        movie.producer = producer.strip()
    publisher = _label_next_text(info, '發行商:')
    if publisher:
        movie.publisher = publisher.strip()
    serial = _label_next_text(info, '系列:')
    if serial:
        movie.serial = serial
    # genre, genre_id
    genre_tags = info.xpath("//span[@class='genre']/label/a")
    genre, genre_id = [], []
    for tag in genre_tags:
        tag_url = tag.get('href') or ''
        pre_id = tag_url.split('/')[-1]
        genre.append(tag.text)
        if 'uncensored' in tag_url:
            movie.uncensored = True
            genre_id.append('uncensored-' + pre_id)
        else:
            movie.uncensored = False
            genre_id.append(pre_id)
    # JavBus的磁力链接是依赖js脚本加载的，无法通过静态网页来解析
    # actress, actress_pics
    actress, actress_pics = [], {}
    actress_tags = html.xpath("//a[@class='avatar-box']/div/img")
    for tag in actress_tags:
        name = tag.get('title')
        pic_url = tag.get('src')
        if not name:
            continue
        actress.append(name)
        if pic_url and not pic_url.endswith('nowprinting.gif'):     # 略过默认的头像
            actress_pics[name] = pic_url
    # 整理数据并更新movie的相应属性
    movie.dvdid = dvdid
    movie.title = title.replace(dvdid, '').strip()
    movie.cover = cover
    movie.preview_pics = preview_pics
    if publish_date and publish_date != '0000-00-00':    # 丢弃无效的发布日期
        movie.publish_date = publish_date
    movie.duration = duration if (duration and duration.isdigit() and int(duration)) else None
    movie.genre = genre
    movie.genre_id = genre_id
    movie.actress = actress
    movie.actress_pics = actress_pics


def parse_clean_data(movie: MovieInfo):
    """解析指定番号的影片数据并进行清洗"""
    parse_data(movie)
    movie.genre_norm = genre_map.map(movie.genre_id)
    movie.genre_id = None   # 没有别的地方需要再用到，清空genre id（暗示已经完成转换）


if __name__ == "__main__":
    import pretty_errors
    pretty_errors.configure(display_link=True)
    logger.root.handlers[1].level = logging.DEBUG

    movie = MovieInfo('NANP-030')
    try:
        parse_clean_data(movie)
        print(movie)
    except CrawlerError as e:
        logger.error(e, exc_info=1)
