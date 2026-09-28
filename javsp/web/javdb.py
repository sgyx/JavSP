"""从JavDB抓取数据"""
import os
import re
import logging
import threading

from javsp.web.base import Request, resp2html, xpath_first
from javsp.web.exceptions import *
from javsp.func import *
from javsp.avid import guess_av_type
from javsp.config import Cfg, CrawlerID
from javsp.datatype import MovieInfo, GenreMap
from javsp.chromium import get_browsers_cookies


# 初始化Request实例。需要指定网页语言，否则可能会返回其他语言网页，影响解析
# 注意: 不再使用cloudscraper，JavDB的CloudFlare现在反而会拦截cloudscraper的请求（403 Just a moment），普通请求则可以正常访问
request = Request()
accept_language = 'zh-CN,zh;q=0.9,zh-TW;q=0.8,en-US;q=0.7,en;q=0.6,ja;q=0.5'
request.headers['Accept-Language'] = accept_language

logger = logging.getLogger(__name__)
genre_map = GenreMap('data/genre_javdb.csv')
permanent_url = 'https://javdb.com'
if Cfg().network.proxy_server is not None:
    base_url = permanent_url
else:
    base_url = str(Cfg().network.proxy_free[CrawlerID.javdb])


def get_html_wrapper(url):
    """包装外发的request请求并负责转换为可xpath的html，同时处理Cookies无效等问题"""
    global request, cookies_pool
    used_request = request
    r = used_request.get(url, delay_raise=True)
    if r.status_code == 200:
        # 发生重定向可能仅仅是域名重定向，因此还要检查url以判断是否被跳转到了登录页
        if r.history and '/login' in r.url:
            with _cookies_lock:
                # 并行整理时其他线程可能已经更换了Cookies，此时直接使用新的Cookies重试
                if request is used_request:
                    _switch_cookies()
            # 在释放锁之后再重试，避免重试时再次遇到登录页而发生死锁
            return get_html_wrapper(url)
        elif r.history and 'pay' in r.url.split('/')[-1]:
            raise SitePermissionError(f"JavDB: 此资源被限制为仅VIP可见: '{r.history[0].url}'")
        else:
            html = resp2html(r)
            return html
    elif r.status_code in (403, 503):
        html = resp2html(r)
        code_tag = html.xpath("//span[@class='code-label']/span")
        error_code = code_tag[0].text if code_tag else None
        if error_code:
            if error_code == '1020':
                block_msg = f'JavDB: {r.status_code} 禁止访问: 站点屏蔽了来自日本地区的IP地址，请使用其他地区的代理服务器'
            else:
                block_msg = f'JavDB: {r.status_code} 禁止访问: {url} (Error code: {error_code})'
        else:
            block_msg = f'JavDB: {r.status_code} 禁止访问: {url}'
        raise SiteBlocked(block_msg)
    else:
        raise WebsiteError(f'JavDB: {r.status_code} 非预期状态码: {url}')


_cookies_lock = threading.Lock()


def _switch_cookies():
    """读取浏览器中JavDB的Cookies并更换为下一个可用的Cookies（调用时需持有_cookies_lock）"""
    global request, cookies_pool
    # 仅在需要时去读取Cookies
    if 'cookies_pool' not in globals():
        try:
            cookies_pool = get_browsers_cookies()
        except (PermissionError, OSError) as e:
            logger.warning(f"无法从浏览器Cookies文件获取JavDB的登录凭据({e})，可能是安全软件在保护浏览器Cookies文件", exc_info=True)
            cookies_pool = []
        except Exception as e:
            logger.warning(f"获取JavDB的登录凭据时出错({e})，你可能使用的是国内定制版等非官方Chrome系浏览器", exc_info=True)
            cookies_pool = []
    if len(cookies_pool) > 0:
        item = cookies_pool.pop()
        # 更换Cookies时创建新的request实例，避免沿用之前的请求设置
        new_request = Request()
        new_request.headers['Accept-Language'] = accept_language
        new_request.cookies = item['cookies']
        request = new_request
        cookies_source = (item['profile'], item['site'])
        logger.debug(f'未携带有效Cookies而发生重定向，尝试更换Cookies为: {cookies_source}')
    else:
        raise CredentialError('JavDB: 所有浏览器Cookies均已过期')

