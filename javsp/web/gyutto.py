"""从https://gyutto.com/官网抓取数据"""
import logging
import time

from javsp.web.base import resp2html, request_get, xpath_first
from javsp.web.exceptions import *
from javsp.datatype import MovieInfo

logger = logging.getLogger(__name__)

# https://dl.gyutto.com/i/item266923
base_url = 'http://gyutto.com'
base_encode = 'euc-jp'

def get_movie_title(html):
    """获取标题，找不到时返回None"""
    container = xpath_first(html, "//h1")
    if container is None:
        return None
    title = container.text
    
    return title

def get_movie_img(html, index = 1):
    images = []
    container = html.xpath("//a[@class='highslide']/img")
    if index == 0:
        # 只获取封面，没有图片时返回None
        return container[0].get('src') if container else None

    for row in container:
        src = row.get('src')
        if src:
            images.append(src)

    return images

def parse_data(movie: MovieInfo):
    """解析指定番号的影片数据"""
    # 去除番号中的'gyutto'字样
    id_uc = movie.dvdid.upper()
    if not id_uc.startswith('GYUTTO-'):
        raise ValueError('Invalid gyutto number: ' + movie.dvdid)
    gyutto_id = id_uc.replace('GYUTTO-', '')
    # 抓取网页
    url = f'{base_url}/i/item{gyutto_id}?select_uaflag=1'
    r = request_get(url, delay_raise=True)
    if r.status_code == 404:
        raise MovieNotFoundError(__name__, movie.dvdid)
    html = resp2html(r, base_encode)
    parse_html(movie, html, id_uc, url)

def parse_html(movie: MovieInfo, html, id_uc, url):
    """从已解析的网页中提取影片数据（便于离线测试）"""
    # 基本信息和标题是必需的，缺失时说明网页结构可能已变化
    container = html.xpath("//dl[@class='BasicInfo clearfix']")
    if not container:
        raise WebsiteError(f'gyutto: 网页结构可能已变化，找不到基本信息: {url}')
    title = get_movie_title(html)
    if not (title and title.strip()):
        raise WebsiteError(f'gyutto: 网页结构可能已变化，找不到标题: {url}')

    # 以下均为可选字段，缺失时保持为None
    producer = genre = publish_date = None
    for row in container:
        key = xpath_first(row, ".//dt/text()")
        if key is None:
            continue
        key = key.strip()
        if key == "サークル":
            producer = ''.join(row.xpath(".//dd/a/text()")) or None
        elif key == "ジャンル":
            genre = row.xpath(".//dd/a/text()")
        elif key == "配信開始日":
            date = row.xpath(".//dd/text()")
            date_str = ''.join(date).strip()
            try:
                date_time = time.strptime(date_str, "%Y年%m月%d日")
                publish_date = time.strftime("%Y-%m-%d", date_time)
            except ValueError:
                logger.debug(f"无法解析配信開始日: '{date_str}'")

    plot = xpath_first(html, "//div[@class='unit_DetailLead']/p/text()")
    
    movie.title = title
    movie.cover = get_movie_img(html, 0)
    movie.preview_pics = get_movie_img(html)
    movie.dvdid = id_uc
    movie.url = url
    movie.producer = producer
    # movie.actress = actress
    # movie.duration = duration
    movie.publish_date = publish_date
    movie.genre = genre
    movie.plot = plot

if __name__ == "__main__":
    import pretty_errors

    pretty_errors.configure(display_link=True)
    logger.root.handlers[1].level = logging.DEBUG
    movie = MovieInfo('gyutto-266923')

    try:
        parse_data(movie)
        print(movie)
    except CrawlerError as e:
        logger.error(e, exc_info=1)
