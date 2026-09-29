"""从subtitlecat.com获取字幕"""
import logging
from urllib.parse import quote, urljoin

from javsp.avid import get_id
from javsp.web.base import Request


logger = logging.getLogger(__name__)
base_url = 'https://www.subtitlecat.com/'
# 最多查看多少个同番号的字幕条目（每个条目需要请求一次详情页）
MAX_CANDIDATES = 5

request = Request()


def parse_search_page(html, avid: str) -> list[str]:
    """从搜索结果中筛选出番号一致的字幕条目，按评价和下载量排序后返回详情页的url

    站点的搜索是模糊匹配（搜索'ZZZZ-999'也会返回'zzzz'等无关结果），因此必须按番号过滤
    """
    candidates = []
    for row in html.xpath("//table[contains(@class,'sub-table')]//tr[td/a]"):
        link = row.xpath('td[1]/a')[0]
        match_id = get_id(link.text_content().strip())
        if not match_id or match_id.upper() != avid.upper():
            continue
        rated_good = bool(row.xpath("td[contains(@class,'sub-table__stars')]/span[contains(@title,'good')]"))
        downloads = 0
        for td in row.xpath("td[contains(@class,'sub-table__metric')]"):
            if td.xpath(".//img[contains(@src,'arrow-down')]"):
                text = td.xpath("string(.//span[@class='sub-table__metric-value'])")
                digits = ''.join(c for c in text if c.isdigit())
                downloads = int(digits) if digits else 0
        candidates.append((rated_good, downloads, urljoin(base_url, link.get('href'))))
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [url for _, _, url in candidates]


def parse_download_links(html, languages: list[str]) -> dict[str, str]:
    """从字幕详情页中提取指定语言的下载链接

    只有已经翻译好的语言才有下载链接，其余语言只有需要在线翻译的'Translate'按钮，不予考虑
    """
    links = {}
    for lang in languages:
        href = html.xpath(f"//a[@id='download_{lang}']/@href")
        if href:
            links[lang] = urljoin(base_url, href[0])
    return links


def get_subtitle(avid: str, languages: list[str]) -> tuple[str, bytes] | None:
    """搜索并下载字幕

    Returns:
        [tuple]: (语言, 字幕文件内容)，未找到时返回None
    """
    html = request.get_html(base_url + 'index.php?search=' + quote(avid))
    candidates = parse_search_page(html, avid)
    if not candidates:
        return None
    # 优先选择语言优先级更高的字幕，同一语言内选择评价/下载量更高的条目
    fetched = []
    for url in candidates[:MAX_CANDIDATES]:
        links = parse_download_links(request.get_html(url), languages)
        fetched.append(links)
        if languages[0] in links:
            break
    for lang in languages:
        for links in fetched:
            if lang in links:
                content = request.get(links[lang]).content
                # 防止将错误页面当作字幕保存
                if content.lstrip()[:1] == b'<':
                    logger.debug(f"下载的字幕不是有效的字幕文件: {links[lang]}")
                    continue
                return lang, content
    return None
