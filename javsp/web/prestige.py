"""从蚊香社-prestige抓取数据"""
import re
import logging


from javsp.web.base import *
from javsp.web.base import xpath_first
from javsp.web.exceptions import *
from javsp.datatype import MovieInfo


logger = logging.getLogger(__name__)
base_url = 'https://www.prestige-av.com'
# prestige要求访问者携带已通过R18认证的cookies才能够获得完整数据，否则会被重定向到认证页面
# （其他多数网站的R18认证只是在网页上遮了一层，完整数据已经传回，不影响爬虫爬取）
cookies = {'__age_auth__': 'true'}


def parse_data(movie: MovieInfo):
    """从网页抓取并解析指定番号的数据
    Args:
        movie (MovieInfo): 要解析的影片信息，解析后的信息直接更新到此变量内
    """
    url = f'{base_url}/goods/goods_detail.php?sku={movie.dvdid}'
    resp = request_get(url, cookies=cookies, delay_raise=True)
    if resp.status_code == 500:
        # 500错误表明prestige没有这部影片的数据，不是网络问题，因此不再重试
        raise MovieNotFoundError(__name__, movie.dvdid)
    elif resp.status_code == 403:
        raise SiteBlocked('prestige不允许从当前IP所在地区访问，请尝试更换为日本地区代理')
    resp.raise_for_status()
    html = resp2html(resp)
    container_tags = html.xpath("//section[@class='px-4 mb-4 md:px-8 md:mb-16']")
    if not container_tags:
        raise MovieNotFoundError(__name__, movie.dvdid)

    container = container_tags[0]
    # 标题是必需字段，缺失时说明网页结构已变化
    title_span = xpath_first(container, "h1/span")
    if title_span is None or title_span.tail is None:
        raise WebsiteError(f'prestige: 网页结构可能已变化，找不到标题: {url}')
    title = title_span.tail.strip()
    # 以下均为可选字段，缺失时保持为空
    cover = xpath_first(container, "//div[@class='c-ratio-image mr-8']/picture/source/img/@src")
    if cover:
        cover = cover.split('?')[0]
    actress = container.xpath("//p[text()='出演者：']/following-sibling::div/p/a/text()")
    # 移除女优名中的空格，使女优名与其他网站保持一致
    actress = [i.strip().replace(' ', '') for i in actress]
    duration_label = xpath_first(container, "//p[text()='収録時間：']")
    duration_tag = duration_label.getnext() if duration_label is not None else None
    if duration_tag is not None:
        match = re.search(r'\d+', duration_tag.text_content())
        if match:
            movie.duration = match.group(0)
    date_url = xpath_first(container, "//p[text()='発売日：']/following-sibling::div/a/@href")
    publish_date = date_url.split('?date=')[-1] if date_url and '?date=' in date_url else None
    producer = xpath_first(container, "//p[text()='メーカー：']/following-sibling::div/a/text()")
    if producer is not None:
        producer = producer.strip()
    dvdid = xpath_first(container, "//p[text()='品番：']/following-sibling::div/p/text()")
    genre_tags = container.xpath("//p[text()='ジャンル：']/following-sibling::div/a")
    genre = [tag.text.strip() for tag in genre_tags if tag.text]
    serial = xpath_first(container, "//p[text()='レーベル：']/following-sibling::div/a/text()")
    if serial is not None:
        serial = serial.strip()
    # 部分页面的商品紹介直接是h2后面的p标签，没有外层的div
    plot_tag = xpath_first(container, "//h2[text()='商品紹介']/following-sibling::div/p")
    if plot_tag is None:
        plot_tag = xpath_first(container, "//h2[text()='商品紹介']/following-sibling::p")
    plot = plot_tag.text.strip() if plot_tag is not None and plot_tag.text else None
    preview_pics = container.xpath("//h2[text()='サンプル画像']/following-sibling::div/div/picture/source/img/@src")
    preview_pics = [i.split('?')[0] for i in preview_pics]

    # prestige改版后已经无法获取高清封面，此前已经获取的高清封面地址也已失效
    movie.url = url
    # 页面上没有品番时保留原有的dvdid
    if dvdid is not None:
        movie.dvdid = dvdid
    movie.title = title
    movie.cover = cover
    movie.actress = actress
    movie.publish_date = publish_date
    movie.producer = producer
    movie.genre = genre
    movie.serial = serial
    movie.plot = plot
    movie.preview_pics = preview_pics
    movie.uncensored = False    # prestige服务器在日本且面向日本国内公开发售，不会包含无码片


if __name__ == "__main__":
    import pretty_errors
    pretty_errors.configure(display_link=True)
    logger.root.handlers[1].level = logging.DEBUG

    movie = MovieInfo('ABP-647')
    try:
        parse_data(movie)
        print(movie)
    except CrawlerError as e:
        logger.error(e, exc_info=1)
