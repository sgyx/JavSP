"""从字幕网站下载外挂字幕"""
import os
import re
import json
import shutil
import logging
import subprocess

from javsp.config import Cfg, SubtitleProviderID
from javsp.datatype import Movie
from javsp.lib import list_sidecar_subtitles
from javsp.subtitle import subtitlecat


__all__ = ['download_subtitle', 'detect_hard_sub']


logger = logging.getLogger(__name__)

_providers = {
    SubtitleProviderID.subtitlecat: subtitlecat,
}

# 文件名或文件夹名中表示带有中文字幕的关键词（排除'无中文字幕'这类否定的写法）
_KEYWORD_PATTERN = re.compile(r'(?<![无無没沒未])(中文字幕|中字|字幕版|[简簡繁][中体體]|chinese[ ._-]?sub)', flags=re.I)
# 字幕轨道的标题中表示中文的关键词
_STREAM_TITLE_PATTERN = re.compile(r'中|chinese|chs|cht|[简簡繁]', flags=re.I)
_CHINESE_LANG_CODES = ('chi', 'zho', 'zh', 'chs', 'cht', 'cn')


def _has_chinese_sub_stream(filepath: str) -> bool:
    """使用ffprobe检查影片文件中是否有中文字幕轨道（未安装ffprobe时总是返回False）"""
    ffprobe = shutil.which('ffprobe')
    if not ffprobe:
        return False
    cmd = [ffprobe, '-v', 'error', '-select_streams', 's',
           '-show_entries', 'stream_tags=language,title', '-of', 'json', filepath]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=30, encoding='utf-8', errors='replace')
        streams = json.loads(r.stdout or '{}').get('streams', [])
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        logger.debug(f"ffprobe检查字幕轨道失败: {e!r}")
        return False
    for stream in streams:
        tags = {k.lower(): v for k, v in stream.get('tags', {}).items()}
        lang = tags.get('language', '').lower()
        title = tags.get('title', '')
        if lang in _CHINESE_LANG_CODES or lang.startswith('zh-'):
            return True
        if title and _STREAM_TITLE_PATTERN.search(title):
            return True
        # 未标注语言且没有标题的字幕轨道，对于这类影片来说基本都是中文字幕
        if lang in ('', 'und') and not title:
            return True
    return False


def detect_hard_sub(movie: Movie, video_path: str) -> str | None:
    """检测影片是否已经带有中文字幕

    画面中硬编码的字幕只能通过文件名上的标注来识别，文件名中没有标注的无法识别

    Returns:
        [str]: 判断依据，没有检测到字幕时返回None
    """
    if movie.hard_sub:
        return '文件名带有-C后缀'
    src = movie.files[0]
    filename = os.path.splitext(os.path.basename(src))[0]
    if _KEYWORD_PATTERN.search(filename):
        return '文件名中标注了中文字幕'
    # 形如'ABC-123ch'或'ABC-123-chs'的文件名
    avid_pattern = re.sub(r'[_-]', '[_-]*', movie.dvdid) + r'[_-]?(ch|chs|cht)\b'
    if re.search(avid_pattern, filename, flags=re.I):
        return '文件名带有ch后缀'
    folder_name = os.path.basename(os.path.dirname(os.path.abspath(src)))
    if _KEYWORD_PATTERN.search(folder_name):
        return '所在文件夹名中标注了中文字幕'
    if _has_chinese_sub_stream(video_path):
        return '影片内有中文字幕轨道'
    return None


def download_subtitle(movie: Movie) -> bool:
    """为影片下载外挂字幕，保存为与影片同名的字幕文件

    Returns:
        [bool]: 是否下载了字幕
    """
    cfg = Cfg().summarizer.subtitle
    if not movie.dvdid:
        logger.debug(f'{movie}没有番号，无法搜索字幕')
        return False
    if len(movie.files) != 1:
        logger.info(f'{movie.dvdid}: 暂不支持为多分片的影片下载字幕')
        return False
    # 整理影片文件后，影片已被移动到新的位置
    video_path = (getattr(movie, 'new_paths', None) or movie.files)[0]
    folder, stem = os.path.split(os.path.splitext(video_path)[0])
    if cfg.skip_hard_sub:
        reason = detect_hard_sub(movie, video_path)
        if reason:
            logger.info(f'{movie.dvdid}: {reason}，不下载字幕')
            return False
    if cfg.skip_if_exists:
        existing = list_sidecar_subtitles(folder, stem)
        if existing:
            logger.info(f"{movie.dvdid}: 已有字幕文件，不再下载: '{os.path.basename(existing[0][0])}'")
            return False
    for provider in cfg.providers:
        try:
            result = _providers[provider].get_subtitle(movie.dvdid, cfg.languages)
        except Exception as e:
            logger.warning(f'从{provider.value}获取字幕失败: {e!r}')
            continue
        if result:
            lang, content = result
            path = os.path.join(folder, stem + cfg.filename_suffix.format(lang=lang) + '.srt')
            with open(path, 'wb') as f:
                f.write(content)
            logger.info(f"已从{provider.value}下载字幕({lang}): '{os.path.basename(path)}'")
            return True
    logger.info(f'未在字幕网站上找到{movie.dvdid}的字幕')
    return False