def get_user_info(site, cookies):
    """获取cookies对应的JavDB用户信息"""
    try:
        request.cookies = cookies
        html = request.get_html(f'https://{site}/users/profile')
    except Exception as e:
        logger.info('JavDB: 获取用户信息时出错')
        logger.debug(e, exc_info=1)
        return
    # 扫描浏览器得到的Cookies对应的临时域名可能会过期，因此需要先判断域名是否仍然指向JavDB的站点
    if 'JavDB' in html.text:
        email = html.xpath("//div[@class='user-profile']/ul/li[1]/span/following-sibling::text()")[0].strip()
        username = html.xpath("//div[@class='user-profile']/ul/li[2]/span/following-sibling::text()")[0].strip()
        return email, username
    else:
        logger.debug('JavDB: 域名已过期: ' + site)


def get_valid_cookies():
    """扫描浏览器，获取一个可用的Cookies"""
    # 经测试，Cookies所发往的域名不需要和登录时的域名保持一致，只要Cookies有效即可在多个域名间使用
    for d in cookies_pool:
        info = get_user_info(d['site'], d['cookies'])
        if info:
            return d['cookies']
        else:
            logger.debug(f"{d['profile']}, {d['site']}: Cookies无效")


def parse_data(movie: MovieInfo):
    """从网页抓取并解析指定番号的数据
    Args:
        movie (MovieInfo): 要解析的影片信息，解析后的信息直接更新到此变量内
    """
    # JavDB搜索番号时会有多个搜索结果，从中查找匹配番号的那个
    search_url = f'{base_url}/search?q={movie.dvdid}'
    html = get_html_wrapper(search_url)
    ids = list(map(str.lower, html.xpath("//div[@class='video-title']/strong/text()")))
    movie_urls = html.xpath("//a[@class='box']/@href")
    match_count = len([i for i in ids if i == movie.dvdid.lower()])
    if match_count == 0:
        raise MovieNotFoundError(__name__, movie.dvdid, ids)
    elif match_count == 1:
        index = ids.index(movie.dvdid.lower())
        # 番号列表与链接列表数量不一致时说明搜索结果的结构已变化
        if index >= len(movie_urls):
            raise WebsiteError(f'JavDB: 网页结构可能已变化，找不到搜索结果的链接: {search_url}')
        new_url = movie_urls[index]
        try:
            html2 = get_html_wrapper(new_url)
        except (SitePermissionError, CredentialError):
            # 不开VIP不让看，过分。决定榨出能获得的信息，毕竟有时候只有这里能找到标题和封面
            boxes = html.xpath("//a[@class='box']")
            if index >= len(boxes):
                raise WebsiteError(f'JavDB: 网页结构可能已变化，找不到搜索结果: {search_url}')
            parse_search_box(movie, boxes[index], new_url)
            return
    else:
        raise MovieDuplicateError(__name__, movie.dvdid, match_count)

    parse_detail(movie, html2, new_url)


def parse_score(score_str):
    """从形如'4.5分, 由xx人評價'的文本中解析评分并转换为10分制，解析失败时返回None"""
    if not score_str:
        return None
    match = re.search(r'([\d.]+)分', score_str)
    if not match:
        return None
    try:
        return "{:.2f}".format(float(match.group(1))*2)
    except ValueError:
        return None


def parse_search_box(movie: MovieInfo, box, url):
    """从搜索结果中的影片条目提取有限的信息（用于仅VIP可见的影片）"""
    movie.url = url
    movie.title = (box.get('title') or '').strip() or None
    movie.cover = xpath_first(box, "div/img/@src")
    score_tag = xpath_first(box, "div[@class='score']/span/span")
    if score_tag is not None:
        movie.score = parse_score(score_tag.tail)
    publish_date = xpath_first(box, "div[@class='meta']/text()")
    if publish_date is not None:
        movie.publish_date = publish_date.strip()


def get_info_value(info, label):
    """获取信息面板中指定标签(如'導演:')后的值节点，找不到时返回None"""
    tag = xpath_first(info, f"div/strong[text()='{label}']")
    return tag.getnext() if tag is not None else None


