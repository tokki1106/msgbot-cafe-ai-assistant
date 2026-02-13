"""
Naver Cafe API Client
네이버 카페 게시글 상세 조회 API 래퍼 + 순차 ID 기반 새 게시글 탐색
"""
import time
import logging
import requests

import config

logger = logging.getLogger("cafe_bot.api_client")


def _build_headers(cookie: str) -> dict:
    """API 요청용 헤더 생성"""
    headers = dict(config.DEFAULT_HEADERS)
    headers["Cookie"] = cookie
    return headers


def get_article_detail(
    article_id: int,
    menu_id: int = 0,
    cookie: str = None,
) -> dict | None:
    """
    게시글 상세 정보 조회 (gw/v4 API)

    Args:
        article_id: 게시글 ID
        menu_id: 게시판 ID (0이면 menuId 파라미터 생략)
        cookie: 인증 쿠키

    Returns:
        dict: result 객체 (article, comments 포함)
        None: 실패 시 (존재하지 않는 글 포함)
    """
    if cookie is None:
        cookie = config.NAVER_CAFE_STAFF_COOKIE

    # menuId=0 이면 menuId 파라미터 없이 호출 (범용 조회)
    if menu_id == 0:
        url = (
            f"https://article.cafe.naver.com/gw/v4/cafes/{config.CAFE_ID}"
            f"/articles/{article_id}"
            f"?query=&boardType=L&useCafeId=true&requestFrom=A"
        )
    else:
        url = config.ARTICLE_DETAIL_URL.format(
            cafe_id=config.CAFE_ID,
            article_id=article_id,
            menu_id=menu_id,
        )

    headers = _build_headers(cookie)

    try:
        resp = requests.get(url, headers=headers, timeout=15)

        # 404 또는 존재하지 않는 글
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            logger.warning(f"API HTTP {resp.status_code}: articleId={article_id}")
            # 응답 내용도 일부 출력 (디버깅용)
            logger.debug(f"응답 내용: {resp.text[:200]}")
            return None

        data = resp.json()
        result = data.get("result")

        if result:
            return result
        else:
            # result가 없으면 에러 메시지 확인
            error_msg = data.get("errorMessage") or data.get("message") or ""
            if error_msg:
                logger.warning(
                    f"API 응답에 result 없음 (articleId={article_id}): {error_msg}"
                )
            return None

    except requests.RequestException as e:
        logger.error(f"게시글 상세 조회 실패 (articleId={article_id}): {e}")
        return None


def test_api_connection(cookie: str, test_article_id: int = 53288) -> bool:
    """
    API 연결 및 쿠키 유효성 테스트

    알려진 게시글 ID로 API를 호출하여 쿠키가 유효한지 확인

    Returns:
        bool: API 정상 작동 여부
    """
    logger.info(f"API 연결 테스트 (articleId={test_article_id})...")

    if not cookie:
        logger.error("쿠키가 비어있습니다!")
        return False

    # menuId 없이 호출
    url = (
        f"https://article.cafe.naver.com/gw/v4/cafes/{config.CAFE_ID}"
        f"/articles/{test_article_id}"
        f"?query=&boardType=L&useCafeId=true&requestFrom=A"
    )
    headers = _build_headers(cookie)

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        logger.info(f"  HTTP 상태: {resp.status_code}")

        if resp.status_code != 200:
            logger.error(f"  API 응답 실패: HTTP {resp.status_code}")
            logger.error(f"  응답 내용: {resp.text[:300]}")
            return False

        data = resp.json()
        result = data.get("result")

        if not result:
            error_msg = data.get("errorMessage") or data.get("message") or "알 수 없는 오류"
            logger.error(f"  API 응답에 result 없음: {error_msg}")
            logger.error(f"  전체 응답: {str(data)[:300]}")
            return False

        article = result.get("article", {})
        subject = article.get("subject", "?")
        writer = article.get("writer", {}).get("nick", "?")
        menu_name = article.get("menu", {}).get("name", "?")
        menu_id = article.get("menu", {}).get("id", "?")

        logger.info(f"  ✅ API 정상! [{subject}] by {writer} (게시판: {menu_name}, menuId={menu_id})")
        return True

    except requests.RequestException as e:
        logger.error(f"  API 연결 실패: {e}")
        return False


def scan_new_articles(
    last_id: int,
    menu_id: int = None,
    cookie: str = None,
    max_scan: int = 50,
) -> list[int]:
    """
    순차 ID 스캔으로 새 게시글 탐색

    last_id+1부터 순차적으로 상세 API를 호출하여
    존재하는 게시글 ID를 반환한다.

    Args:
        last_id: 마지막으로 처리한 게시글 ID
        menu_id: 특정 게시판만 필터 (None이면 전체)
        cookie: 인증 쿠키
        max_scan: 최대 스캔 범위

    Returns:
        list[int]: 새로 발견된 게시글 ID 목록
    """
    if cookie is None:
        cookie = config.NAVER_CAFE_STAFF_COOKIE

    new_ids = []
    consecutive_misses = 0
    max_consecutive_misses = 50  # 50개 연속 없으면 중단 (삭제글/비공개글 감안)

    for offset in range(1, max_scan + 1):
        article_id = last_id + offset

        # menuId=0 → menuId 파라미터 생략하여 범용 조회
        result = get_article_detail(article_id, menu_id=0, cookie=cookie)

        if result:
            article = result.get("article", {})
            article_menu_id = article.get("menu", {}).get("id", 0)

            # menu_id 필터 적용
            if menu_id is None or article_menu_id == menu_id:
                new_ids.append(article_id)
                logger.info(f"새 게시글 발견: #{article_id} (menuId={article_menu_id})")
            else:
                logger.debug(f"다른 게시판 글 건너뜀: #{article_id} (menuId={article_menu_id})")

            consecutive_misses = 0
        else:
            consecutive_misses += 1
            if consecutive_misses >= max_consecutive_misses:
                logger.info(f"#{article_id}: {max_consecutive_misses}개 연속 미발견 — 스캔 중단")
                break

        time.sleep(0.3)

    return new_ids


def get_all_comments(
    article_id: int,
    cookie: str = None,
) -> list[dict]:
    """게시글의 모든 댓글 조회"""
    if cookie is None:
        cookie = config.NAVER_CAFE_STAFF_COOKIE

    result = get_article_detail(article_id, cookie=cookie)
    if not result:
        return []

    comments_data = result.get("comments", {})
    all_comments = list(comments_data.get("items", []))
    logger.debug(f"댓글 조회 완료: articleId={article_id}, count={len(all_comments)}")
    return all_comments
