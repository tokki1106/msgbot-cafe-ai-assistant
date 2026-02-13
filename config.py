"""
Naver Cafe Auto-Reply Bot - Configuration
"""
import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# 프로젝트 루트 경로
BASE_DIR = Path(__file__).resolve().parent

# .env 로드
load_dotenv(BASE_DIR / ".env")


# === Naver Cafe 설정 ===
CAFE_ID = 29537083
CAFE_NAME = "nameyee"
MENU_ID_MONITOR = 3  # 질문 게시판
ALLOWED_MEMBER_LEVELS = None  # None이면 모든 멤버에게 답변 (디버그 모드)
# ALLOWED_MEMBER_LEVELS = {110, 120, 130, 888}  # 프로덕션용
BOT_NICK = "your_bot_nickname"  # 봇 계정 닉네임 (중복 댓글 방지용)

# === 로컬 지식 소스 (knowledge 폴더: instruction.md + *.md + *.js) ===
PRIORITY_KNOWLEDGE_PATH = BASE_DIR / "knowledge"
PRIORITY_INSTRUCTION_FILE = "instruction.md"
PRIORITY_CONTEXT_TOP_K = 4

# === 폴링 설정 ===
POLL_INTERVAL = 60  # 초

# === API URL 템플릿 ===
ARTICLE_DETAIL_URL = (
    "https://article.cafe.naver.com/gw/v4/cafes/{cafe_id}/articles/{article_id}"
    "?query=&menuId={menu_id}&boardType=L&useCafeId=true&requestFrom=A"
)
ARTICLE_LIST_URL = (
    "https://apis.naver.com/cafe-web/cafe2/ArticleListV2dot1.json"
    "?clubId={cafe_id}&menuId={menu_id}&page={page}&perPage={per_page}&queryType=lastArticle"
)

# === 인증 정보 ===
def _strip_env(key: str) -> str:
    """환경변수 값에서 작은따옴표/큰따옴표 제거"""
    val = os.getenv(key, "")
    return val.strip().strip("'").strip('"')



def _env_bool(key: str, default: bool = False) -> bool:
    val = _strip_env(key).lower()
    if not val:
        return default
    return val in {"1", "true", "yes", "y", "on"}

NAVER_CAFE_STAFF_COOKIE = _strip_env("NAVER_CAFE_STAFF_COOKIE")
NAVER_ID = _strip_env("NAVER_ID")
NAVER_PW = _strip_env("NAVER_PW")
ANTHROPIC_API_KEY = _strip_env("ANTHROPIC_API_KEY")

# === Claude 모델 설정 ===
CLAUDE_MODEL = _strip_env("CLAUDE_MODEL") or "claude-opus-4-6"
CLAUDE_MAX_TOKENS = 4096
CLAUDE_ENABLE_TOOL_USE = True
CLAUDE_TOOL_MAX_CONTEXT_CHARS = 12000
CLAUDE_ENABLE_THINKING = _env_bool("CLAUDE_ENABLE_THINKING", True)
CLAUDE_THINKING_BUDGET = int(_strip_env("CLAUDE_THINKING_BUDGET") or "2048")

# Optional Anthropic MCP connector settings
CLAUDE_MCP_ENABLED = _env_bool("CLAUDE_MCP_ENABLED", False)
CLAUDE_MCP_BETA_VERSION = _strip_env("CLAUDE_MCP_BETA_VERSION") or "mcp-client-2025-04-04"
CLAUDE_MCP_SERVER_NAME = _strip_env("CLAUDE_MCP_SERVER_NAME") or "knowledge_mcp"
CLAUDE_MCP_SERVER_URL = _strip_env("CLAUDE_MCP_SERVER_URL")
CLAUDE_MCP_AUTH_TOKEN = _strip_env("CLAUDE_MCP_AUTH_TOKEN")
CLAUDE_MCP_TOOL_ALLOWLIST = [
    x.strip() for x in _strip_env("CLAUDE_MCP_TOOL_ALLOWLIST").split(",") if x.strip()
]

LOCAL_MCP_AUTO_START = _env_bool("LOCAL_MCP_AUTO_START", True)
LOCAL_MCP_HOST = _strip_env("LOCAL_MCP_HOST") or "127.0.0.1"
LOCAL_MCP_PORT = int(_strip_env("LOCAL_MCP_PORT") or "8765")
LOCAL_MCP_PATH = _strip_env("LOCAL_MCP_PATH") or "/mcp"
if not CLAUDE_MCP_SERVER_URL:
    CLAUDE_MCP_SERVER_URL = f"http://{LOCAL_MCP_HOST}:{LOCAL_MCP_PORT}{LOCAL_MCP_PATH}"

# === 경로 설정 ===
STATE_FILE = BASE_DIR / "state.json"
PROMPT_FILE = BASE_DIR / "prompt.txt"
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "bot.log"

# === HTTP 헤더 ===
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": f"https://cafe.naver.com/{CAFE_NAME}",
}


def setup_logging() -> logging.Logger:
    """로깅 설정"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("cafe_bot")
    logger.setLevel(logging.DEBUG)

    # 파일 핸들러
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setLevel(logging.DEBUG)

    # 콘솔 핸들러
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)

    fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    fh.setFormatter(fmt)
    ch.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)

    return logger


def validate_config():
    """필수 설정값 검증"""
    errors = []
    if not NAVER_CAFE_STAFF_COOKIE:
        errors.append("NAVER_CAFE_STAFF_COOKIE가 .env에 설정되지 않았습니다.")
    if not ANTHROPIC_API_KEY:
        errors.append("ANTHROPIC_API_KEY가 .env에 설정되지 않았습니다.")
    if not NAVER_ID or not NAVER_PW:
        errors.append("NAVER_ID / NAVER_PW가 .env에 설정되지 않았습니다.")
    if errors:
        raise ValueError("설정 오류:\n" + "\n".join(f"  - {e}" for e in errors))