def parse_detail(movie: MovieInfo, html, url):
    """解析影片详情页，解析后的信息直接更新到movie内"""
    container = xpath_first(html, "/html/body/section/div/div[@class='video-detail']")
    if container is None:
        raise WebsiteError(f'JavDB: 网页结构可能已变化，找不到影片信息容器: {url}')
    info = xpath_first(container, "//nav[@class='panel movie-panel-info']")
    if info is None:
        raise WebsiteError(f'JavDB: 网页结构可能已变化，找不到影片信息面板: {url}')
    title = xpath_first(container, "h2/strong[@class='current-title']/text()")
    if title is None:
        raise WebsiteError(f'JavDB: 网页结构可能已变化，找不到标题: {url}')
    show_orig_title = container.xpath("//a[contains(@class, 'meta-link') and not(contains(@style, 'display: none'))]")
    if show_orig_title:
        ori_title = xpath_first(container, "h2/span[@class='origin-title']/text()")
        if ori_title is not None:
            movie.ori_title = ori_title
    cover = xpath_first(container, "//img[@class='video-cover']/@src")
    preview_pics = container.xpath("//a[@class='tile-item'][@data-fancybox='gallery']/@href")
    preview_video_tag = container.xpath("//video[@id='preview-video']/source/@src")
    if preview_video_tag:
        preview_video = preview_video_tag[0]
        if preview_video.startswith('//'):
            preview_video = 'https:' + preview_video
        movie.preview_video = preview_video
    # 找不到网页上的番号时沿用原有番号
    dvdid_tag = xpath_first(info, "div/span")
    dvdid = dvdid_tag.text_content() if dvdid_tag is not None else movie.dvdid
    date_tag = get_info_value(info, '日期:')
    publish_date = date_tag.text if date_tag is not None else None
    duration_tag = get_info_value(info, '時長:')
    duration = None
    if duration_tag is not None and duration_tag.text is not None:
        duration = duration_tag.text.replace('分鍾', '').strip()
    director_tag = get_info_value(info, '導演:')
    if director_tag is not None:
        movie.director = director_tag.text_content().strip()
    av_type = guess_av_type(movie.dvdid)
    if av_type != 'fc2':
        producer_tag = get_info_value(info, '片商:')
    else:
        producer_tag = get_info_value(info, '賣家:')
    if producer_tag is not None:
        movie.producer = producer_tag.text_content().strip()
    publisher_tag = get_info_value(info, '發行:')
    if publisher_tag is not None:
        movie.publisher = publisher_tag.text_content().strip()
    serial_tag = get_info_value(info, '系列:')
    if serial_tag is not None:
        movie.serial = serial_tag.text_content().strip()
    score_tag = xpath_first(info, "//span[@class='score-stars']")
    if score_tag is not None:
        score = parse_score(score_tag.tail)
        if score is not None:
            movie.score = score
    genre_tags = info.xpath("//strong[text()='類別:']/../span/a")
    genre, genre_id = [], []
    for tag in genre_tags:
        href = tag.get('href')
        if not href:
            continue
        pre_id = href.split('/')[-1]
        genre.append(tag.text)
        genre_id.append(pre_id)
        # 判定影片有码/无码
        subsite = pre_id.split('?')[0]
        movie.uncensored = {'uncensored': True, 'tags':False}.get(subsite)
    # JavDB目前同时提供男女优信息，根据用来标识性别的符号筛选出女优
    actress = None
    actors_tag = xpath_first(info, "//strong[text()='演員:']/../span")
    if actors_tag is not None:
        all_actors = actors_tag.xpath("a/text()")
        genders = actors_tag.xpath("strong/text()")
        if genders:
            actress = [i for i, g in zip(all_actors, genders) if g == '♀']
        else:
            # 网页上没有性别符号时（如未登录），改为根据女优链接的class筛选
            actress = actors_tag.xpath("a[contains(@class, 'actor-female')]/text()")
    # 磁力链接所在元素的class曾为'magnet-name column is-four-fifths'，现在只有'magnet-name'，两种都要匹配
    magnet = container.xpath("//div[contains(concat(' ', normalize-space(@class), ' '), ' magnet-name ')]/a/@href")

    movie.dvdid = dvdid
    movie.url = url.replace(base_url, permanent_url)
    movie.title = title.replace(dvdid, '').strip()
    movie.cover = cover
    movie.preview_pics = preview_pics
    movie.publish_date = publish_date
    movie.duration = duration
    movie.genre = genre
    movie.genre_id = genre_id
    movie.actress = actress
    movie.magnet = [i.replace('[javdb.com]','') for i in magnet]


