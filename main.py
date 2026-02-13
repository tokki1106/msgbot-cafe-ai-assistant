"""
Naver Cafe Auto-Reply Bot — Main Entry Point
네이버 카페 질문 게시판 자동 답변 봇

사용법:
    python main.py                  # 봇 실행
    python main.py --dry-run        # 댓글 등록 없이 테스트
    python main.py --headless       # 브라우저 창 숨김
"""
import sys
import json
import time
import signal
import logging
import argparse
from datetime import datetime

import config
from modules.api_client import get_article_detail, scan_new_articles, test_api_connection
from modules.html_processor import clean_html, clean_comment_content
from modules.reply_generator import ReplyGenerator
from modules.comment_poster import CommentPoster
from modules.local_mcp_manager import LocalMCPServerManager
from modules.priority_knowledge import PriorityKnowledge

logger = logging.getLogger("cafe_bot.main")

# Graceful shutdown 플래그
_shutdown = False

# 댓글 모니터링 설정
WATCH_MAX_CHECKS = 30       # 최대 모니터링 횟수 (30분 ~ 30회)
WATCH_CHECK_INTERVAL = 5    # 매 N번째 폴링 사이클마다 댓글 확인



def signal_handler(signum, frame):
    global _shutdown
    logger.info("종료 신호를 받았습니다. 안전하게 종료합니다...")
    _shutdown = True


# === 상태 관리 ===

def load_state() -> dict:
    """state.json에서 상태 로드"""
    try:
        if config.STATE_FILE.exists():
            with open(config.STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"상태 파일 로드 실패: {e}")

    return {
        "last_article_id": 0,
        "processed_articles": [],
        "watched_articles": {},
        "last_run": None,
    }


