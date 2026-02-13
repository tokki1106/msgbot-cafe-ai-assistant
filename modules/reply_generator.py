"""
Reply Generator
Claude API를 사용해 질문에 대한 답변을 생성한다.
"""
from __future__ import annotations

import logging
import re
from typing import Callable

from anthropic import Anthropic

import config

MAX_COMMENT_LENGTH = 2900  # Naver comment soft-limit
REFERENCE_TOOL_NAME = "get_priority_reference"

logger = logging.getLogger("cafe_bot.reply_generator")


class ReplyGenerator:
    """Claude API 기반 답변 생성기"""

    def __init__(
        self,
        system_prompt_override: str | None = None,
        context_lookup: Callable[[str, int], str] | None = None,
    ):
        self.client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
        self.model = config.CLAUDE_MODEL
        self.max_tokens = config.CLAUDE_MAX_TOKENS

        self.enable_tool_use = bool(getattr(config, "CLAUDE_ENABLE_TOOL_USE", True))
        self.tool_max_context_chars = int(getattr(config, "CLAUDE_TOOL_MAX_CONTEXT_CHARS", 12000))
        self.context_lookup = context_lookup

        self.enable_thinking = bool(getattr(config, "CLAUDE_ENABLE_THINKING", True))
        self.thinking_budget = max(256, int(getattr(config, "CLAUDE_THINKING_BUDGET", 2048)))

        self.enable_mcp = bool(getattr(config, "CLAUDE_MCP_ENABLED", False))
        self.mcp_beta_version = str(getattr(config, "CLAUDE_MCP_BETA_VERSION", "mcp-client-2025-04-04"))
        self.mcp_servers = self._build_mcp_servers()

        if system_prompt_override and system_prompt_override.strip():
            self.system_prompt = system_prompt_override.strip()
            logger.info("ReplyGenerator: external instruction applied (%d chars)", len(self.system_prompt))
        else:
            self.system_prompt = self._load_system_prompt()

        logger.info(
            "ReplyGenerator initialized (model=%s, tool_use=%s, thinking=%s, mcp=%s)",
            self.model,
            self.enable_tool_use,
            self.enable_thinking,
            bool(self.mcp_servers),
        )

    def _build_mcp_servers(self) -> list[dict]:
        if not self.enable_mcp:
            return []

        url = str(getattr(config, "CLAUDE_MCP_SERVER_URL", "") or "").strip()
        if not url:
            logger.warning("CLAUDE_MCP_ENABLED=true but CLAUDE_MCP_SERVER_URL is empty; MCP disabled.")
            return []

        server = {
            "type": "url",
            "name": str(getattr(config, "CLAUDE_MCP_SERVER_NAME", "knowledge_mcp")),
            "url": url,
        }

        token = str(getattr(config, "CLAUDE_MCP_AUTH_TOKEN", "") or "").strip()
        if token:
            server["authorization_token"] = token

        return [server]

    @staticmethod
    def _clean_reply(text: str) -> str:
        # Remove think-style tags
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
        text = re.sub(r"</?(?:think|analysis|verification)>", "", text)

        text = text.strip()
        text = re.sub(r"\n{3,}", "\n\n", text)

        if len(text) > MAX_COMMENT_LENGTH:
            text = text[:MAX_COMMENT_LENGTH]
            last_period = text.rfind(".")
            last_newline = text.rfind("\n")
            cut_point = max(last_period, last_newline)
            if cut_point > MAX_COMMENT_LENGTH * 0.7:
                text = text[:cut_point + 1]
            text += "\n\n(답변이 길어 일부를 줄였습니다. 추가 질문을 남겨 주세요.)"
            logger.warning("Reply trimmed to %d chars", MAX_COMMENT_LENGTH)

        return text

    def _load_system_prompt(self) -> str:
        try:
            with open(config.PROMPT_FILE, "r", encoding="utf-8") as f:
                prompt = f.read().strip()
            logger.debug("System prompt loaded (%d chars)", len(prompt))
            return prompt
        except FileNotFoundError:
            logger.error("Prompt file not found: %s", config.PROMPT_FILE)
            return (
                "당신은 카카오톡 봇 커뮤니티의 기술 스태프입니다. "
                "질문에 대해 간결하고 정확한 답변을 제공합니다."
            )

    @staticmethod
    def _extract_text(message) -> str:
        reply_text = ""
        for block in getattr(message, "content", []):
            if getattr(block, "type", "") == "text":
                reply_text += block.text
        return reply_text

    @staticmethod
    def _log_usage(message, label: str) -> None:
        usage = getattr(message, "usage", None)
        if not usage:
            return
        logger.info(
            "%s usage: input=%s, output=%s",
            label,
            getattr(usage, "input_tokens", "?"),
            getattr(usage, "output_tokens", "?"),
        )

    def _build_request_kwargs(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: dict | None = None,
    ) -> dict:
        kwargs = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": self.system_prompt,
            "messages": messages,
        }

        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice

        if self.enable_thinking:
            kwargs["thinking"] = {
                "type": "adaptive",
            }

        if self.mcp_servers:
            kwargs["mcp_servers"] = self.mcp_servers
            kwargs["betas"] = [self.mcp_beta_version]

        return kwargs

    def _messages_create(self, kwargs: dict):
        current = dict(kwargs)
        use_beta_api = bool(current.get("mcp_servers"))

        while True:
            api = self.client.beta.messages if use_beta_api else self.client.messages
            try:
                return api.create(**current)
            except Exception as e:
                if use_beta_api and ("mcp_servers" in current or "betas" in current):
                    logger.warning("Claude request failed with MCP enabled; retrying without MCP: %s", e)
                    current.pop("mcp_servers", None)
                    current.pop("betas", None)
                    use_beta_api = False
                    continue

                if "thinking" in current:
                    logger.warning("Claude request failed with thinking enabled; retrying without thinking: %s", e)
                    current.pop("thinking", None)
                    continue

                raise

    def _request_direct(self, user_message: str):
        kwargs = self._build_request_kwargs(
            messages=[{"role": "user", "content": user_message}],
        )
        return self._messages_create(kwargs)

    @staticmethod
    def _reference_tool_schema() -> dict:
        return {
            "name": REFERENCE_TOOL_NAME,
            "description": (
                "Return curated reference context from local instruction/docs "
                "for the current user question."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_chars": {"type": "integer", "minimum": 500, "maximum": 20000},
                },
                "required": ["query"],
            },
        }

    def _resolve_reference_payload(self, query: str, max_chars: int, fallback_context: str) -> str:
        if self.context_lookup:
            try:
                looked_up = self.context_lookup(query, max_chars)
                if looked_up:
                    return looked_up[:max_chars]
            except Exception as e:
                logger.warning("context_lookup failed: %s", e)

        if fallback_context:
            return fallback_context[:max_chars]
        return "참고 컨텍스트가 없습니다."

    def _request_with_reference_tool(
        self,
        user_message: str,
        default_query: str,
        fallback_context: str = "",
    ):
        tool_schema = self._reference_tool_schema()
        first_kwargs = self._build_request_kwargs(
            messages=[{"role": "user", "content": user_message}],
            tools=[tool_schema],
            tool_choice={"type": "auto"},
        )
        first = self._messages_create(first_kwargs)

        if getattr(first, "stop_reason", "") != "tool_use":
            return first

        tool_results = []
        for block in getattr(first, "content", []):
            if getattr(block, "type", "") != "tool_use":
                continue
            if getattr(block, "name", "") != REFERENCE_TOOL_NAME:
                continue

            tool_input = getattr(block, "input", {}) or {}
            query = default_query
            max_chars = self.tool_max_context_chars
            if isinstance(tool_input, dict):
                if isinstance(tool_input.get("query"), str) and tool_input.get("query").strip():
                    query = tool_input["query"].strip()
                requested = tool_input.get("max_chars")
                if isinstance(requested, int):
                    max_chars = max(500, min(20000, requested))

            payload = self._resolve_reference_payload(query, max_chars, fallback_context)
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": payload,
                }
            )

        if not tool_results:
            return first

        final_kwargs = self._build_request_kwargs(
            messages=[
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": first.content},
                {"role": "user", "content": tool_results},
            ],
            tools=[tool_schema],
        )
        return self._messages_create(final_kwargs)

    def _call_claude(
        self,
        tool_user_message: str,
        fallback_user_message: str,
        default_query: str,
        fallback_context: str = "",
    ):
        can_use_reference_tool = self.enable_tool_use and (self.context_lookup is not None or bool(fallback_context))
        if can_use_reference_tool:
            try:
                return self._request_with_reference_tool(
                    user_message=tool_user_message,
                    default_query=default_query,
                    fallback_context=fallback_context,
                )
            except Exception as e:
                logger.warning("Tool-use request failed, fallback to direct call: %s", e)

        return self._request_direct(fallback_user_message)

    def generate_reply(self, subject: str, content: str) -> str:
        user_parts_tool = []
        user_parts_direct = []

        if self.enable_tool_use:
            user_parts_tool.append(
                "## 참고 자료\n"
                f"필요하면 `{REFERENCE_TOOL_NAME}` 도구를 호출해 참고 문맥을 확인한 뒤 답변하세요."
            )

        current_question = (
            f"## 현재 질문\n"
            f"제목: {subject}\n\n"
            f"내용:\n{content}"
        )
        user_parts_tool.append(current_question)
        user_parts_direct.append(current_question)

        answer_rule = (
            "\n위 질문에 대한 답변만 작성하세요. "
            "접두어(예: '답변:')는 붙이지 마세요."
        )
        user_parts_tool.append(answer_rule)
        user_parts_direct.append(answer_rule)

        tool_user_message = "\n\n---\n\n".join(user_parts_tool)
        fallback_user_message = "\n\n---\n\n".join(user_parts_direct)
        default_query = f"{subject}\n{content[:1200]}"

        try:
            logger.info("Claude API call (subject=%s)", subject[:50])
            message = self._call_claude(
                tool_user_message=tool_user_message,
                fallback_user_message=fallback_user_message,
                default_query=default_query,
            )
            self._log_usage(message, "Claude")
            return self._clean_reply(self._extract_text(message))
        except Exception as e:
            logger.error("Claude API failed: %s", e)
            return ""

    def generate_followup_reply(
        self,
        subject: str,
        content: str,
        conversation_history: list[dict],
        new_comment: str,
        commenter_nick: str,
    ) -> str:
        user_parts_tool = []
        user_parts_direct = []

        if self.enable_tool_use:
            user_parts_tool.append(
                "## 참고 자료\n"
                f"필요하면 `{REFERENCE_TOOL_NAME}` 도구를 호출해 참고 문맥을 확인하세요."
            )

        post_info = (
            f"## 원문 게시글\n"
            f"제목: {subject}\n\n"
            f"내용:\n{content[:1000]}"
        )
        user_parts_tool.append(post_info)
        user_parts_direct.append(post_info)

        if conversation_history:
            conv_lines = []
            for msg in conversation_history:
                nick = msg["nick"]
                role = "(봇)" if msg.get("is_bot") else "(질문자)"
                conv_lines.append(f"{nick} {role}: {msg['content']}")
            conv_text = "## 기존 댓글 대화\n" + "\n\n".join(conv_lines)
            user_parts_tool.append(conv_text)
            user_parts_direct.append(conv_text)

        new_comment_text = (
            f"## 새 댓글 (답변 필요)\n"
            f"{commenter_nick}: {new_comment}"
        )
        user_parts_tool.append(new_comment_text)
        user_parts_direct.append(new_comment_text)

        reply_rule = (
            "\n위 댓글에 대한 답변을 작성하세요. "
            "기존 대화 맥락을 반영하고 접두어는 붙이지 마세요."
        )
        user_parts_tool.append(reply_rule)
        user_parts_direct.append(reply_rule)

        tool_user_message = "\n\n---\n\n".join(user_parts_tool)
        fallback_user_message = "\n\n---\n\n".join(user_parts_direct)
        default_query = f"{subject}\n{new_comment}\n{content[:900]}"

        try:
            logger.info("Claude follow-up call (commenter=%s)", commenter_nick)
            message = self._call_claude(
                tool_user_message=tool_user_message,
                fallback_user_message=fallback_user_message,
                default_query=default_query,
            )
            self._log_usage(message, "Claude follow-up")
            return self._clean_reply(self._extract_text(message))
        except Exception as e:
            logger.error("Claude API failed (follow-up): %s", e)
            return ""
