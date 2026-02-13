# 메신저봇R 질의응답 AI 도우미

네이버 카페 **[카카오톡 봇 커뮤니티(nameyee)](https://cafe.naver.com/nameyee)** 질문 게시판에 올라오는 [MessengerBot R](https://msgbot.app) 관련 기술 질문에 **Anthropic Claude AI**가 자동으로 답변을 작성하는 봇입니다.

## 주요 기능

- **질문 게시판 자동 모니터링** — 새 게시글을 순차 ID 스캔 방식으로 탐지
- **AI 답변 생성** — Claude API + 로컬 지식 기반 기술 답변 자동 작성
- **댓글 자동 등록** — Selenium을 이용한 네이버 카페 댓글 등록
- **후속 대화 지원** — 봇이 답변한 글에 작성자가 추가 댓글을 달면 후속 답변 자동 생성
- **로컬 지식 소스** — `knowledge/` 폴더의 마크다운·JS 문서를 우선 참조하여 정확도 향상
- **MCP 서버 (선택)** — Claude가 로컬 지식을 도구로 활용할 수 있도록 MCP 프로토콜 연동 지원

## 프로젝트 구조

```
├── main.py                  # 봇 메인 루프 (게시글 스캔 → 답변 생성 → 댓글 등록)
├── config.py                # 설정 관리 (.env 로드, 경로, API URL 등)
├── prompt.txt               # Claude 시스템 프롬프트
├── .env.example             # 환경변수 템플릿
├── requirements.txt         # Python 패키지 의존성
├── knowledge/               # 로컬 지식 소스 (우선 참고 문서)
│   ├── instruction.md       # 시스템 프롬프트 오버라이드 (있으면 prompt.txt 대신 사용)
│   ├── faq.md               # 자주 묻는 질문
│   ├── rhino.md             # Rhino JS 엔진 관련 문서
│   ├── graal.md             # GraalJS 엔진 관련 문서
│   ├── migration.md         # 버전 마이그레이션 가이드
│   ├── version_issues.md    # 버전별 이슈 정리
│   └── example.js           # 예제 코드
└── modules/
    ├── api_client.py         # 네이버 카페 API 클라이언트
    ├── html_processor.py     # HTML → 텍스트 변환
    ├── reply_generator.py    # Claude API 기반 답변 생성
    ├── comment_poster.py     # Selenium 기반 댓글 등록
    ├── priority_knowledge.py # 로컬 지식 로더 및 검색
    ├── local_mcp_manager.py  # MCP 서버 프로세스 관리
    └── knowledge_mcp_server.py  # MCP 서버 구현
```

## 설치 방법

### 1. 사전 요구사항

- **Python 3.11+**
- **Google Chrome** 브라우저 설치
- **Anthropic API 키** — [console.anthropic.com](https://console.anthropic.com)에서 발급
- **네이버 계정** — 해당 카페에 스탭 이상 권한 필요

### 2. 저장소 클론 및 의존성 설치

```bash
git clone https://github.com/<your-username>/msgbot-cafe-ai-assistant.git
cd msgbot-cafe-ai-assistant
pip install -r requirements.txt
```

### 3. 환경변수 설정

`.env.example`을 `.env`로 복사한 뒤 실제 값을 입력합니다:

```bash
cp .env.example .env
```

#### 필수 환경변수

| 변수명 | 설명 | 예시 |
|---|---|---|
| `NAVER_ID` | 네이버 로그인 ID (봇 계정) | `your_naver_id` |
| `NAVER_PW` | 네이버 로그인 비밀번호 | `your_password` |
| `NAVER_CAFE_STAFF_COOKIE` | 네이버 카페 API 호출용 쿠키 (Selenium 로그인 실패 시 fallback) | 브라우저 개발자 도구에서 복사 |
| `ANTHROPIC_API_KEY` | Anthropic Claude API 키 | `sk-ant-api03-xxxx...` |

> **참고:** `NAVER_CAFE_STAFF_COOKIE`는 Selenium이 정상적으로 로그인하면 자동으로 추출되므로, 초기에는 빈 값으로 두어도 됩니다. Selenium 로그인이 반복 실패하는 환경에서만 수동으로 설정하세요.

#### 선택 환경변수

| 변수명 | 기본값 | 설명 |
|---|---|---|
| `CLAUDE_MODEL` | `claude-opus-4-6` | 사용할 Claude 모델명 |
| `CLAUDE_ENABLE_THINKING` | `true` | Claude 사고 과정(thinking) 활성화 |
| `CLAUDE_THINKING_BUDGET` | `2048` | 사고 과정 최대 토큰 수 |
| `CLAUDE_MCP_ENABLED` | `false` | MCP 서버 연동 활성화 |
| `LOCAL_MCP_AUTO_START` | `true` | MCP 활성 시 로컬 서버 자동 시작 |
| `LOCAL_MCP_HOST` | `127.0.0.1` | MCP 서버 호스트 |
| `LOCAL_MCP_PORT` | `8765` | MCP 서버 포트 |

## 실행 방법

### 기본 실행

```bash
python main.py
```

최초 실행 시 Chrome 브라우저가 열리며, 네이버 로그인을 직접 수행해야 합니다 (120초 대기).
로그인 세션은 `chrome_profile/` 폴더에 저장되어 이후 실행 시 자동 로그인됩니다.

### 실행 옵션

| 옵션 | 설명 |
|---|---|
| `--dry-run` | 테스트 모드 — 답변을 생성하지만 실제 댓글은 등록하지 않음 |
| `--headless` | 브라우저 창을 표시하지 않고 백그라운드에서 실행 |
| `--start-id <ID>` | 지정한 게시글 ID부터 모니터링 시작 |
| `--reprocess <ID> [ID ...]` | 특정 게시글을 강제로 재처리 |

### 사용 예시

```bash
# 테스트 모드 (댓글 등록 없이 확인)
python main.py --dry-run

# 백그라운드 실행
python main.py --headless

# 특정 게시글부터 시작
python main.py --start-id 53300

# 특정 글 강제 재처리
python main.py --reprocess 53297 53299

# 조합 가능
python main.py --dry-run --headless --start-id 53300
```

## 동작 원리

1. **폴링** — `state.json`에 저장된 마지막 게시글 ID 이후의 새 글을 60초 간격으로 스캔
2. **필터링** — 질문 게시판(menuId=3)의 글 중, 봇이 아직 답변하지 않은 글만 대상
3. **지식 검색** — `knowledge/` 폴더의 문서에서 질문과 관련된 컨텍스트 추출
4. **답변 생성** — Claude API에 시스템 프롬프트 + 컨텍스트 + 질문을 전달하여 답변 생성
5. **댓글 등록** — Selenium으로 네이버 카페에 댓글 등록
6. **후속 모니터링** — 답변한 글의 댓글을 주기적으로 확인, 작성자 추가 질문에 후속 답변

## 지식 소스 관리

`knowledge/` 폴더에 `.md`(마크다운) 또는 `.js`(자바스크립트) 파일을 추가하면 자동으로 참조됩니다.

- `instruction.md` — 이 파일이 있으면 `prompt.txt` 대신 시스템 프롬프트로 사용됩니다
- 나머지 `.md`/`.js` 파일 — 섹션 단위로 분할되어 질문과의 키워드 매칭으로 컨텍스트 제공

## 로그

실행 로그는 `logs/bot.log`에 기록됩니다. 콘솔에는 INFO 이상, 파일에는 DEBUG 이상 레벨이 출력됩니다.

## 라이선스

이 프로젝트는 개인 학습 및 커뮤니티 봉사 목적으로 제작되었습니다.