def parse_clean_data(movie: MovieInfo):
    """解析指定番号的影片数据并进行清洗"""
    try:
        parse_data(movie)
        # 检查封面URL是否真的存在对应图片
        if movie.cover is not None:
            r = request.head(movie.cover)
            if r.status_code != 200:
                movie.cover = None
    except SiteBlocked:
        raise
        logger.error('JavDB: 可能触发了反爬虫机制，请稍后再试')
    if movie.genre_id and (not movie.genre_id[0].startswith('fc2?')):
        movie.genre_norm = genre_map.map(movie.genre_id)
        movie.genre_id = None   # 没有别的地方需要再用到，清空genre id（表明已经完成转换）


def collect_actress_alias(type=0, use_original=True):
    """
    收集女优的别名
    type: 0-有码, 1-无码, 2-欧美
    use_original: 是否使用原名而非译名，True-田中レモン，False-田中檸檬
    """
    import json
    import time
    import random

    actressAliasMap = {}

    actressAliasFilePath = "data/actress_alias.json"
    # 检查文件是否存在
    if not os.path.exists(actressAliasFilePath):
        # 如果文件不存在，创建文件并写入空字典
        with open(actressAliasFilePath, "w", encoding="utf-8") as file:
            json.dump({}, file)

    typeList = ["censored", "uncensored", "western"]
    page_url = f"{base_url}/actors/{typeList[type]}"
    while True:
        try:
            html = get_html_wrapper(page_url)
            actors = html.xpath("//div[@class='box actor-box']/a")

            count = 0
            for actor in actors:
                count += 1
                actor_name = actor.xpath("strong/text()")[0].strip()
                actor_url = actor.xpath("@href")[0]
                # actor_url = f"https://javdb.com{actor_url}"  # 构造演员主页的完整URL

                # 进入演员主页，获取更多信息
                actor_html = get_html_wrapper(actor_url)
                # 解析演员所有名字信息
                names_span = actor_html.xpath("//span[@class='actor-section-name']")[0]
                aliases_span_list = actor_html.xpath("//span[@class='section-meta']")
                aliases_span = aliases_span_list[0]

                names_list = [name.strip() for name in names_span.text.split(",")]
                if len(aliases_span_list) > 1:
                    aliases_list = [
                        alias.strip() for alias in aliases_span.text.split(",")
                    ]
                else:
                    aliases_list = []

                # 将信息添加到actressAliasMap中
                actressAliasMap[names_list[-1 if use_original else 0]] = (
                    names_list + aliases_list
                )
                print(
                    f"{count} --- {names_list[-1 if use_original else 0]}: {names_list + aliases_list}"
                )

                if count == 10:
                    # 将数据写回文件
                    with open(actressAliasFilePath, "r", encoding="utf-8") as file:
                        existing_data = json.load(file)

                    # 合并现有数据和新爬取的数据
                    existing_data.update(actressAliasMap)

                    # 将合并后的数据写回文件
                    with open(actressAliasFilePath, "w", encoding="utf-8") as file:
                        json.dump(existing_data, file, ensure_ascii=False, indent=2)

                    actressAliasMap = {}  # 重置actressAliasMap

                    print(
                        f"已爬取 {count} 个女优，数据已更新并写回文件:",
                        actressAliasFilePath,
                    )

                    # 重置计数器
                    count = 0

                time.sleep(max(1, 10 * random.random()))  # 随机等待 1-10 秒

            # 判断是否有下一页按钮
            next_page_link = html.xpath(
                "//a[@rel='next' and @class='pagination-next']/@href"
            )
            if not next_page_link:
                break  # 没有下一页，结束循环
            else:
                next_page_url = f"{next_page_link[0]}"
                page_url = next_page_url

        except SiteBlocked:
            raise

    with open(actressAliasFilePath, "r", encoding="utf-8") as file:
        existing_data = json.load(file)

    # 合并现有数据和新爬取的数据
    existing_data.update(actressAliasMap)

    # 将合并后的数据写回文件
    with open(actressAliasFilePath, "w", encoding="utf-8") as file:
        json.dump(existing_data, file, ensure_ascii=False, indent=2)

    print(f"已爬取 {count} 个女优，数据已更新并写回文件:", actressAliasFilePath)


if __name__ == "__main__":
    # collect_actress_alias()
    movie = MovieInfo('FC2-2735981')
    try:
        parse_clean_data(movie)
        print(movie)
    except CrawlerError as e:
        print(repr(e))
