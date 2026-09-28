"""从加勒比官网（Caribbeancom / Caribbeancom Premium）抓取数据"""
import re
import json
import logging


from javsp.web.base import *
from javsp.web.exceptions import *
from javsp.datatype import MovieInfo


logger = logging.getLogger(__name__)

# 两个站点的番号都是'MMDDYY'+'序号'的形式，区别在于分隔符：Caribbeancom使用'-'，Caribbeancom Premium使用'_'
SITES = {
    'carib': {
        'base_url': 'https://www.caribbeancom.com',
        'sep': '-',
        'name': 'カリビアンコム',
    },
    'caribpr': {
        'base_url': 'https://www.caribbeancompr.com',
        'sep': '_',
        'name': 'カリビアンコムプレミアム',
    },
}
# 由文件名推断出的属于其他片商的提示（这些片商由其他抓取器处理）
OTHER_STUDIOS = {'1pondo', '10musume', 'pacopacomama', 'muramura'}
id_pattern = re.compile(r'^(\d{2})(\d{2})(\d{2})[-_](\d{3})$')


def _select_site(movie: MovieInfo):
    """根据番号和片商提示选择要查询的站点，不属于本抓取器时返回None"""
    hint = getattr(movie, '_studio_hint', None)
    if hint in SITES:
        return hint
    if hint in OTHER_STUDIOS:
        return None
    # 没有提示时仅处理'-'分隔的番号：'_'分隔的番号同时也是一本道等片商的命名方式（同一个编号在不同片商下
    # 可能是完全不同的影片，如123119_001），贸然查询Caribbeancom Premium可能会混入另一部影片的数据
    if '-' in movie.dvdid:
        return 'carib'
    return None


def _spec_content(html, label):
    """获取'出演'等标签对应的内容节点，不存在时返回None"""
    return xpath_first(html, f"//li[contains(@class,'movie-spec')][span[@class='spec-title']='{label}']"
                             "/span[contains(@class,'spec-content')]")


def _spec_links(html, label):
    """获取'出演'、'タグ'等标签下所有链接的文本"""
    content = _spec_content(html, label)
    if content is None:
        return []
    texts = [a.text_content().strip() for a in content.xpath('.//a')]
    return [i for i in texts if i]


def _parse_duration(text):
    """将'01:10:39'形式的再生時間转换为分钟数（字符串），无法解析时返回None"""
    match = re.search(r'(?:(\d+):)?(\d+):(\d{2})', text or '')
    if not match:
        return None
    hours, minutes = int(match.group(1) or 0), int(match.group(2))
    total = hours * 60 + minutes
    return str(total) if total else None


def _parse_movie_var(text):
    """解析网页脚本中的'var Movie = {...};'，其中包含样品视频等信息"""
    match = re.search(r'var\s+Movie\s*=\s*(\{.*?\});', text)
    if not match:
        return {}
    try:
        return json.loads(match.group(1))
    except ValueError:
        logger.debug(f"无法解析影片的脚本数据: {match.group(1)}")
        return {}


def parse_data(movie: MovieInfo):
    """从网页抓取并解析指定番号的数据
    Args:
        movie (MovieInfo): 要解析的影片信息，解析后的信息直接更新到此变量内
    """
    # 本抓取器会被用于所有常规番号，因此不符合格式的番号必须在访问网络前就排除掉
    match = id_pattern.match(movie.dvdid or '')
    if not match:
        raise MovieNotFoundError(__name__, movie.dvdid)
    site_key = _select_site(movie)
    if site_key is None:
        raise MovieNotFoundError(__name__, movie.dvdid)
    site = SITES[site_key]
    month, day, year, num = match.groups()
    movie_id = f"{month}{day}{year}{site['sep']}{num}"
    url = f"{site['base_url']}/moviepages/{movie_id}/index.html"
    resp = request_get(url, delay_raise=True)
    if resp.status_code == 404:
        raise MovieNotFoundError(__name__, movie.dvdid)
    resp.raise_for_status()
    # 两个站点都是EUC-JP编码（Premium站的响应头中没有声明编码）。使用euc_jis_2004以兼容①等扩展字符
    html = resp2html(resp, encoding='euc_jis_2004')
    raw_text = resp.text

    # 两个站点的网页结构不同（Caribbeancom的标题带有itemprop，Premium则没有），但标题都位于'.movie-info .heading'下
    title_tag = xpath_first(html, "//div[contains(@class,'movie-info')]//div[@class='heading']/h1")
    title = title_tag.text_content().strip() if title_tag is not None else ''
    if not title:
        raise WebsiteError(f'{__name__}: 网页结构可能已变化，找不到标题: {url}')
    movie.url = url
    movie.title = title
    # 简介紧跟在标题之后（Caribbeancom为p[@itemprop='description']，Premium则是没有属性的p）
    plot_tag = xpath_first(title_tag, "../following-sibling::p[1]")
    if plot_tag is not None:
        plot = plot_tag.text_content().strip()
        if plot:
            movie.plot = plot

    # 封面：网页脚本中使用'/moviepages/<id>/images/l_l.jpg'作为播放器的封面
    movie.cover = f"{site['base_url']}/moviepages/{movie_id}/images/l_l.jpg"
    # 画廊中只有data-is_sample='1'的图片是公开的，其余图片的地址位于'/member/'下，访问时会跳转到登录页面
    pics = html.xpath("//a[contains(@class,'fancy-gallery')][@data-is_sample='1']/@href")
    preview_pics = [i for i in pics if '/member/' not in i]
    if preview_pics:
        movie.preview_pics = preview_pics
    # 样品视频：网页中的播放器仅在sample_flash_exists为1且sampleexclude_flag不为1时才使用样品视频
    movie_var = _parse_movie_var(raw_text)
    sample_url = movie_var.get('sample_flash_url')
    if (str(movie_var.get('sample_flash_exists')) == '1' and str(movie_var.get('sampleexclude_flag')) != '1'
            and sample_url):
        movie.preview_video = sample_url

    actress = _spec_links(html, '出演')
    if actress:
        movie.actress = actress
    genre = _spec_links(html, 'タグ')
    if genre:
        movie.genre = genre
    serial = _spec_links(html, 'シリーズ')
    if serial:
        movie.serial = serial[0]
    # Premium站同时销售其他片商的影片（如一本道、パコパコママ），此时以'スタジオ'作为制作商
    studio = _spec_links(html, 'スタジオ')
    movie.producer = studio[0] if studio else site['name']
    duration_tag = _spec_content(html, '再生時間')
    if duration_tag is not None:
        movie.duration = _parse_duration(duration_tag.text_content())
    # 用户评价是5星制（如'★★★★'），转换为10分制
    rating_tag = _spec_content(html, 'ユーザー評価')
    if rating_tag is not None:
        stars = rating_tag.text_content().count('★')
        if stars:
            movie.score = f'{stars * 2:.2f}'
    # 网页中没有发布日期，而番号的前6位就是配信日期（MMDDYY，两个站点均始于2000年后，因此年份按20YY处理）
    movie.publish_date = f'20{year}-{month}-{day}'
    movie.uncensored = True


if __name__ == "__main__":
    import pretty_errors
    pretty_errors.configure(display_link=True)
    logger.root.handlers[1].level = logging.DEBUG

    movie = MovieInfo('082713-417')
    try:
        parse_data(movie)
        print(movie)
    except CrawlerError as e:
        logger.error(e, exc_info=1)
