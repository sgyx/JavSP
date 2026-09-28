"""配置日志：按照不同日志级别将日志信息输出到控制台和文件"""
import re
import getpass
import logging

from javsp.lib import re_escape, user_path


__all__ = ['setup_logging']


def log_filter(record):
    """只接受JavSP自身的日志，排除所依赖的库的日志"""
    rname = record.name
    return rname in ('main', '__main__') or rname.startswith('javsp.')


class ColoredFormatter(logging.Formatter):
    """为不同level的日志着色"""
    NO_STYLE = '\033[0m'
    COLOR_MAP = {
        logging.DEBUG:    '\033[1;30m',  # grey
        logging.WARNING:  '\033[1;33m',  # light yellow
        logging.ERROR:    '\033[1;31m',  # light red
        logging.CRITICAL: '\033[0;31m',  # red
    }

    def __init__(self, fmt='%(levelname)-8s:%(message)s',
                 datefmt='%Y-%m-%d %H:%M:%S', style='%', validate=True) -> None:
        super().__init__(fmt=fmt, datefmt=datefmt, style=style, validate=validate)

    def format(self, record):
        # 清除exc_info异常信息，保持终端输出的整洁
        record.exc_info = None
        record.exc_text = None
        raw = super().format(record)
        color = self.COLOR_MAP.get(record.levelno, self.NO_STYLE)
        return color + raw + self.NO_STYLE


class DetailedFormatter(logging.Formatter):
    """如果日志记录包含异常信息，则将传递给异常的参数一起记录下来"""
    def __init__(self, fmt='%(asctime)s %(name)s:%(lineno)d %(levelname)s: %(message)s',
                 datefmt='%Y-%m-%d %H:%M:%S', *args) -> None:
        super().__init__(fmt, datefmt, *args)
        username = getpass.getuser()
        self.anonymize = re.compile(r'([\\/]*)' + re_escape(username) + '([\\/]*)', flags=re.I)

    def format(self, record):
        raw = super().format(record)
        s = self.anonymize.sub(r'\1javsp\2', raw)
        return s

    def formatException(self, ei):
        s = super().formatException(ei)
        # ei[1] 是异常的实例，从中提取除了异常的message外的其他参数
        if len(ei[1].args) > 1:
            args = ei[1].args[1:]
            s += "\nArguments: \n  " + str(args).strip('(),')
        return s


def _file_handler(filename):
    """创建写入到程序所在目录的日志文件的handler，目录不可写时返回None"""
    try:
        return logging.FileHandler(filename=user_path(filename), mode='a', encoding='utf-8')
    except OSError:
        return None


def setup_logging():
    """配置日志：详细日志写入JavSP.log，文件移动记录写入FileMove.log，INFO及以上级别输出到控制台

    各抓取器模块在单独运行时通过 logger.root.handlers[1] 访问控制台handler，
    因此要保持文件handler在前、控制台handler在后的顺序
    """
    # 添加到root的filter无法对root的子logger生效（真是反直觉的设计），因此将filter添加到每一个handler
    # https://docs.python.org/3/library/logging.html#filter-objects
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    file_handler = _file_handler('JavSP.log')
    if file_handler is None:
        # 占位以保持控制台handler的位置不变
        file_handler = logging.NullHandler()
    file_handler.setLevel(logging.DEBUG)
    file_handler.addFilter(filter=log_filter)
    file_handler.setFormatter(DetailedFormatter())
    root_logger.addHandler(file_handler)
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.INFO)
    stream_handler.addFilter(filter=log_filter)
    stream_handler.setFormatter(ColoredFormatter(fmt='%(message)s'))
    root_logger.addHandler(stream_handler)

    filemove_logger = logging.getLogger('filemove')
    file_handler2 = _file_handler('FileMove.log')
    if file_handler2 is not None:
        file_handler2.addFilter(filter=lambda r: r.name == 'filemove')
        file_handler2.setFormatter(logging.Formatter(
            fmt='%(asctime)s\t%(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
        filemove_logger.addHandler(file_handler2)

    if isinstance(file_handler, logging.NullHandler):
        logging.getLogger('main').warning(f"无法在程序所在目录创建日志文件，将不会保存日志: '{user_path('')}'")
