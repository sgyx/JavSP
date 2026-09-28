"""从FC2官网抓取数据"""
import logging


from javsp.web.base import get_html, request_get, resp2html
from javsp.web.exceptions import *
from javsp.config import Cfg
from javsp.lib import strftime_to_minutes
from javsp.datatype import MovieInfo


logger = logging.getLogger(__name__)
base_url = 'https://adult.contents.fc2.com'


def get_first(lst):
    """返回列表的第一个元素，列表为空时返回None"""
    return lst[0] if lst else None


def fix_url(url: str) -> str:
    """为省略了协议的地址（如'//example.com/a.jpg'）补全'https:'"""
    return 'https:' + url if url.startswith('//') else url


def get_movie_score(fc2_id):
    """通过评论数据来计算FC2的影片评分（10分制），无法获得评分时返回None"""
    html = get_html(f'{base_url}/article/{fc2_id}/review')
    review_tags = html.xpath("//ul[@class='items_comment_headerReviewInArea']/li")
    reviews = {}
    for tag in review_tags:
        score_str = get_first(tag.xpath("div/span/text()"))
        vote_tag = get_first(tag.xpath("span"))
        if score_str is None or vote_tag is None:
            continue
        reviews[int(score_str)] = int(vote_tag.text_content())
    total_votes = sum(reviews.values())
    if (total_votes >= 2):   # 至少也该有两个人评价才有参考意义一点吧
        summary = sum([k*v for k, v in reviews.items()])
        final_score = summary / total_votes * 2   # 乘以2转换为10分制
        return final_score


def parse_data(movie: MovieInfo):
    """解析指定番号的影片数据"""
    # 去除番号中的'FC2'字样
    id_uc = movie.dvdid.upper()
    if not id_uc.startswith('FC2-'):
        raise ValueError('Invalid FC2 number: ' + movie.dvdid)
    fc2_id = id_uc.replace('FC2-', '')
    # 抓取网页
    url = f'{base_url}/article/{fc2_id}/'
    resp = request_get(url)
    if '/id.fc2.com/' in resp.url:
        raise SiteBlocked('FC2要求当前IP登录账号才可访问，请尝试更换为日本IP')
    html = resp2html(resp)
    container = html.xpath("//div[@class='items_article_left']")
    if len(container) > 0:
        container = container[0]
    else:
        raise MovieNotFoundError(__name__, movie.dvdid)
    # FC2 标题增加反爬乱码，使用数组合并标题
    title_arr = container.xpath("//div[@class='items_article_headerInfo']/h3/text()")
    title = ''.join(title_arr)
    thumb_pic = get_first(container.xpath("//div[@class='items_article_MainitemThumb']/span/img/@src"))
    duration_str = get_first(container.xpath("//div[@class='items_article_MainitemThumb']/span/p[@class='items_article_info']/text()"))
    # FC2没有制作商和发行商的区分，作为个人市场，影片页面的'by'更接近于制作商
    producer = get_first(container.xpath("//li[text()='by ']/a/text()"))
    genre = container.xpath("//a[@class='tag tagTag']/text()")
    # 旧版页面的发售日期在'items_article_Releasedate'中，新版页面移到了'items_article_softDevice'中
    date_str = get_first(container.xpath("//div[@class='items_article_Releasedate']/p/text()")
                         or container.xpath("//div[@class='items_article_softDevice']/p[starts-with(text(),'販売日')]/text()"))
    publish_date = date_str[-10:].replace('/', '-') if date_str else None  # '販売日 : 2017/11/30'
    # 图片地址可能是省略了协议的'//contents-thumbnail2.fc2.com/...'形式
    preview_pics = [fix_url(i) for i in container.xpath("//ul[@data-feed='sample-images']/li/a/@href")]

    if Cfg().crawler.hardworking:
        # 通过评论数据来计算准确的评分
        score = get_movie_score(fc2_id)
        if score:
            movie.score = f'{score:.2f}'
        # 预览视频是动态加载的，不在静态网页中
        desc_frame_url = get_first(container.xpath("//section[@class='items_article_Contents']/iframe/@src"))
        if desc_frame_url:
            key = desc_frame_url.split('=')[-1]     # /widget/article/718323/description?ac=60fc08fa...
            api_url = f'{base_url}/api/v2/videos/{fc2_id}/sample?key={key}'
            r = request_get(api_url).json()
            movie.preview_video = r.get('path')
    else:
        # 获取影片评分。影片页面的评分只能粗略到星级，且没有分数，要通过类名来判断，如'items_article_Star5'表示5星
        score_tag_attr = get_first(container.xpath("//a[@class='items_article_Stars']/p/span/@class"))
        if score_tag_attr and score_tag_attr[-1].isdigit():
            score = int(score_tag_attr[-1]) * 2
            movie.score = f'{score:.2f}'

    movie.dvdid = id_uc
    movie.url = url
    movie.title = title
    movie.genre = genre
    movie.producer = producer
    if duration_str:
        movie.duration = str(strftime_to_minutes(duration_str))
    movie.publish_date = publish_date
    movie.preview_pics = preview_pics
    # FC2的封面是220x220的，和正常封面尺寸、比例都差太多。如果有预览图片，则使用第一张预览图作为封面
    if movie.preview_pics:
        movie.cover = preview_pics[0]
    else:
        movie.cover = fix_url(thumb_pic) if thumb_pic else None


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
