"""
Logger.py - カラー対応ロガー
Mirrors: Util/Logger.cpp
"""
import sys
import datetime


class Colors:
    RESET   = "\033[0m"
    RED     = "\033[91m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    BLUE    = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN    = "\033[96m"
    WHITE   = "\033[97m"
    BOLD    = "\033[1m"


class Logger:
    """シンプルなカラー対応ロガー"""
    debug_enabled = False

    def __init__(self, tag: str, enabled: bool = True):
        self.tag = tag
        self.enabled = enabled

    @staticmethod
    def set_debug(enabled: bool):
        Logger.debug_enabled = enabled

    def _now(self) -> str:
        return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]

    def _print(self, level: str, color: str, msg: str, force: bool = False):
        if not self.enabled and not force:
            return
        ts = self._now()
        print(f"{Colors.BOLD}{color}[{ts}] [{level}] [{self.tag}]{Colors.RESET} {msg}")
        sys.stdout.flush()

    def info(self, msg: str):
        self._print("INFO ", Colors.CYAN, msg)

    def ok(self, msg: str):
        self._print("OK   ", Colors.GREEN, msg)

    def warn(self, msg: str):
        self._print("WARN ", Colors.YELLOW, msg)

    def error(self, msg: str):
        self._print("ERROR", Colors.RED, msg)

    def debug(self, msg: str):
        if Logger.debug_enabled:
            self._print("DEBUG", Colors.WHITE, msg)

    def skin(self, msg: str):
        self._print("SKIN ", Colors.MAGENTA, msg)


# グローバルロガー (初期値)
log = Logger("SkinGetBE")