def save_state(state: dict):
    """state.json에 상태 저장"""
    state["last_run"] = datetime.now().isoformat()
    if len(state.get("processed_articles", [])) > 200:
        state["processed_articles"] = state["processed_articles"][-200:]

    try:
        with open(config.STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except IOError as e:
        logger.error(f"상태 파일 저장 실패: {e}")


# === 게시글 필터링 ===

def check_bot_already_replied(article_result: dict) -> bool:
    """봇이 이미 댓글을 달았는지 확인"""
    comments = article_result.get("comments", {}).get("items", [])
    for c in comments:
        writer_nick = c.get("writer", {}).get("nick", "")
        if writer_nick == config.BOT_NICK:
            return True
    return False


def _get_bot_comment_ids(article_result: dict) -> list[int]:
    """봇이 달은 댓글 ID 목록 반환"""
    bot_ids = []
    comments = article_result.get("comments", {}).get("items", [])
    for c in comments:
        writer_nick = c.get("writer", {}).get("nick", "")
        if writer_nick == config.BOT_NICK:
            comment_id = c.get("id", 0)
            if comment_id:
                bot_ids.append(comment_id)
    return bot_ids


def is_eligible_article(article_result: dict) -> bool:
    """
    답변 대상 게시글인지 확인

    조건:
    1. memberLevel 필터 (None이면 모든 멤버)
    2. 봇이 아직 답변하지 않았음
    3. 댓글 작성이 가능한 게시글
    """
    article = article_result.get("article", {})

    member_level = article.get("writer", {}).get("memberLevel", 0)
    if config.ALLOWED_MEMBER_LEVELS is not None:
        if member_level not in config.ALLOWED_MEMBER_LEVELS:
            logger.debug(
                f"멤버 레벨 미달: {member_level} "
                f"(닉네임: {article.get('writer', {}).get('nick', '?')})"
            )
            return False

    if not article.get("isReadable", True):
        return False

    if not article.get("isWriteComment", True):
        return False

    if check_bot_already_replied(article_result):
        logger.debug("이미 답변한 게시글")
        return False

    return True


# === 게시글 처리 ===

def process_article(
    article_id: int,
    priority_knowledge: PriorityKnowledge,
    generator: ReplyGenerator,
    poster: CommentPoster | None,
    dry_run: bool = False,
    cookie: str = None,
) -> bool:
    """단일 게시글 처리 (조회 → 필터 → 지식 검색 → 답변 생성 → 댓글 등록)"""
    logger.info(f"=== 게시글 #{article_id} 처리 시작 ===")

    # 1. 게시글 상세 조회
    result = get_article_detail(article_id, menu_id=config.MENU_ID_MONITOR, cookie=cookie)
    if not result:
        logger.warning(f"게시글 #{article_id} 조회 실패")
        return False

    article = result.get("article", {})
    subject = article.get("subject", "")
    writer_nick = article.get("writer", {}).get("nick", "?")
    member_level = article.get("writer", {}).get("memberLevel", 0)

    logger.info(f"게시글: [{subject}] by {writer_nick} (level={member_level})")

    # 2. 답변 대상 확인
    if not is_eligible_article(result):
        logger.info(f"게시글 #{article_id}: 답변 대상이 아닙니다.")
        return False

    # 3. HTML 전처리
    content_html = article.get("contentHtml", "")
    content_text = clean_html(content_html)
    logger.debug(f"전처리된 내용 ({len(content_text)} chars)")

    # 4. 컨텍스트 검색 (knowledge 소스)
    reply = generator.generate_reply(subject, content_text)
    if not reply:
        logger.error(f"게시글 #{article_id}: 답변 생성 실패")
        return False

    logger.info(f"생성된 답변 ({len(reply)} chars):\n{reply[:300]}...")

    # 6. 댓글 등록
    if dry_run:
        logger.info("[DRY-RUN] 댓글 등록 건너뜀")
        return True

    if poster is None:
        logger.warning("CommentPoster가 초기화되지 않았습니다.")
        return False

    success = poster.post_comment(article_id, reply)
    if success:
        logger.info(f"게시글 #{article_id}: 댓글 등록 성공!")
    else:
        logger.error(f"게시글 #{article_id}: 댓글 등록 실패")

    return success


# === 댓글 모니터링 ===

def add_to_watch(state: dict, article_id: int, writer_nick: str, subject: str, comment_count: int):
    """봇이 답변한 게시글을 모니터링 목록에 추가"""
    if "watched_articles" not in state:
        state["watched_articles"] = {}

    state["watched_articles"][str(article_id)] = {
        "writer_nick": writer_nick,
        "subject": subject,
        "last_comment_count": comment_count,
        "checks_remaining": WATCH_MAX_CHECKS,
        "added_at": datetime.now().isoformat(),
    }
    logger.info(
        f"모니터링 등록: #{article_id} [{subject}] "
        f"by {writer_nick} (댓글 {comment_count}개)"
    )


def check_watched_articles(
    state: dict,
    priority_knowledge: PriorityKnowledge,
    generator: ReplyGenerator,
    poster: CommentPoster | None,
    dry_run: bool,
    cookie: str,
):
    """
    모니터링 중인 게시글의 새 댓글 확인 및 응답

    게시글 작성자가 봇의 답변 이후에 새 댓글을 달면,
    해당 댓글에 대해 후속 답변을 생성하여 등록.
    """
    watched = state.get("watched_articles", {})
    if not watched:
        return

    logger.info(f"\n--- 댓글 모니터링 ({len(watched)}개 게시글) ---")
    to_remove = []

    for article_id_str, watch_info in list(watched.items()):
        if _shutdown:
            break

        article_id = int(article_id_str)
        writer_nick = watch_info["writer_nick"]
        subject = watch_info.get("subject", "?")
        last_count = watch_info.get("last_comment_count", 0)
        checks_left = watch_info.get("checks_remaining", 0)

        # 모니터링 횟수 소진
        if checks_left <= 0:
            logger.debug(f"모니터링 종료: #{article_id} (횟수 소진)")
            to_remove.append(article_id_str)
            continue

        # 게시글 상세 조회
        result = get_article_detail(article_id, cookie=cookie)
        if not result:
            watch_info["checks_remaining"] = checks_left - 1
            continue

        comments = result.get("comments", {}).get("items", [])
        current_count = len(comments)

        # 댓글 수 변화 없음
        if current_count <= last_count:
            watch_info["checks_remaining"] = checks_left - 1
            continue

        logger.info(
            f"#{article_id}: 새 댓글 감지! "
            f"({last_count} → {current_count}개)"
        )

        # 봇의 마지막 댓글 이후의 작성자 댓글 찾기
        bot_comment_ids = _get_bot_comment_ids(result)
        if not bot_comment_ids:
            # 봇 댓글이 없으면 (삭제되었을 수 있음) 모니터링 중단
            to_remove.append(article_id_str)
            continue

        max_bot_comment_id = max(bot_comment_ids)

        # 봇 댓글 이후에 작성된, 게시글 작성자의 새 댓글 찾기
        new_author_comments = []
        for c in comments:
            c_nick = c.get("writer", {}).get("nick", "")
            c_id = c.get("id", 0)
            # 게시글 작성자의 댓글 & 봇 댓글 이후에 작성된 것
            if c_nick == writer_nick and c_id > max_bot_comment_id:
                c_content = clean_comment_content(c.get("content", ""))
                if c_content:
                    new_author_comments.append({
                        "id": c_id,
                        "nick": c_nick,
                        "content": c_content,
                    })

        if not new_author_comments:
            # 작성자 댓글이 아닌 경우 (다른 유저 댓글) 카운트만 업데이트
            watch_info["last_comment_count"] = current_count
            watch_info["checks_remaining"] = checks_left - 1
            continue

        logger.info(
            f"#{article_id}: 작성자 '{writer_nick}'의 새 댓글 "
            f"{len(new_author_comments)}개 발견"
        )

        # 대화 이력 구성 (봇 + 작성자 댓글 시간순)
        conversation_history = []
        for c in comments:
            c_nick = c.get("writer", {}).get("nick", "")
            c_content = clean_comment_content(c.get("content", ""))
            if not c_content:
                continue
            is_bot = (c_nick == config.BOT_NICK)
            is_author = (c_nick == writer_nick)
            if is_bot or is_author:
                conversation_history.append({
                    "nick": c_nick,
                    "content": c_content,
                    "is_bot": is_bot,
                })

        # 가장 최신 작성자 댓글에 대해 응답 생성
        latest_comment = new_author_comments[-1]

        # 원글 내용
        article = result.get("article", {})
        content_html = article.get("contentHtml", "")
        content_text = clean_html(content_html)

        # 후속 답변 생성
        reply = generator.generate_followup_reply(
            subject=subject,
            content=content_text,
            conversation_history=conversation_history,
            new_comment=latest_comment["content"],
            commenter_nick=writer_nick,
        )

        if not reply:
            logger.error(f"#{article_id}: 후속 답변 생성 실패")
            watch_info["last_comment_count"] = current_count
            watch_info["checks_remaining"] = checks_left - 1
            continue

        logger.info(f"후속 답변 ({len(reply)} chars):\n{reply[:300]}...")

        # 댓글 등록
        if dry_run:
            logger.info("[DRY-RUN] 후속 댓글 등록 건너뜀")
        elif poster:
            success = poster.post_comment(article_id, reply)
            if success:
                logger.info(f"#{article_id}: 후속 댓글 등록 성공!")
            else:
                logger.error(f"#{article_id}: 후속 댓글 등록 실패")
        else:
            logger.warning("CommentPoster가 초기화되지 않았습니다.")

        # 상태 업데이트 — 새 댓글 등록 후 카운트 갱신
        watch_info["last_comment_count"] = current_count + (1 if not dry_run else 0)
        watch_info["checks_remaining"] = WATCH_MAX_CHECKS  # 응답했으면 카운터 리셋

        time.sleep(3)

    # 만료된 항목 제거
    for key in to_remove:
        del watched[key]
        logger.debug(f"모니터링 해제: #{key}")

    save_state(state)


# === 메인 루프 ===

def run_bot(dry_run: bool = False, headless: bool = False):
    """메인 봇 루프 실행"""
    logger.info("=" * 60)
    logger.info("네이버 카페 자동 답변 봇 시작")
    logger.info(f"  카페: {config.CAFE_NAME} (ID: {config.CAFE_ID})")
    logger.info(f"  모니터링 게시판: menuId={config.MENU_ID_MONITOR}")
    logger.info(f"  폴링 주기: {config.POLL_INTERVAL}초")
    logger.info(f"  방식: 순차 ID 스캔 (상세 API 기반)")
    logger.info(f"  댓글 모니터링: 활성 (최대 {WATCH_MAX_CHECKS}회 체크)")
    logger.info(f"  모드: {'DRY-RUN' if dry_run else 'LIVE'}")
    logger.info("=" * 60)

    # 컴포넌트 초기화
    priority_knowledge = PriorityKnowledge(
        source_path=config.PRIORITY_KNOWLEDGE_PATH,
        instruction_file=getattr(config, "PRIORITY_INSTRUCTION_FILE", "instruction.md"),
    )
    priority_knowledge.load()
    mcp_manager = LocalMCPServerManager()
    mcp_ready = mcp_manager.start_if_needed()
    if mcp_manager.should_start() and not mcp_ready:
        logger.warning("Local MCP server unavailable; fallback to non-MCP mode.")
        config.CLAUDE_MCP_ENABLED = False

    def _lookup_context(query: str, max_chars: int = 12000) -> str:
        ctx = priority_knowledge.retrieve_context(
            query=query,
            top_k=int(getattr(config, "PRIORITY_CONTEXT_TOP_K", 4)),
        )
        return (ctx or "")[:max_chars]

    generator = ReplyGenerator(
        system_prompt_override=priority_knowledge.get_instruction_prompt(),
        context_lookup=_lookup_context,
    )

    poster = None
    api_cookie = config.NAVER_CAFE_STAFF_COOKIE  # 기본값: .env 쿠키

    # dry-run이든 아니든 Selenium으로 신선한 쿠키 확보
    # (dry-run에서는 댓글 등록만 안 하고, 로그인 + 쿠키 추출은 동일하게 수행)
    cookie_poster = CommentPoster(headless=headless)
    if not cookie_poster.ensure_login():
        logger.error("네이버 로그인 실패. 봇을 종료합니다.")
        cookie_poster.close()
        mcp_manager.stop()
        return

    fresh_cookie = cookie_poster.get_cookie_str()
    if fresh_cookie:
        api_cookie = fresh_cookie
        logger.info("✅ Selenium 쿠키를 API 호출에 사용합니다.")
    else:
        logger.warning("Selenium 쿠키 추출 실패. .env 쿠키를 사용합니다.")

    if dry_run:
        # dry-run: 쿠키만 쓰고 브라우저는 닫음 (댓글 안 씀)
        cookie_poster.close()
    else:
        # live: 댓글 등록용으로 브라우저 유지
        poster = cookie_poster

    # API 연결 테스트 (쿠키 유효성 확인 — dry-run에서도 실행)
    if not test_api_connection(api_cookie):
        logger.error("❌ API 연결 테스트 실패! 쿠키가 유효하지 않습니다.")
        logger.error("   브라우저에서 로그인하거나 .env의 쿠키를 갱신하세요.")
        if poster:
            poster.close()
        mcp_manager.stop()
        return

    state = load_state()

    # watched_articles가 없으면 초기화
    if "watched_articles" not in state:
        state["watched_articles"] = {}

    # 최초 실행 시 last_article_id가 0이면, 현재 최신 글 근처부터 시작
    if state.get("last_article_id", 0) == 0:
        logger.info("최초 실행: 최신 게시글 ID를 찾습니다...")
        state["last_article_id"] = 53285  # 사용자가 알려준 최근 ID
        save_state(state)
        logger.info(f"시작 ID 설정: {state['last_article_id']}")

    logger.info(f"마지막 처리 게시글 ID: {state['last_article_id']}")
    logger.info(f"모니터링 중인 게시글: {len(state.get('watched_articles', {}))}개")

    poll_count = 0  # 폴링 사이클 카운터

    try:
        while not _shutdown:
            try:
                poll_count += 1
                logger.info(
                    f"\n--- 폴링 #{poll_count} "
                    f"(#{state['last_article_id']+1}~, "
                    f"{datetime.now().strftime('%H:%M:%S')}) ---"
                )

                # ── 1. 새 게시글 스캔 ──
                processed_set = set(state.get("processed_articles", []))
                new_article_ids = scan_new_articles(
                    last_id=state["last_article_id"],
                    menu_id=None,
                    cookie=api_cookie,
                    max_scan=100,
                )

                new_article_ids = [
                    aid for aid in new_article_ids
                    if aid not in processed_set
                ]

                if not new_article_ids:
                    logger.info("새 게시글 없음")
                else:
                    logger.info(f"새 게시글 {len(new_article_ids)}개 발견: {new_article_ids}")

                    for article_id in new_article_ids:
                        if _shutdown:
                            break

                        result = get_article_detail(article_id, cookie=api_cookie)
                        if result:
                            article_menu = result.get("article", {}).get("menu", {}).get("id", 0)
                            if article_menu == config.MENU_ID_MONITOR:
                                article = result.get("article", {})
                                a_writer = article.get("writer", {}).get("nick", "?")
                                a_subject = article.get("subject", "?")

                                success = process_article(
                                    article_id=article_id,
                                    priority_knowledge=priority_knowledge,
                                    generator=generator,
                                    poster=poster,
                                    dry_run=dry_run,
                                    cookie=api_cookie,
                                )

                                # 답변 성공 시 모니터링 등록
                                if success:
                                    # 현재 댓글 수 조회 (방금 등록한 것 포함)
                                    fresh = get_article_detail(article_id, cookie=api_cookie)
                                    comment_count = len(
                                        fresh.get("comments", {}).get("items", [])
                                    ) if fresh else 0
                                    add_to_watch(
                                        state, article_id,
                                        a_writer, a_subject, comment_count,
                                    )
                            else:
                                logger.debug(
                                    f"게시글 #{article_id}: "
                                    f"menuId={article_menu} (모니터링 대상 아님)"
                                )

                        state["processed_articles"].append(article_id)
                        if article_id > state.get("last_article_id", 0):
                            state["last_article_id"] = article_id
                        save_state(state)

                        time.sleep(3)

                if new_article_ids:
                    max_found = max(new_article_ids)
                    if max_found > state["last_article_id"]:
                        state["last_article_id"] = max_found
                        save_state(state)

                # ── 2. 댓글 모니터링 (매 N번째 사이클) ──
                if poll_count % WATCH_CHECK_INTERVAL == 0:
                    check_watched_articles(
                        state=state,
                        priority_knowledge=priority_knowledge,
                        generator=generator,
                        poster=poster,
                        dry_run=dry_run,
                        cookie=api_cookie,
                    )

            except KeyboardInterrupt:
                raise
            except Exception as e:
                logger.error(f"폴링 루프 중 오류: {e}", exc_info=True)

            if not _shutdown:
                time.sleep(config.POLL_INTERVAL)

    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt — 봇 종료 중...")
    finally:
        if poster:
            poster.close()
        mcp_manager.stop()
        save_state(state)
        logger.info("봇이 정상 종료되었습니다.")


# === 강제 재처리 ===

def reprocess_articles(article_ids: list[int], dry_run: bool = False, headless: bool = False):
    """특정 게시글을 강제로 재처리 (processed_articles 무시)"""
    logger.info("=" * 60)
    logger.info(f"강제 재처리: {article_ids}")
    logger.info(f"모드: {'DRY-RUN' if dry_run else 'LIVE'}")
    logger.info("=" * 60)

    priority_knowledge = PriorityKnowledge(
        source_path=config.PRIORITY_KNOWLEDGE_PATH,
        instruction_file=getattr(config, "PRIORITY_INSTRUCTION_FILE", "instruction.md"),
    )
    priority_knowledge.load()
    mcp_manager = LocalMCPServerManager()
    mcp_ready = mcp_manager.start_if_needed()
    if mcp_manager.should_start() and not mcp_ready:
        logger.warning("Local MCP server unavailable; fallback to non-MCP mode.")
        config.CLAUDE_MCP_ENABLED = False
    def _lookup_context(query: str, max_chars: int = 12000) -> str:
        ctx = priority_knowledge.retrieve_context(
            query=query,
            top_k=int(getattr(config, "PRIORITY_CONTEXT_TOP_K", 4)),
        )
        return (ctx or "")[:max_chars]

    generator = ReplyGenerator(
        system_prompt_override=priority_knowledge.get_instruction_prompt(),
        context_lookup=_lookup_context,
    )

    poster = None
    api_cookie = config.NAVER_CAFE_STAFF_COOKIE

    cookie_poster = CommentPoster(headless=headless)
    if not cookie_poster.ensure_login():
        logger.error("네이버 로그인 실패.")
        cookie_poster.close()
        mcp_manager.stop()
        return

    fresh_cookie = cookie_poster.get_cookie_str()
    if fresh_cookie:
        api_cookie = fresh_cookie
        logger.info("✅ Selenium 쿠키를 API 호출에 사용합니다.")

    if dry_run:
        cookie_poster.close()
    else:
        poster = cookie_poster

    if not test_api_connection(api_cookie):
        logger.error("❌ API 연결 테스트 실패!")
        if poster:
            poster.close()
        mcp_manager.stop()
        return

    state = load_state()

    for article_id in article_ids:
        logger.info(f"\n=== #{article_id} 강제 재처리 ===")

        # processed_articles에서 제거
        if article_id in state.get("processed_articles", []):
            state["processed_articles"].remove(article_id)
            logger.info(f"  processed_articles에서 제거")

        result = get_article_detail(article_id, cookie=api_cookie)
        if not result:
            logger.error(f"  #{article_id} 조회 실패")
            continue

        article = result.get("article", {})
        a_subject = article.get("subject", "?")
        a_writer = article.get("writer", {}).get("nick", "?")
        a_menu = article.get("menu", {}).get("id", 0)

        logger.info(f"  [{a_subject}] by {a_writer} (menuId={a_menu})")

        # 봇이 이미 댓글 달았는지 확인
        if check_bot_already_replied(result):
            logger.warning(f"  #{article_id}: 봇이 이미 답변한 글 — 건너뜀")
            continue

        # HTML 전처리
        content_html = article.get("contentHtml", "")
        content_text = clean_html(content_html)

        reply = generator.generate_reply(a_subject, content_text)
        if not reply:
            logger.error(f"  #{article_id}: 답변 생성 실패")
            continue

        logger.info(f"  생성된 답변 ({len(reply)} chars):\n{reply[:300]}...")

        # 댓글 등록
        if dry_run:
            logger.info("  [DRY-RUN] 댓글 등록 건너뜀")
        elif poster:
            success = poster.post_comment(article_id, reply)
            if success:
                logger.info(f"  ✅ #{article_id} 댓글 등록 성공!")
                # 모니터링 등록
                fresh = get_article_detail(article_id, cookie=api_cookie)
                cc = len(fresh.get("comments", {}).get("items", [])) if fresh else 0
                add_to_watch(state, article_id, a_writer, a_subject, cc)
            else:
                logger.error(f"  #{article_id} 댓글 등록 실패")

        state["processed_articles"].append(article_id)
        save_state(state)
        time.sleep(3)

    if poster:
        poster.close()
    save_state(state)
    mcp_manager.stop()
    logger.info("\n강제 재처리 완료!")


# === CLI ===

def parse_args():
    parser = argparse.ArgumentParser(
        description="네이버 카페 자동 답변 봇",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  python main.py                          # 봇 실행 (브라우저 표시)
  python main.py --headless               # 봇 실행 (브라우저 숨김)
  python main.py --dry-run                # 테스트 모드 (댓글 등록 안 함)
  python main.py --start-id 53300         # 특정 ID부터 모니터링 시작
  python main.py --reprocess 53297 53299  # 특정 게시글 강제 재처리
        """,
    )
    parser.add_argument("--dry-run", action="store_true", help="테스트 모드")
    parser.add_argument("--headless", action="store_true", help="브라우저 숨김")
    parser.add_argument("--start-id", type=int, help="모니터링 시작 게시글 ID 지정")
    parser.add_argument("--reprocess", type=int, nargs="+", help="특정 게시글 ID 강제 재처리")
    return parser.parse_args()


def main():
    log = config.setup_logging()
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    args = parse_args()

    # 설정 검증
    try:
        config.validate_config()
    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)

    # 특정 게시글 강제 재처리 모드
    if args.reprocess:
        logger.info(f"강제 재처리 모드: {args.reprocess}")
        reprocess_articles(
            article_ids=args.reprocess,
            dry_run=args.dry_run,
            headless=args.headless,
        )
        return

    # 시작 ID 지정
    if args.start_id:
        state = load_state()
        state["last_article_id"] = args.start_id
        save_state(state)
        logger.info(f"시작 ID 설정: {args.start_id}")

    # 봇 실행
    run_bot(dry_run=args.dry_run, headless=args.headless)


if __name__ == "__main__":
    main()
