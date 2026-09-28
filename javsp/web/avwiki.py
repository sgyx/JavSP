"""从av-wiki抓取数据"""
import logging


from javsp.web.base import *
from javsp.web.exceptions import *
from javsp.datatype import MovieInfo

logger = logging.getLogger(__name__)
base_url = 'https://av-wiki.net'


def parse_data(movie: MovieInfo):
    """从网页抓取并解析指定番号的数据
    Args:
        movie (MovieInfo): 要解析的影片信息，解析后的信息直接更新到此变量内
    """
    movie.url = url = f'{base_url}/{movie.dvdid}'
    resp = request_get(url, delay_raise=True)
    if resp.status_code == 404:
        raise MovieNotFoundError(__name__, movie.dvdid)
    html = resp2html(resp)

    cover_tag = html.xpath("//header/div/a[@class='image-link-border']/img")
    if cover_tag:
        # 图片改为了懒加载，地址存放在data-srcset/data-src中
        img = cover_tag[0]
        try:
            srcset = (img.get('srcset') or img.get('data-srcset')).split(', ')
            src_set_urls = {}
            for src in srcset:
                url, width = src.split()
                width = int(width.rstrip('w'))
                src_set_urls[width] = url
            max_pic = sorted(src_set_urls.items(), key=lambda x:x[0], reverse=True)
            movie.cover = max_pic[0][1]
        except:
            src = img.get('src')
            movie.cover = img.get('data-src') if (not src or src.startswith('data:')) else src
        # 图片地址可能带有'?f=webp'等参数，会返回webp格式的图片，去掉参数以获取原始的jpg图片
        if movie.cover:
            movie.cover = movie.cover.split('?')[0]
    body = html.xpath("//section[@class='article-body']")[0]
    title = body.xpath("div/p/text()")[0]
    title = title.replace(f"【{movie.dvdid}】", '')
    info = body.xpath("dl[@class='dltable']")[0]
    dt_txt_ls, dd_tags = info.xpath("dt/text()"), info.xpath("dd")
    data = {}
    for dt_txt, dd in zip(dt_txt_ls, dd_tags):
        dt_txt = dt_txt.strip()
        a_tag = dd.xpath('a')
        if len(a_tag) == 0:
            dd_txt = dd.text.strip()
        else:
            dd_txt = [i.text.strip() for i in a_tag]
        if isinstance(dd_txt, list) and dt_txt != 'AV女優名':    # 只有女优名以列表的数据格式保留
            dd_txt = dd_txt[0]
        data[dt_txt] = dd_txt

    ATTR_MAP = {'メーカー': 'producer', 'AV女優名': 'actress', 'メーカー品番': 'dvdid', 'シリーズ': 'serial', '配信開始日': 'publish_date'}
    for key, attr in ATTR_MAP.items():
        setattr(movie, attr, data.get(key))
    movie.title = title
    movie.uncensored = False    # 服务器在日本且面向日本国内公开发售，不会包含无码片


if __name__ == "__main__":
    import pretty_errors
    pretty_errors.configure(display_link=True)

    movie = MovieInfo('259LUXU-593')
    try:
        parse_data(movie)
        print(movie)
    except CrawlerError as e:
        logger.error(e, exc_info=1)
