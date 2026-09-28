"""从蚊香社-mgstage抓取数据"""
import re
import logging
import requests


from javsp.web.base import Request, resp2html, xpath_first
from javsp.web.exceptions import *
from javsp.config import Cfg
from javsp.datatype import MovieInfo


logger = logging.getLogger(__name__)
base_url = 'https://www.mgstage.com'
# 初始化Request实例（要求携带已通过R18认证的cookies，否则会被重定向到认证页面）
request = Request()
request.cookies = {'adc': '1'}


def parse_data(movie: MovieInfo):
    """解析指定番号的影片数据"""
    url = f'{base_url}/product/product_detail/{movie.dvdid}/'
    resp = request.get(url, delay_raise=True)
    if resp.status_code == 403:
        raise SiteBlocked('mgstage不允许从当前IP所在地区访问，请尝试更换为日本地区代理')
    # url不存在时会被重定向至主页。history非空时说明发生了重定向
    elif resp.history:
        raise MovieNotFoundError(__name__, movie.dvdid)

    html = resp2html(resp)
    # mgstage的文本中含有大量的空白字符（'\n \t'），需要使用strip去除
    # 标题和详情容器是必需的，缺失时说明网页结构已变化
    title_text = xpath_first(html, "//div[@class='common_detail_cover']/h1/text()")
    if title_text is None:
        raise WebsiteError(f'mgstage: 网页结构可能已变化，找不到标题: {url}')
    title = title_text.strip()
    container = xpath_first(html, "//div[@class='detail_left']")
    if container is None:
        raise WebsiteError(f'mgstage: 网页结构可能已变化，找不到影片信息: {url}')
    # 以下均为可选字段，缺失时保持为空
    cover = xpath_first(container, "//a[@id='EnlargeImage']/@href")
    # 有链接的女优和仅有文本的女优匹配方法不同，因此分别匹配以后合并列表
    actress_text = container.xpath("//th[text()='出演：']/following-sibling::td/text()")
    actress_link = container.xpath("//th[text()='出演：']/following-sibling::td/a/text()")
    actress = [i.strip() for i in actress_text + actress_link]
    actress = [i for i in actress if i]     # 移除空字符串
    producer = xpath_first(container, "//th[text()='メーカー：']/following-sibling::td/a/text()")
    if producer is not None:
        producer = producer.strip()
    duration_str = xpath_first(container, "//th[text()='収録時間：']/following-sibling::td/text()")
    match = re.search(r'\d+', duration_str) if duration_str else None
    if match:
        movie.duration = match.group(0)
    dvdid = xpath_first(container, "//th[text()='品番：']/following-sibling::td/text()")
    date_str = xpath_first(container, "//th[text()='配信開始日：']/following-sibling::td/text()")
    publish_date = date_str.replace('/', '-') if date_str is not None else None
    serial_tag = container.xpath("//th[text()='シリーズ：']/following-sibling::td/a/text()")
    if serial_tag:
        movie.serial = serial_tag[0].strip()
    # label: 大意是某个系列策划用同样的番号，例如ABS打头的番号label是'ABSOLUTELY PERFECT'，暂时用不到
    # label = container.xpath("//th[text()='レーベル：']/following-sibling::td/text()")[0].strip()
    genre_tags = container.xpath("//th[text()='ジャンル：']/following-sibling::td/a")
    genre = [i.text.strip() for i in genre_tags if i.text]
    score_tag = xpath_first(container, "//td[@class='review']/span")
    score_str = (score_tag.tail or '').strip() if score_tag is not None else ''
    match = re.search(r'^[\.\d]+', score_str)
    if match:
        try:
            score = float(match.group()) * 2
            movie.score = f'{score:.2f}'
        except ValueError:  # 例如匹配到了'..'
            logger.debug(f"mgstage: 无法解析评分: '{score_str}'")
    # plot可能含有嵌套格式，为了保留plot中的换行关系，手动处理plot中的各个标签
    plots = []
    plot_p_tags = container.xpath("//dl[@id='introduction']/dd/p[not(@class='more')]")
    for p in plot_p_tags:
        children = p.getchildren()
        # 没有children时表明plot不含有格式，此时简单地提取文本就可以
        if not children:
            plots.append(p.text_content())
            continue
        for child in children:
            # plots为空时（以<br>开头）不需要插入换行
            if child.tag == 'br' and plots and plots[-1] != '\n':
                plots.append('\n')
            else:
                if child.text:
                    plots.append(child.text)
                if child.tail:
                    plots.append(child.tail)
    plot = ''.join(plots).strip()
    preview_pics = container.xpath("//a[@class='sample_image']/@href")

    if Cfg().crawler.hardworking:
        # 预览视频是点击按钮后再加载的，不在静态网页中
        btn_url = xpath_first(container, "//a[@class='button_sample']/@href")
        if btn_url:
            video_pid = btn_url.split('/')[-1]
            req_url = f'{base_url}/sampleplayer/sampleRespons.php?pid={video_pid}'
            # 预览视频只是附加信息，获取失败时不应影响其他字段
            try:
                resp = request.get(req_url).json()
            except (requests.exceptions.RequestException, ValueError) as e:
                logger.debug(f'mgstage: 获取预览视频失败: {e}')
                resp = None
            video_url = resp.get('url') if isinstance(resp, dict) else None
            if video_url and isinstance(video_url, str):
                # /sample/shirouto/siro/3093/SIRO-3093_sample.ism/request?uid=XXX&amp;pid=XXX
                preview_video = video_url.split('.ism/')[0] + '.mp4'
                movie.preview_video = preview_video

    # 页面上没有品番时保留原有的dvdid
    if dvdid is not None:
        movie.dvdid = dvdid
    movie.url = url
    movie.title = title
    movie.cover = cover
    movie.actress = actress
    movie.producer = producer
    movie.publish_date = publish_date
    movie.genre = genre
    movie.plot = plot
    movie.preview_pics = preview_pics
    movie.uncensored = False    # 服务器在日本且面向日本国内公开发售，不会包含无码片


if __name__ == "__main__":
    import pretty_errors
    pretty_errors.configure(display_link=True)
    logger.root.handlers[1].level = logging.DEBUG

    movie = MovieInfo('HRV-045')
    try:
        parse_data(movie)
        print(movie)
    except CrawlerError as e:
        logger.error(e, exc_info=1)
