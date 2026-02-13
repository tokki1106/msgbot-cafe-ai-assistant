"""
Comment Poster (Selenium)
Selenium을 사용하여 네이버 카페 게시글에 댓글 등록

로그인 방식: Chrome 프로필 재사용
  - chrome_profile/ 폴더에 브라우저 세션 저장
  - 최초 실행 시 사용자가 직접 로그인 (120초 대기)
  - 이후 실행 시 프로필의 쿠키로 자동 로그인
"""
import time
import logging
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    WebDriverException,
    UnexpectedAlertPresentException,
)

try:
    from webdriver_manager.chrome import ChromeDriverManager
except ImportError:
    ChromeDriverManager = None

import config

logger = logging.getLogger("cafe_bot.comment_poster")

# Chrome 프로필 저장 경로 (프로젝트 루트/chrome_profile)
CHROME_PROFILE_DIR = str(config.BASE_DIR / "chrome_profile")


class CommentPoster:
    """Selenium 기반 네이버 카페 댓글 등록기"""

    def __init__(self, headless: bool = False):
        """
        Args:
            headless: True면 브라우저 창을 띄우지 않음
                      ※ 첫 로그인 시에는 headless=False로 강제 전환
        """
        self.headless = headless
        self.driver = None
        self.is_logged_in = False
        self._cookie_str = None  # Selenium에서 추출한 쿠키 (API 호출용)

    @staticmethod
    def _cleanup_profile_locks():
        """Chrome 프로필 잠금 파일 제거 (이전 실행이 비정상 종료된 경우)"""
        profile_path = Path(CHROME_PROFILE_DIR)
        if not profile_path.exists():
            return
        lock_files = ["SingletonLock", "SingletonSocket", "SingletonCookie"]
        for name in lock_files:
            lock = profile_path / name
            if lock.exists():
                try:
                    lock.unlink()
                    logger.debug(f"잠금 파일 제거: {name}")
                except OSError:
                    pass

    def init_driver(self, force_visible: bool = False):
        """
        Chrome WebDriver 초기화

        Args:
            force_visible: True면 headless 설정 무시하고 브라우저 표시
        """
        # 이전 실행의 잠금 파일 정리
        self._cleanup_profile_locks()

        chrome_options = Options()

        # Chrome 프로필 재사용 (로그인 세션 유지)
        chrome_options.add_argument(f"--user-data-dir={CHROME_PROFILE_DIR}")
        chrome_options.add_argument("--profile-directory=BotProfile")

        use_headless = self.headless and not force_visible
        if use_headless:
            chrome_options.add_argument("--headless=new")

        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument(
            f"--user-agent={config.DEFAULT_HEADERS['User-Agent']}"
        )

        # 자동화 감지 방지
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option("useAutomationExtension", False)

        try:
            if ChromeDriverManager:
                service = Service(ChromeDriverManager().install())
                self.driver = webdriver.Chrome(service=service, options=chrome_options)
            else:
                self.driver = webdriver.Chrome(options=chrome_options)

            # webdriver 감지 방지
            self.driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {
                    "source": """
                        Object.defineProperty(navigator, 'webdriver', {
                            get: () => undefined
                        });
                    """
                },
            )

            self.driver.implicitly_wait(5)
            logger.info("Chrome WebDriver 초기화 완료")
            return True

        except WebDriverException as e:
            logger.error(f"Chrome WebDriver 초기화 실패: {e}")
            return False

    def _check_login_status(self) -> bool:
        """
        현재 브라우저가 네이버에 로그인되어 있는지 확인

        카페 페이지에서만 확인 (다른 페이지로 이동 안 함)
        """
        try:
            current = self.driver.current_url
            # 이미 카페에 있지 않으면 이동
            if config.CAFE_NAME not in current:
                self.driver.get(f"https://cafe.naver.com/{config.CAFE_NAME}")
                time.sleep(3)

            # CDP로 NID_AUT 쿠키 존재 확인 (가장 확실한 로그인 지표)
            cdp_cookies = self.driver.execute_cdp_cmd("Network.getAllCookies", {})
            cookie_names = {c["name"] for c in cdp_cookies.get("cookies", [])}

            if "NID_AUT" in cookie_names and "NID_SES" in cookie_names:
                logger.info("로그인 쿠키 확인됨 (NID_AUT, NID_SES)")
                return True

            logger.warning(f"로그인 쿠키 없음. 보유 쿠키: {sorted(cookie_names)}")
            return False

        except Exception as e:
            logger.debug(f"로그인 상태 확인 중 오류: {e}")
            return False

    def _wait_for_manual_login(self, timeout: int = 120) -> bool:
        """
        사용자가 브라우저에서 직접 로그인할 때까지 대기

        Args:
            timeout: 최대 대기 시간 (초)

        Returns:
            bool: 로그인 성공 여부
        """
        logger.info("=" * 60)
        logger.info("🔑 네이버 로그인이 필요합니다!")
        logger.info("   브라우저 창에서 직접 로그인해 주세요.")
        logger.info(f"   {timeout}초 이내에 로그인을 완료해 주세요.")
        logger.info("=" * 60)

        # 네이버 로그인 페이지로 이동
        self.driver.get("https://nid.naver.com/nidlogin.login")
        time.sleep(2)

        # 로그인 완료 대기
        for elapsed in range(timeout):
            current_url = self.driver.current_url

            # 로그인 페이지를 벗어났으면 성공 가능성
            if "nidlogin" not in current_url and "nid.naver.com" not in current_url:
                time.sleep(2)
                # CDP로 NID_AUT 쿠키 존재 확인 (페이지 이동 없이)
                try:
                    cdp_cookies = self.driver.execute_cdp_cmd("Network.getAllCookies", {})
                    names = {c["name"] for c in cdp_cookies.get("cookies", [])}
                    if "NID_AUT" in names:
                        logger.info("✅ 로그인 성공!")
                        return True
                except Exception:
                    pass

            if elapsed > 0 and elapsed % 30 == 0:
                logger.info(f"   로그인 대기 중... ({elapsed}/{timeout}초)")

            time.sleep(1)

        logger.error(f"❌ {timeout}초 내에 로그인이 완료되지 않았습니다.")
        return False

    def ensure_login(self) -> bool:
        """
        로그인 상태 보장 (메인 진입점)

        1. Chrome 프로필에 저장된 세션이 있으면 자동 로그인
        2. 없으면 브라우저를 열어 사용자가 직접 로그인하도록 유도
        """
        if not self.driver:
            if not self.init_driver():
                return False

        # 카페로 이동 (프로필 세션이 있으면 자동 로그인됨)
        logger.info("카페 접속 및 로그인 확인 중...")
        self.driver.get(f"https://cafe.naver.com/{config.CAFE_NAME}")
        time.sleep(3)

        if self._check_login_status():
            self.is_logged_in = True
            logger.info("✅ 로그인 확인됨")
            self._extract_cookies()
            return True

        # 프로필 세션이 만료됨 → 수동 로그인 필요
        logger.warning("로그인이 필요합니다.")

        # headless 모드였으면 브라우저를 다시 띄워야 함
        if self.headless:
            logger.info("수동 로그인을 위해 브라우저를 표시합니다...")
            self.close()
            self.headless = False
            if not self.init_driver(force_visible=True):
                return False

        # 수동 로그인 대기
        if self._wait_for_manual_login(timeout=120):
            self.is_logged_in = True
            # 로그인 후 카페로 이동
            self.driver.get(f"https://cafe.naver.com/{config.CAFE_NAME}")
            time.sleep(2)
            self._extract_cookies()
            return True

        return False

    def _extract_cookies(self):
        """
        CDP로 모든 네이버 도메인 쿠키를 한 번에 추출 (페이지 이동 없음)
        """
        if not self.driver:
            return
        try:
            # CDP: 브라우저의 모든 쿠키를 한 번에 가져옴
            cdp_result = self.driver.execute_cdp_cmd("Network.getAllCookies", {})
            all_browser_cookies = cdp_result.get("cookies", [])

            # naver.com 관련 쿠키만 필터링
            naver_cookies = [
                c for c in all_browser_cookies
                if "naver.com" in c.get("domain", "")
            ]

            self._cookie_str = "; ".join(
                f"{c['name']}={c['value']}" for c in naver_cookies
            )

            # 주요 인증 쿠키 확인 로그
            cookie_names = {c["name"] for c in naver_cookies}
            auth_keys = ["NID_AUT", "NID_SES", "NID_JKL", "nid_inf"]
            found_auth = [k for k in auth_keys if k in cookie_names]
            logger.info(
                f"쿠키 추출 완료: {len(naver_cookies)}개 "
                f"(인증: {', '.join(found_auth) or '없음'})"
            )

        except Exception as e:
            logger.error(f"쿠키 추출 실패: {e}")

    def get_cookie_str(self) -> str | None:
        """추출된 쿠키 문자열 반환 (API 호출용)"""
        return self._cookie_str

    def post_comment(self, article_id: int, comment_text: str) -> bool:
        """
        게시글에 댓글 등록

        Args:
            article_id: 게시글 ID
            comment_text: 댓글 내용

        Returns:
            bool: 성공 여부
        """
        if not self.is_logged_in:
            logger.error("로그인되지 않은 상태에서 댓글 등록 시도")
            return False

        article_url = (
            f"https://cafe.naver.com/ca-fe/cafes/{config.CAFE_ID}"
            f"/articles/{article_id}"
        )

        try:
            logger.info(f"게시글 접속 중: {article_url}")
            self.driver.get(article_url)
            time.sleep(3)

            # 댓글 입력란 찾기
            wait = WebDriverWait(self.driver, 15)

            textarea = wait.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "textarea.comment_inbox_text")
                )
            )

            # 댓글 입력란 클릭하여 포커스
            textarea.click()
            time.sleep(0.5)

            # 텍스트 입력
            textarea.clear()
            time.sleep(0.3)

            # JavaScript로 값 설정 후 이벤트 트리거
            escaped_text = comment_text.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
            self.driver.execute_script(
                f"""
                var textarea = document.querySelector('textarea.comment_inbox_text');
                var nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                    window.HTMLTextAreaElement.prototype, 'value'
                ).set;
                nativeInputValueSetter.call(textarea, '{escaped_text}');
                textarea.dispatchEvent(new Event('input', {{ bubbles: true }}));
                textarea.dispatchEvent(new Event('change', {{ bubbles: true }}));
                """
            )
            time.sleep(1)

            # 텍스트가 제대로 입력되었는지 확인
            current_value = textarea.get_attribute("value")
            if not current_value:
                # 대안: 직접 타이핑
                logger.warning("JS 입력 실패, ActionChains로 재시도")
                textarea.click()
                time.sleep(0.3)
                actions = ActionChains(self.driver)
                actions.send_keys(comment_text)
                actions.perform()
                time.sleep(1)

            # 등록 버튼 클릭
            register_btn = wait.until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, "a.btn_register, button.btn_register")
                )
            )
            register_btn.click()
            time.sleep(2)

            # alert 팝업 확인 (글자수 초과 등)
            try:
                alert = self.driver.switch_to.alert
                alert_text = alert.text
                logger.warning(f"Alert 감지: {alert_text} (articleId={article_id})")
                alert.accept()
                time.sleep(0.5)

                # 글자수 초과 alert인 경우 → 텍스트 잘라서 재시도
                if "자까지" in alert_text or "글자" in alert_text:
                    logger.warning("글자수 초과 — 텍스트를 강제 절삭하여 재시도합니다.")
                    truncated = comment_text[:2800]
                    # 마지막 완전한 줄에서 자르기
                    last_nl = truncated.rfind("\n")
                    if last_nl > 2000:
                        truncated = truncated[:last_nl]
                    truncated += "\n\n(답변이 길어 일부 생략되었습니다.)"

                    # textarea를 다시 비우고 입력
                    textarea = self.driver.find_element(
                        By.CSS_SELECTOR, "textarea.comment_inbox_text"
                    )
                    textarea.click()
                    time.sleep(0.3)
                    escaped = truncated.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
                    self.driver.execute_script(
                        f"""
                        var ta = document.querySelector('textarea.comment_inbox_text');
                        var setter = Object.getOwnPropertyDescriptor(
                            window.HTMLTextAreaElement.prototype, 'value'
                        ).set;
                        setter.call(ta, '{escaped}');
                        ta.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        ta.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        """
                    )
                    time.sleep(1)

                    register_btn = self.driver.find_element(
                        By.CSS_SELECTOR, "a.btn_register, button.btn_register"
                    )
                    register_btn.click()
                    time.sleep(2)

                    # 재시도 후에도 alert 뜨는지 확인
                    try:
                        alert2 = self.driver.switch_to.alert
                        logger.error(f"재시도에도 Alert 발생: {alert2.text}")
                        alert2.accept()
                        return False
                    except Exception:
                        pass  # alert 없으면 성공

                    logger.info(f"댓글 등록 완료 (절삭 후): articleId={article_id}")
                    return True
                else:
                    # 글자수 외 다른 alert → 실패 처리
                    return False

            except UnexpectedAlertPresentException as ae:
                # 이미 alert이 떠 있는 상태에서 다른 조작 시도 시
                logger.warning(f"UnexpectedAlert: {ae}")
                try:
                    self.driver.switch_to.alert.accept()
                except Exception:
                    pass
                return False
            except Exception:
                pass  # alert 없음 → 정상 등록

            time.sleep(1)
            logger.info(f"댓글 등록 완료: articleId={article_id}")
            return True

        except UnexpectedAlertPresentException as ae:
            logger.error(f"댓글 등록 중 Alert 발생 (articleId={article_id}): {ae}")
            try:
                self.driver.switch_to.alert.accept()
            except Exception:
                pass
            return False
        except TimeoutException:
            logger.error(f"댓글 등록 타임아웃: articleId={article_id}")
            return False
        except Exception as e:
            logger.error(f"댓글 등록 실패 (articleId={article_id}): {e}")
            return False

    def close(self):
        """브라우저 종료"""
        if self.driver:
            try:
                self.driver.quit()
                logger.info("브라우저 종료 완료")
            except Exception as e:
                logger.error(f"브라우저 종료 중 오류: {e}")
            finally:
                self.driver = None
                self.is_logged_in = False
