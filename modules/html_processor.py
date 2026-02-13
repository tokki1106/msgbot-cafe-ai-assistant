"""
HTML Content Processor
네이버 카페 SmartEditor HTML을 깨끗한 텍스트로 변환
"""
import re
import html
import logging

from bs4 import BeautifulSoup

logger = logging.getLogger("cafe_bot.html_processor")


def clean_html(content_html: str) -> str:
    """
    네이버 카페 SmartEditor HTML을 깨끗한 평문 텍스트로 변환

    처리 과정:
    1. script, style 태그 제거
    2. <p>, <br> → 줄바꿈
    3. 모든 HTML 태그 제거
    4. HTML 엔터티 디코딩
    5. 특수 문자 정리 (zero-width space 등)
    6. 연속 공백/줄바꿈 정리

    Args:
        content_html: 네이버 카페 게시글의 contentHtml

    Returns:
        str: 깨끗한 한국어 텍스트
    """
    if not content_html:
        return ""

    try:
        soup = BeautifulSoup(content_html, "lxml")
    except Exception:
        soup = BeautifulSoup(content_html, "html.parser")

    # 1. script, style, 메타데이터 태그 제거
    for tag in soup.find_all(["script", "style", "noscript", "meta", "link"]):
        tag.decompose()

    # 2. SmartEditor 모듈 데이터 제거 (__se_module_data)
    for tag in soup.find_all("script", {"class": "__se_module_data"}):
        tag.decompose()

    # 3. OG Link (외부 링크 미리보기) 에서 URL만 추출
    for og_link in soup.find_all("div", class_="se-module-oglink"):
        link_tag = og_link.find("a")
        if link_tag and link_tag.get("href"):
            og_link.replace_with(f"\n[링크: {link_tag['href']}]\n")
        else:
            og_link.decompose()

    # 4. 이미지 태그에서 alt 텍스트 추출
    for img in soup.find_all("img"):
        alt = img.get("alt", "")
        if alt:
            img.replace_with(f"[이미지: {alt}]")
        else:
            img.replace_with("[이미지]")

    # 5. <p>, <br>, <div> → 줄바꿈
    for tag in soup.find_all(["p", "br", "div"]):
        tag.insert_before("\n")

    # 6. <a> 태그에서 텍스트 유지
    for a_tag in soup.find_all("a"):
        href = a_tag.get("href", "")
        text = a_tag.get_text(strip=True)
        if text and href and text != href:
            a_tag.replace_with(f"{text} ({href})")
        elif text:
            a_tag.replace_with(text)

    # 7. 모든 태그 제거, 텍스트만 추출
    text = soup.get_text()

    # 8. HTML 엔터티 디코딩
    text = html.unescape(text)

    # 9. 특수 문자 정리
    text = text.replace("\u200b", "")   # zero-width space
    text = text.replace("\u200c", "")   # zero-width non-joiner
    text = text.replace("\u200d", "")   # zero-width joiner
    text = text.replace("\ufeff", "")   # BOM
    text = text.replace("\xa0", " ")    # &nbsp;

    # 10. 연속 공백 정리 (줄 내부)
    text = re.sub(r"[^\S\n]+", " ", text)

    # 11. 연속 줄바꿈 정리 (최대 2개)
    text = re.sub(r"\n{3,}", "\n\n", text)

    # 12. 각 줄 앞뒤 공백 제거
    lines = [line.strip() for line in text.split("\n")]
    text = "\n".join(lines)

    # 13. 앞뒤 공백 제거
    text = text.strip()

    return text


def extract_text_brief(content_html: str, max_length: int = 500) -> str:
    """
    게시글 내용을 요약 길이로 추출 (목록 표시용)

    Args:
        content_html: HTML 콘텐츠
        max_length: 최대 문자 수

    Returns:
        str: 잘린 텍스트
    """
    text = clean_html(content_html)
    if len(text) > max_length:
        return text[:max_length] + "..."
    return text


def clean_comment_content(content: str) -> str:
    """
    댓글 내용 정리 (댓글은 보통 plain text이지만 HTML 엔터티가 있을 수 있음)

    Args:
        content: 댓글 텍스트

    Returns:
        str: 정리된 텍스트
    """
    if not content:
        return ""

    text = html.unescape(content)
    text = text.replace("\u200b", "")
    text = text.strip()
    return text
