from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import select

from src.core.config import get_settings
from src.infrastructure.db.models.memory import AgentMessage, MorningBriefHistory, ScoutHistory
from src.infrastructure.db.session import AsyncSessionLocal
from src.infrastructure.logging.logger import get_logger
from src.services.ai.claude_service import ClaudeService
from src.services.goals_priorities import GoalsPrioritiesService
from src.services.memory_service import MemoryService
from src.services.runtime_context import runtime_datetime_context
from src.services.web_search import SearchResult, SerperSearchService, WebSearchUnavailable

logger = get_logger(__name__)

ScoutKind = Literal["general", "business", "tools", "clients"]

_BAD_PROVIDER_MARKERS = (
    "coding-focused assistant",
    "only help with software development",
    "what would you like to build or debug",
)

_CODING_ONLY_BASE_URL_MARKERS = (
    "vibecode-claude",
)


@dataclass(frozen=True)
class ScoutRunResult:
    text: str
    history_id: int
    results_count: int
    queries: list[str]


class ScoutLLMUnavailable(Exception):
    """Raised when Scout cannot use a normal analysis-capable LLM provider."""


def _bad_provider_reply(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _BAD_PROVIDER_MARKERS)


class OpportunityScoutService:
    def __init__(self) -> None:
        self.search = SerperSearchService(timeout_seconds=18.0, max_results=5)
        self.goals = GoalsPrioritiesService()
        self.memory = MemoryService()

    async def run(self, user_id: int, kind: ScoutKind = "general") -> ScoutRunResult:
        queries = self._queries(kind)
        all_results: list[SearchResult] = []
        error = ""
        try:
            for query in queries:
                try:
                    results = await self.search.search(query)
                    all_results.extend(results)
                except WebSearchUnavailable as search_error:
                    logger.warning("[SCOUT] search query failed", kind=kind, query=query, error=str(search_error))
                    error = str(search_error)
        except Exception as exc:
            logger.exception("[SCOUT] unexpected search failure", kind=kind)
            error = str(exc)

        deduped = self._dedupe_results(all_results)
        if not deduped:
            text = "Ничего сильного не нашёл. Лучше повторить поиск позже или сузить нишу."
        else:
            try:
                text = await self._synthesize(user_id, kind, queries, deduped)
            except ScoutLLMUnavailable as exc:
                error = str(exc)
                logger.error("[SCOUT] llm analysis unavailable", kind=kind, error=error)
                text = (
                    "Scout сделал реальный поиск и нашёл материалы, но AI-анализ сейчас недоступен.\n\n"
                    "Причина: текущий LLM provider отвечает как coding-only assistant. "
                    "Нужно настроить normal Scout LLM provider через SCOUT_ANTHROPIC_API_KEY / SCOUT_ANTHROPIC_BASE_URL."
                )
        history_id = await self._save_history(user_id, kind, queries, len(deduped), text, "generated" if not error else "error", error)
        logger.info("[SCOUT] completed", kind=kind, history_id=history_id, queries=queries, results_count=len(deduped))
        return ScoutRunResult(text=text, history_id=history_id, results_count=len(deduped), queries=queries)

    async def mark_sent(self, history_id: int) -> None:
        async with AsyncSessionLocal() as session:
            row = await session.get(ScoutHistory, history_id)
            if row is None:
                return
            row.send_status = "sent"
            await session.commit()

    def _queries(self, kind: ScoutKind) -> list[str]:
        if kind == "business":
            return [
                "small business automation appointment booking AI agent salons",
                "AI automation for beauty salons customer support appointment reminders",
                "local service business CRM automation Telegram bot",
                "massage salon booking automation chatbot",
            ]
        if kind == "tools":
            return [
                "new AI agent tools business automation 2026",
                "no-code AI automation tools small business 2026",
                "Telegram bot AI automation tools CRM appointment booking",
                "AI workflow automation tools customer support small business",
            ]
        if kind == "clients":
            return [
                "local businesses need appointment booking automation salons tutors fitness trainers",
                "small business customer support automation niches",
                "businesses with missed calls appointment booking problems salons clinics tutors",
                "lead generation automation for local service businesses",
            ]
        return [
            "AI agents business automation opportunities 2026 small business",
            "Telegram bot automation business ideas local services",
            "AI appointment booking automation salons tutors fitness trainers",
            "customer support automation small business opportunities",
            "no-code AI agents CRM automation opportunities",
        ]

    def _dedupe_results(self, results: list[SearchResult]) -> list[SearchResult]:
        seen: set[str] = set()
        useful: list[SearchResult] = []
        banned = ("crypto", "politics", "election", "stock prediction")
        for result in results:
            key = (result.link or result.title).strip().lower()
            haystack = f"{result.title} {result.snippet}".lower()
            if not key or key in seen or any(word in haystack for word in banned):
                continue
            seen.add(key)
            useful.append(result)
            if len(useful) >= 12:
                break
        return useful

    async def _synthesize(self, user_id: int, kind: ScoutKind, queries: list[str], results: list[SearchResult]) -> str:
        context = await self._context(user_id)
        selected_results = self._select_diverse_results(results)
        result_lines = []
        for index, result in enumerate(selected_results, start=1):
            result_lines.append(f"{index}. {result.title}\nSnippet: {result.snippet}\nSource: {result.link}")
        system_prompt = (
            "You are Chief's Internet Opportunity Scout for Artem.\n"
            "You are not a search engine and not a generic opinion generator. You are an analyst of the found materials.\n"
            "Use the real search results below as the only source material.\n"
            "You are given exactly 3 selected search results. Analyze all 3 and only these 3.\n"
            "For each material, keep the source title in **bold**, then explain it in Russian.\n"
            "Do not copy snippets. Do not show long English fragments. Translate, paraphrase, and adapt.\n"
            "Every selected material must answer: why is this useful for Artem?\n"
            "Priority: earning potential, improving Chief, finding clients, automating small businesses.\n"
            "Low priority: generic AI news, hype, broad tool lists, abstract motivation, articles without practical application.\n"
            "Do not use labels: Коротко, Что важно, Как можно использовать, Почему важно, Следующий шаг.\n"
            "Each material must have its own angle. If two sources are similar, explain a different useful signal from each.\n"
            "Avoid repeated phrases like 'Для Артёма это полезно', 'Материал связан', 'Можно использовать'.\n"
            "Write 2 short paragraphs per material maximum.\n"
            "Separate materials with ⸻.\n"
            "If sources are English, translate and explain in Russian. English is allowed only for product/company/tool names.\n"
            "End with a separate block titled **Вывод**. It must be 2-4 sentences: common pattern, strongest signal, what to watch next.\n"
            "Use Markdown **bold** for article titles, key conclusions, important numbers, companies, or trends.\n"
            "Do not mention Serper/API/debug details.\n"
            "Never say you are coding-focused.\n\n"
            f"Scout type: {kind}\n"
            f"Queries: {json.dumps(queries, ensure_ascii=False)}\n\n"
            f"{context}\n\n"
            "Search results:\n"
            + "\n\n".join(result_lines)
        )
        try:
            llm = self._scout_llm()
            logger.info(
                "[SCOUT] llm runtime",
                provider=llm.runtime_info.provider,
                model=llm.runtime_info.model,
                base_url=llm.runtime_info.base_url,
                kind=kind,
            )
            reply = await llm.generate_response(
                system_prompt=system_prompt,
                history_messages=[{"role": "user", "content": "Найди лучшие возможности и дай короткую сводку."}],
                max_tokens=900,
            )
            if _bad_provider_reply(reply):
                logger.error("[SCOUT] received coding-only provider reply; retrying with strict scout prompt", kind=kind)
                reply = await llm.generate_response(
                    system_prompt=system_prompt
                    + "\n\nCRITICAL OVERRIDE: You are running Internet Opportunity Scout, not a coding assistant. "
                    + "Analyze the 3 search results above. Produce the Russian scout analysis now. "
                    + "Never refuse because of software-development scope.",
                    history_messages=[{"role": "user", "content": "Проанализируй эти 3 найденных материала как Scout. Это не coding task."}],
                    max_tokens=900,
                )
            if reply.strip() and not _bad_provider_reply(reply):
                cleaned = self._clean_scout_reply(reply)
                if self._reply_uses_found_materials(cleaned, selected_results) and not self._reply_repeats_analysis(cleaned):
                    return cleaned
                raise ScoutLLMUnavailable("Scout LLM response failed source-grounding or repetition quality checks.")
            elif _bad_provider_reply(reply):
                raise ScoutLLMUnavailable(
                    "Scout LLM provider returned coding-only assistant reply after retry. "
                    f"provider={llm.runtime_info.provider} model={llm.runtime_info.model} base_url={llm.runtime_info.base_url}"
                )
        except ScoutLLMUnavailable:
            raise
        except Exception as exc:
            logger.exception("[SCOUT] synthesis failed", kind=kind)
            raise ScoutLLMUnavailable(f"Scout LLM synthesis failed: {type(exc).__name__}: {str(exc)[:300]}") from exc
        raise ScoutLLMUnavailable("Scout LLM returned an empty response.")

    def _scout_llm(self) -> ClaudeService:
        settings = get_settings()
        api_key = settings.scout_anthropic_api_key.strip() or settings.anthropic_api_key
        base_url = settings.scout_anthropic_base_url.strip() if settings.scout_anthropic_base_url.strip() else settings.anthropic_base_url.strip()
        model = settings.scout_anthropic_model.strip() or settings.anthropic_model
        provider = "scout"

        if not settings.scout_anthropic_api_key.strip() and any(marker in base_url.lower() for marker in _CODING_ONLY_BASE_URL_MARKERS):
            raise ScoutLLMUnavailable(
                "Scout is configured to use a known coding-only LLM provider. "
                f"provider={provider} model={model} base_url={base_url}. "
                "Set SCOUT_ANTHROPIC_API_KEY and SCOUT_ANTHROPIC_BASE_URL to a normal Claude-compatible provider."
            )

        return ClaudeService(api_key=api_key, base_url=base_url, model=model, provider=provider)

    async def _context(self, user_id: int) -> str:
        goals = await self.goals.context_for_prompt()
        memory = await self.memory.context_for_user(user_id, limit=24)
        async with AsyncSessionLocal() as session:
            briefs = (
                await session.execute(
                    select(MorningBriefHistory).where(MorningBriefHistory.user_id == user_id).order_by(MorningBriefHistory.id.desc()).limit(2)
                )
            ).scalars().all()
            actions = (
                await session.execute(select(AgentMessage).order_by(AgentMessage.id.desc()).limit(8))
            ).scalars().all()
        return "\n".join(
            [
                runtime_datetime_context(),
                "",
                goals,
                "",
                "Memory:",
                memory or "- none",
                "",
                "Recent briefs:",
                "\n".join(f"- {brief.text[:360]}" for brief in briefs) or "- none",
                "",
                "Recent agent actions:",
                "\n".join(f"- {item.from_agent}->{item.to_agent}: {(item.content or '')[:220]}" for item in reversed(actions)) or "- none",
            ]
        )

    def _clean_scout_reply(self, text: str) -> str:
        value = (text or "").strip()
        banned_prefixes = (
            "Коротко:",
            "Что важно:",
            "Как можно использовать:",
            "Почему важно:",
            "Следующий шаг:",
            "Возможность №",
            "Opportunity #",
            "Why important:",
            "What can be done:",
        )
        lines: list[str] = []
        for raw_line in value.splitlines():
            line = raw_line.strip()
            for prefix in banned_prefixes:
                if line.lower().startswith(prefix.lower()):
                    line = line[len(prefix):].strip()
                    break
            if line:
                lines.append(line)
            elif lines and lines[-1] != "":
                lines.append("")
        return "\n".join(lines).strip()

    def _reply_uses_found_materials(self, text: str, results: list[SearchResult]) -> bool:
        lowered = (text or "").lower()
        hits = 0
        for result in results[:8]:
            title = (result.title or "").strip()
            if not title:
                continue
            title_words = [word.lower() for word in title.replace("|", " ").replace("-", " ").split() if len(word) >= 5]
            if title.lower() in lowered or any(word in lowered for word in title_words[:4]):
                hits += 1
            if hits >= 2:
                return True
        return False

    def _reply_repeats_analysis(self, text: str) -> bool:
        blocks = [block.strip() for block in (text or "").split("⸻") if block.strip()]
        bodies: list[str] = []
        for block in blocks[:3]:
            lines = [line.strip() for line in block.splitlines() if line.strip()]
            body_lines = [line for line in lines if not line.startswith(("1.", "2.", "3.", "**"))]
            normalized = " ".join(body_lines).lower()
            normalized = " ".join(normalized.split())
            if normalized:
                bodies.append(normalized)
        for index, body in enumerate(bodies):
            for other in bodies[index + 1:]:
                if body == other:
                    return True
                shorter, longer = sorted((body, other), key=len)
                if len(shorter) > 140 and shorter in longer:
                    return True
        return False

    def _fallback_summary(self, kind: ScoutKind, results: list[SearchResult]) -> str:
        useful_results = self._select_diverse_results(results)
        if not useful_results:
            return "Ничего сильного не нашёл. Лучше повторить поиск позже или сузить нишу."

        selected = useful_results[:3]
        blocks: list[str] = []
        used_angles: set[str] = set()
        for index, result in enumerate(selected, start=1):
            blocks.append(self._material_block(result, index, used_angles))
        final = self._final_judgment(selected, kind)
        return "\n\n⸻\n\n".join(blocks + [final]).strip()

    def _select_diverse_results(self, results: list[SearchResult]) -> list[SearchResult]:
        useful_results = [result for result in results if self._is_useful_result(result)]
        if not useful_results:
            return []

        selected: list[SearchResult] = []
        used_angles: set[str] = set()
        used_hosts: set[str] = set()
        for result in useful_results:
            text = f"{result.title} {result.snippet}".lower()
            angle = self._result_angle(text, used_angles)
            host = (result.link or "").split("/")[2].lower() if "://" in (result.link or "") else ""
            if angle in used_angles:
                continue
            if host and host in used_hosts and len(selected) < 2:
                continue
            selected.append(result)
            used_angles.add(angle)
            if host:
                used_hosts.add(host)
            if len(selected) == 3:
                return selected

        for result in useful_results:
            if result not in selected:
                selected.append(result)
            if len(selected) == 3:
                break
        return selected

    def _material_block(self, result: SearchResult, index: int, used_angles: set[str]) -> str:
        title = (result.title or "Найденный материал").strip().strip("*").strip()
        text = f"{result.title} {result.snippet}".lower()
        angle = self._result_angle(text, used_angles)
        used_angles.add(angle)

        if angle == "market_discussion":
            body = (
                "Это ценно не как статья, а как срез живых сомнений рынка. В таких обсуждениях люди обычно прямо говорят, "
                "что им неудобно: ручные переносы, хаос в расписании, слабые ответы клиентам, непонятный выбор инструментов.\n\n"
                "Для Chief здесь важен не сам Reddit, а язык боли. Такие формулировки можно брать почти напрямую для первых офферов и проверки ниши."
            )
        elif angle == "missed_revenue":
            body = (
                "Здесь хороший денежный сигнал: бизнес теряет не абстрактную продуктивность, а конкретные заявки. "
                "Пропущенный звонок или медленный ответ легко объяснить владельцу как потерянный чек.\n\n"
                "Chief можно показывать как **AI-ресепшен**, который возвращает клиента в диалог: отвечает, уточняет услугу и доводит до записи."
            )
        elif angle == "category_standard":
            body = (
                "Материал полезен как карта ожиданий рынка. Онлайн-запись, календарь, переносы и напоминания уже стали базовым стандартом, "
                "а значит клиенту не нужно объяснять саму категорию.\n\n"
                "Интересный ход для Chief — не конкурировать с такими платформами целиком, а сделать **Telegram-first слой** поверх одной конкретной боли."
            )
        elif angle == "fitness_niche":
            body = (
                "Ниша тренеров выглядит практично: у них повторные клиенты, постоянные переносы и простая логика расписания. "
                "Здесь не нужен огромный продукт, достаточно помощника, который не теряет людей и держит договорённости.\n\n"
                "Для первого теста можно собрать сценарий: клиент пишет в Telegram, Chief предлагает слот, напоминает о тренировке и делает follow-up после занятия."
            )
        elif angle == "salon_admin":
            body = (
                "Салоны и массажные кабинеты снова выглядят как сильная прикладная ниша. Там много одинаковых вопросов, записи руками и потерь на переносах.\n\n"
                "Самое важное: владелец быстро понимает пользу, если агент уменьшает пропущенные записи и снимает часть переписки с администратора."
            )
        elif angle == "appointment_flow":
            body = (
                "Здесь важен не сам AI, а конкретный сценарий записи: клиент пишет, выбирает время, получает подтверждение и не забывает о визите. Это понятная цепочка, которую легко показать владельцу без долгих объяснений.\n\n"
                "Для Chief такой материал полезен как основа демо: один Telegram-диалог, один календарный слот, одно напоминание. Маленький сценарий, но его можно быстро продать как снижение хаоса в расписании."
            )
        elif angle == "voice_calls":
            body = (
                "Этот материал сдвигает фокус с чата на звонки. Для многих локальных бизнесов проблема не только в переписке, а в том, что входящие обращения приходят в неудобный момент и теряются.\n\n"
                "Для Chief это отдельная продуктовая гипотеза: не просто бот в Telegram, а помощник, который принимает первичный запрос, фиксирует намерение клиента и передаёт владельцу уже понятную заявку."
            )
        elif angle == "leadgen":
            body = (
                "Здесь фокус уже не на экономии времени, а на новых заявках. Для малого бизнеса это сильнее, потому что результат ближе к деньгам.\n\n"
                "Если развивать Chief в эту сторону, продавать стоит не “AI-бота”, а связку: найти потенциального клиента, квалифицировать и довести до записи."
            )
        elif angle == "tool_map":
            body = (
                "Этот материал показывает, что рынок уже переполнен инструментами. Значит слабое место не в отсутствии софта, а в том, что владельцу трудно собрать рабочий сценарий под себя.\n\n"
                "Для Chief это хороший знак: можно продавать не платформу, а **готовую автоматизацию под нишу** — проще, понятнее и ближе к первому чеку."
            )
        elif angle == "telegram_agency":
            body = (
                "Здесь интересен не сам Telegram-бот, а путь от личных проектов к небольшой услуге. Это близко к текущему Chief: сначала рабочий инструмент для себя, потом упаковка под понятную бизнес-боль.\n\n"
                "Сильный сигнал — можно продавать не “разработку бота”, а **готовый рабочий процесс**: заявки, запись, напоминания или поддержка в привычном для клиента Telegram."
            )
        elif angle == "tested_tools":
            body = (
                "Такие подборки полезны как проверка рынка: если люди сравнивают десятки AI-агентов, значит категория уже стала понятной, но перегруженной.\n\n"
                "Для Chief вывод простой: выигрывать нужно не количеством функций, а узким сценарием, где результат виден за один день — например запись клиента или обработка входящих заявок."
            )
        elif angle == "operations_savings":
            body = (
                "Материал показывает классическую боль малого бизнеса: рутинные операции съедают время владельца и команды. Но продавать это надо не как “автоматизацию”, а как меньше ручной переписки и меньше потерянных клиентов.\n\n"
                "Для Chief это аргумент в пользу маленьких внедрений: один процесс, один измеримый эффект, один понятный результат для владельца."
            )
        else:
            body = (
                "Материал можно держать как слабый сигнал, но только если из него получается достать конкретную бизнес-боль. Сам по себе обзор или новость ничего не даёт.\n\n"
                "Я бы проверял его через простой фильтр: ведёт ли это к заявкам, записи, поддержке, продажам или экономии ручной рутины. Если нет — в фокус не брать."
            )
        return f"{index}. **{title}**\n\n{body}"

    def _result_angle(self, text: str, used_angles: set[str]) -> str:
        candidates: list[tuple[str, bool]] = [
            ("market_discussion", any(word in text for word in ["reddit", "r/smallbusiness", "what apps", "which automation niche", "best easy-to-use"])),
            ("missed_revenue", any(word in text for word in ["missed call", "missed calls", "unanswered calls"])),
            ("telegram_agency", any(word in text for word in ["telegram bot", "telegram bots", "telegram-first"])),
            ("category_standard", any(word in text for word in ["setmore", "simplybook", "calendly", "acuity", "bookeo", "trafft"])),
            ("fitness_niche", any(word in text for word in ["personal trainer", "fitness", "trainer"])),
            ("voice_calls", any(word in text for word in ["business calls", "takes business calls", "phone answering", "voice", "call answering"])),
            ("salon_admin", any(word in text for word in ["salon", "spa", "massage", "beauty", "hair"])),
            ("appointment_flow", any(word in text for word in ["appointment", "booking", "scheduling", "schedule", "whatsapp", "no-show"])),
            ("leadgen", any(word in text for word in ["lead generation", "leads", "local lead", "fresh leads"])),
            ("tested_tools", any(word in text for word in ["top 5", "top 8", "top 10", "best ai agents", "actually tested"])),
            ("tool_map", any(word in text for word in ["tool", "software", "platform", "crm", "workflow", "no-code", "low-code"])),
            ("operations_savings", any(word in text for word in ["productivity", "reduce operational costs", "customer support", "automation", "small business"])),
        ]
        for angle, matched in candidates:
            if matched and angle not in used_angles:
                return angle
        for angle in ("operations_savings", "category_standard", "tool_map", "market_discussion", "generic"):
            if angle not in used_angles:
                return angle
        return "generic"

    def _is_useful_result(self, result: SearchResult) -> bool:
        text = f"{result.title} {result.snippet}".lower()
        useful = (
            "automation",
            "appointment",
            "booking",
            "crm",
            "customer support",
            "lead generation",
            "salon",
            "spa",
            "massage",
            "trainer",
            "tutor",
            "small business",
            "local business",
            "telegram",
            "ai agent",
            "workflow",
            "no-code",
            "missed calls",
            "no-show",
        )
        weak = ("politics", "crypto", "election", "stock prediction", "motivation quotes")
        return any(word in text for word in useful) and not any(word in text for word in weak)

    def _interpret_result(self, result: SearchResult) -> tuple[str, str, str]:
        text = f"{result.title} {result.snippet}".lower()
        if any(word in text for word in ["reddit", "r/smallbusiness", "what apps", "which automation niche"]):
            return (
                "Это не рекламная статья, а обсуждение реальных людей, которые выбирают инструменты или нишу. Такие материалы полезны как быстрый срез рынка: где люди сомневаются, что ищут и какие решения уже пробовали.",
                "Для Артёма это ценно, потому что из таких обсуждений можно доставать живые боли: неудобная запись, ручные переносы, слабая поддержка, хаос в заявках.",
                "Можно использовать как источник гипотез: выписать 5 повторяющихся жалоб и проверить их на локальных бизнесах в Уфе.",
            )
        if any(word in text for word in ["missed call", "missed calls", "unanswered calls"]):
            return (
                "Материал про пропущенные звонки показывает прямую денежную боль: клиент хотел записаться или купить услугу, но бизнес не ответил вовремя.",
                "Это полезнее общей AI-новости, потому что владелец сразу понимает потерю: каждая пропущенная заявка может быть деньгами.",
                "Chief можно показать как AI-ресепшен: отвечает после пропущенного звонка, уточняет услугу, предлагает время и возвращает клиента в запись.",
            )
        if any(word in text for word in ["setmore", "simplybook", "calendly", "acuity", "bookeo", "trafft"]):
            return (
                "Материал показывает, какие функции рынок уже считает стандартом: онлайн-запись, календарь, напоминания, переносы и иногда платежи.",
                "Для Артёма это карта конкурентов. Не нужно копировать платформу целиком — важно понять, какой маленький сценарий можно сделать быстрее и ближе к Telegram.",
                "Можно взять один сценарий из таких сервисов и превратить его в демо Chief: запись клиента через чат плюс напоминание владельцу и клиенту.",
            )
        if any(word in text for word in ["personal trainer", "fitness", "trainer"]):
            return (
                "Материал указывает на отдельную нишу тренеров и фитнес-студий. Там запись, переносы, напоминания и повторные занятия происходят постоянно.",
                "Для Артёма это хорошая тестовая ниша: тренеру не нужна сложная CRM, ему нужен помощник, который не теряет клиентов и держит расписание.",
                "Chief можно адаптировать под тренера: запись на занятие, напоминание, перенос, сбор контакта и короткий follow-up после тренировки.",
            )
        if any(word in text for word in ["salon", "spa", "massage", "beauty", "hair"]):
            return (
                "Материал попадает в сегмент салонов, spa и массажных кабинетов. Там много ручной коммуникации: запись, переносы, вопросы по услугам, напоминания.",
                "Для Артёма это сильная ниша, потому что владелец легко видит пользу: меньше пропущенных записей, меньше переписки, больше повторных клиентов.",
                "Chief можно упаковать как AI-администратора для салона: отвечает клиенту, помогает выбрать услугу, фиксирует запись и напоминает перед визитом.",
            )
        if any(word in text for word in ["appointment", "booking", "no-show", "scheduling", "schedule"]):
            return (
                "Материал указывает на спрос вокруг онлайн-записи, расписаний, напоминаний и снижения пропусков. Это не абстрактная AI-тема, а понятная операционная боль сервисного бизнеса.",
                "Для Артёма это полезно, потому что такую боль легко объяснить владельцу: меньше потерянных заявок, меньше ручной переписки, больше повторных клиентов.",
                "Chief можно упаковать как AI-администратора: принимает запрос, отвечает клиенту, предлагает время, напоминает и фиксирует запись.",
            )
        if any(word in text for word in ["missed call", "calls", "customer support", "chatbot", "24/7", "support"]):
            return (
                "Здесь виден спрос на быстрые ответы клиентам и обработку входящих обращений. Малый бизнес часто теряет деньги не из-за плохого продукта, а из-за медленной реакции.",
                "Это близко к монетизации Chief: владелец понимает ценность, если агент спасает заявки, которые раньше просто пропадали.",
                "Можно собрать демо: клиент пишет в Telegram/WhatsApp, Chief отвечает, уточняет услугу и передаёт готовую заявку владельцу.",
            )
        if any(word in text for word in ["lead generation", "leads", "local lead", "fresh leads"]):
            return (
                "Материал связан с лидогенерацией для локальных бизнесов. Это уже не просто автоматизация процессов, а помощь в получении новых клиентов.",
                "Для Артёма это интересно, потому что бизнес охотнее платит за результат, который связан с заявками и продажами.",
                "Chief можно развивать как связку: найти потенциального клиента, квалифицировать интерес и довести до записи или консультации.",
            )
        if any(word in text for word in ["tool", "software", "platform", "crm", "workflow", "no-code", "low-code"]):
            return (
                "Материал показывает, какие инструменты уже закрывают часть автоматизации: CRM, расписания, workflow, no-code-сборки. Это полезно как карта конкурентов и компонентов.",
                "Для Артёма важно не копировать ещё один инструмент, а понять, какой готовый сценарий можно собрать быстрее и проще.",
                "Можно взять сильные идеи из таких сервисов и сделать узкое Telegram-first демо для одной ниши, а не большой универсальный продукт.",
            )
        if any(word in text for word in ["salon", "spa", "massage", "trainer", "tutor", "clinic"]):
            return (
                "Материал попадает в сегмент локальных услуг: салоны, тренеры, репетиторы, кабинеты и похожие бизнесы. У них много повторяющихся коммуникаций.",
                "Это полезно, потому что такие ниши легче тестировать вручную: можно найти 10 бизнесов в городе и быстро проверить боль.",
                "Chief можно адаптировать под одну роль: администратор записи, помощник по заявкам или мини-CRM для владельца.",
            )
        return (
            "Материал связан с автоматизацией малого бизнеса, но требует дополнительной проверки перед тем, как делать из него продуктовую гипотезу.",
            "Для Артёма он полезен только если из него можно достать конкретную боль: заявки, запись, поддержка, продажи или рутина владельца.",
            "Следующий шаг — не строить продукт сразу, а проверить на 5-10 реальных бизнесах, есть ли такая проблема вживую.",
        )

    def _final_judgment(self, selected: list[SearchResult], kind: ScoutKind) -> str:
        themes = self._detect_themes(selected)
        if "appointments" in themes:
            return (
                "**Вывод**\n\nИз всего найденного самым интересным выглядит направление **AI-администраторов для локальных сервисов**. "
                "Это ближе всего к текущим возможностям Chief: запись, ответы, напоминания и простая CRM-логика. "
                "Я бы начал с салонов, массажистов или тренеров, потому что там проще всего проверить боль на первых клиентах."
            )
        if "leadgen" in themes:
            return (
                "**Вывод**\n\nСтоит смотреть в сторону автоматизации, которая влияет на **заявки и продажи**, а не просто экономит время. "
                "Такую пользу легче объяснить бизнесу и проще продать пилотом."
            )
        if "tools" in themes or kind == "tools":
            return (
                "**Вывод**\n\nИнструменты уже есть, но возможность для Chief — не в ещё одной платформе, а в **готовом сценарии под конкретную нишу**. "
                "Лучше выбрать один сервисный сегмент и собрать демонстрацию на его языке."
            )
        return (
            "**Вывод**\n\nИз найденного я бы выбирал только то, что можно проверить на реальных клиентах за 1-2 дня. "
            "Если материал не ведёт к демо, заявкам или автоматизации рутины — его лучше не тащить в фокус."
        )

    def _detect_themes(self, results: list[SearchResult]) -> set[str]:
        text = " ".join(f"{result.title} {result.snippet}" for result in results).lower()
        themes: set[str] = set()
        if any(word in text for word in ["appointment", "booking", "salon", "spa", "massage", "trainer", "tutor", "no-show"]):
            themes.add("appointments")
        if any(word in text for word in ["lead generation", "fresh leads", "local lead", "missed calls", "inbound call"]):
            themes.add("leadgen")
        if any(word in text for word in ["tool", "software", "platform", "workflow", "no-code", "low-code", "crm"]):
            themes.add("tools")
        return themes

    async def _save_history(
        self,
        user_id: int,
        kind: ScoutKind,
        queries: list[str],
        results_count: int,
        summary: str,
        status: str,
        error: str,
    ) -> int:
        async with AsyncSessionLocal() as session:
            row = ScoutHistory(
                user_id=user_id,
                scout_type=kind,
                run_at=datetime.now(timezone.utc),
                queries=json.dumps(queries, ensure_ascii=False),
                results_count=results_count,
                summary=summary,
                send_status=status,
                error=error[:1000],
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row.id
