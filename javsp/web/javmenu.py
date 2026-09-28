"""从JavMenu抓取数据"""
import logging

from javsp.web.base import Request, resp2html
from javsp.web.exceptions import *
from javsp.datatype import MovieInfo


request = Request()

logger = logging.getLogger(__name__)
base_url = 'https://mrzyx.xyz'


def parse_data(movie: MovieInfo):
    """从网页抓取并解析指定番号的数据
    Args:
        movie (MovieInfo): 要解析的影片信息，解析后的信息直接更新到此变量内
    """
    # JavMenu网页做得很不走心，将就了
    url = f'{base_url}/{movie.dvdid}'
    r = request.get(url)
    if r.history:
        # 被重定向到主页说明找不到影片资源
        raise MovieNotFoundError(__name__, movie.dvdid)

    html = resp2html(r)
    # 现在找不到影片时不再重定向，而是直接返回状态码200的'猜你喜歡'页面，只能通过有无影片资料卡来判断
    info = html.xpath("//div[contains(@class, 'left-wrapper')]/div[contains(@class, 'card')]/div[@class='card-body']")
    if not info:
        raise MovieNotFoundError(__name__, movie.dvdid)
    info = info[0]
    container = html.xpath("//div[contains(@class, 'col-md-9')]")[0]
    # 标题中番号和广告语之间夹杂着大量换行和空格
    title = container.xpath("div/h1/strong")[0].text_content().strip()
    # 竟然还在标题里插广告，真的疯了。要不是我已经写了抓取器，才懒得维护这个破站
    title = title.replace('  | JAV目錄大全 | 每日更新', '')
    title = title.replace(' 免費在線看', '').replace('免費AV在線看', '')
    # 有在线播放源时封面是播放器的poster，否则是一张普通的图片（FC2影片是JavDB的封面）
    cover_tag = container.xpath("div/div[@class='single-video']")
    if cover_tag:
        # URL首尾竟然也有空格……
        cover = cover_tag[0].xpath("video/@poster | video/@data-poster | img/@src")
        if cover:
            movie.cover = cover[0].strip()
    # 预览影片是JavDB带签名和时间戳的m3u8链接，很快会失效，就不抓了
    # movie.preview_video = container.xpath("//video[@id='player-preview']/source/@src")
    publish_date = info.xpath("div/span[contains(text(), '發佈於:')]/following-sibling::span/text()")
    if publish_date:
        movie.publish_date = publish_date[0].strip()
    duration = info.xpath("div/span[contains(text(), '時長:')]/following-sibling::span/text()")
    if duration:
        movie.duration = duration[0].replace('分鐘', '').strip()
    producer = info.xpath("div/span[contains(text(), '製作:')]/following-sibling::a/span/text()")
    if producer:
        movie.producer = producer[0].strip()
    genre_tags = info.xpath("div/span[contains(text(), '類別:')]/following-sibling::div/a[@class='genre']")
    genre, genre_id = [], []
    for tag in genre_tags:
        items = tag.get('href').split('/')
        pre_id = items[-3] + '/' + items[-1]
        genre.append(tag.text.strip())
        genre_id.append(pre_id)
        # genre的链接中含有censored字段，但是无法用来判断影片是否有码，因为完全不可靠……
    # 没有女优信息时显示的是'暫無女優資料'，不带链接，因此只取<a>即可
    actress = info.xpath("div/span[contains(text(), '女優:')]/following-sibling::div/a[contains(@class, 'actress')]/text()")
    actress = [i.strip() for i in actress if i.strip()] or None
    magnet_table = container.xpath("//table[contains(@class, 'magnet-table')]/tbody")
    if magnet_table:
        magnet_links = magnet_table[0].xpath("tr/td/a/@href")
        # 它的FC2数据是从JavDB抓的，JavDB更换图片服务器后它也跟上了，似乎数据更新频率还可以
        movie.magnet = [i.replace('[javdb.com]','') for i in magnet_links]
    preview_pics = container.xpath("//a[@data-fancybox='gallery']/@href")

    if (not movie.cover) and preview_pics:
        movie.cover = preview_pics[0]
    movie.url = url
    movie.title = title.replace(movie.dvdid, '').strip()
    movie.preview_pics = preview_pics
    movie.genre = genre
    movie.genre_id = genre_id
    movie.actress = actress


if __name__ == "__main__":
    import pretty_errors
    pretty_errors.configure(display_link=True)
    logger.root.handlers[1].level = logging.DEBUG

    movie = MovieInfo('FC2-718323')
    try:
        parse_data(movie)
        print(movie)
    except CrawlerError as e:
        logger.error(e, exc_info=1)
