# MIGRACJA: Agent + n8n — Kompletna instrukcja

## Co robimy

Przenosimy WSZYSTKIE integracje z zewnętrznymi serwisami z kodu agenta do n8n.
Agent zostaje mózgiem (pamięć, myślenie, Telegram). n8n zostaje rękami (Gmail, Calendar, Drive, bunq, wszystko inne).

---

## FAZA 1: Postawienie n8n na serwerze

### 1.1 Dodaj n8n do docker-compose.yml

W istniejącym docker-compose.yml dodaj serwis:

```yaml
  n8n:
    image: n8nio/n8n:latest
    ports:
      - "5678:5678"
    environment:
      - N8N_BASIC_AUTH_ACTIVE=true
      - N8N_BASIC_AUTH_USER=admin
      - N8N_BASIC_AUTH_PASSWORD=ZMIEN_NA_SILNE_HASLO
      - N8N_HOST=0.0.0.0
      - N8N_PORT=5678
      - N8N_PROTOCOL=http
      - WEBHOOK_URL=http://n8n:5678/
      - N8N_COMMUNITY_PACKAGES_ALLOW_TOOL_USAGE=true
    volumes:
      - ./data/n8n:/home/node/.n8n
    restart: unless-stopped
```

### 1.2 Uruchom n8n

```bash
mkdir -p data/n8n
docker-compose up -d n8n
```

### 1.3 Zainstaluj community node bunq w n8n

Wejdź na http://TWOJ_SERWER:5678
Settings → Community Nodes → Install → wpisz: n8n-nodes-bunq-test → Install

### 1.4 Dodaj N8N_WEBHOOK_URL do settings agenta

W agent/config/settings.py dodaj:
```python
n8n_webhook_base: str = "http://n8n:5678/webhook/"
```

W .env dodaj:
```
N8N_WEBHOOK_BASE=http://n8n:5678/webhook/
```

---

## FAZA 2: Tworzenie workflow w n8n

Każda integracja to osobny workflow w n8n z webhook triggerem.
Agent wysyła HTTP POST na webhook → n8n robi robotę → zwraca JSON.

### 2.1 Workflow: bunq-get-balance

W n8n utwórz nowy workflow:
1. Node: Webhook (POST, path: bunq-get-balance)
2. Node: bunq → Get Monetary Accounts
3. Node: Respond to Webhook (zwróć JSON z saldem)

Testowy URL: POST http://n8n:5678/webhook/bunq-get-balance
Agent wywołuje ten URL → dostaje saldo → pokazuje użytkownikowi.

### 2.2 Workflow: bunq-get-transactions

1. Webhook (POST, path: bunq-get-transactions, body: {"days": 30})
2. bunq → List Payments (filtruj po dacie z body)
3. Respond to Webhook (JSON z transakcjami)

### 2.3 Workflow: bunq-prepare-payment

1. Webhook (POST, path: bunq-prepare-payment, body: {"amount", "recipient", "description", "iban"})
2. bunq → Create Payment (draft)
3. Respond to Webhook (JSON z potwierdzeniem)

### 2.4 Workflow: gmail-check

1. Webhook (POST, path: gmail-check, body: {"max_results": 5})
2. Gmail → Get Many Messages (unread, limit z body)
3. Respond to Webhook (JSON z listą maili)

### 2.5 Workflow: gmail-send

1. Webhook (POST, path: gmail-send, body: {"to", "subject", "body"})
2. Gmail → Send Message
3. Respond to Webhook (JSON z potwierdzeniem)

### 2.6 Workflow: gmail-search

1. Webhook (POST, path: gmail-search, body: {"query", "max_results"})
2. Gmail → Get Many Messages (query z body)
3. Respond to Webhook (JSON z wynikami)

### 2.7 Workflow: calendar-today

1. Webhook (POST, path: calendar-today)
2. Google Calendar → Get Many Events (today)
3. Respond to Webhook (JSON z eventami)

### 2.8 Workflow: calendar-week

1. Webhook (POST, path: calendar-week)
2. Google Calendar → Get Many Events (7 dni)
3. Respond to Webhook (JSON z eventami)

### 2.9 Workflow: calendar-create

1. Webhook (POST, path: calendar-create, body: {"title", "date", "time", "duration_minutes"})
2. Google Calendar → Create Event
3. Respond to Webhook (JSON z potwierdzeniem)

### 2.10 Workflow: drive-search

1. Webhook (POST, path: drive-search, body: {"query"})
2. Google Drive → Search Files
3. Respond to Webhook (JSON z plikami)

### 2.11 Workflow: drive-upload

1. Webhook (POST, path: drive-upload, body: {"file_path", "folder"})
2. Google Drive → Upload File
3. Respond to Webhook (JSON z linkiem)

---

## FAZA 3: Jeden tool w agencie — call_n8n

### 3.1 Utwórz agent/tools/n8n_tool.py

```python
"""
Jedyny tool do komunikacji z n8n.
Zastępuje: gmail_tool.py, calendar_tool.py, drive_tool.py, bunq_tool.py
"""
import httpx
import logging
from agent.config.settings import get_settings

log = logging.getLogger(__name__)


class N8nTool:
    def __init__(self, webhook_base: str = None):
        settings = get_settings()
        self.base_url = webhook_base or settings.n8n_webhook_base

    async def call(self, workflow: str, params: dict = None) -> dict:
        """
        Wywołaj workflow n8n.
        
        workflow: nazwa webhooka (np. "bunq-get-balance")
        params: parametry do przekazania (body JSON)
        
        Zwraca dict z wynikiem lub {"error": "opis błędu"}
        """
        url = f"{self.base_url}{workflow}"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=params or {})
                if response.status_code == 200:
                    return response.json()
                else:
                    log.error(f"n8n error: {response.status_code} {response.text[:200]}")
                    return {"error": f"n8n zwrócił błąd {response.status_code}"}
        except httpx.TimeoutException:
            return {"error": "n8n timeout — workflow trwa za długo"}
        except Exception as e:
            log.error(f"n8n call error: {e}")
            return {"error": f"Nie mogę połączyć się z n8n: {str(e)[:200]}"}
```

### 3.2 Nowe definicje narzędzi w agent/core/tools.py

Zamień WSZYSTKIE narzędzia Gmail/Calendar/Drive/bunq na wersje n8n.
Zamiast osobnych definicji per akcja — generyczne wywołanie n8n:

```python
TOOLS = [
    # === NATYWNE (w kodzie agenta) ===
    {
        "name": "web_search",
        "description": "Szukaj informacji w internecie. Używaj gdy potrzebujesz aktualnych danych, faktów, cen, informacji o firmach. Działaj AUTONOMICZNIE.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Zapytanie do wyszukiwarki"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "set_reminder",
        "description": "Ustaw przypomnienie. Użytkownik dostanie wiadomość na Telegramie o podanej godzinie. Działaj AUTONOMICZNIE — nie pytaj o zgodę.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Treść przypomnienia"},
                "minutes_from_now": {"type": "integer", "description": "Za ile minut wysłać"}
            },
            "required": ["text", "minutes_from_now"]
        }
    },
    {
        "name": "create_document",
        "description": "Utwórz dokument PDF, DOCX lub XLSX. Wyślij użytkownikowi. Działaj AUTONOMICZNIE.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "content": {"type": "string"},
                "format": {"type": "string", "enum": ["pdf", "docx", "xlsx"], "default": "pdf"}
            },
            "required": ["title", "content"]
        }
    },
    {
        "name": "submit_tool_request",
        "description": "Użytkownik potrzebuje czegoś czego nie umiesz. Przekaż prośbę do admina.",
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "Czego użytkownik potrzebuje"}
            },
            "required": ["description"]
        }
    },

    # === PRZEZ N8N (integracje zewnętrzne) ===
    {
        "name": "check_email",
        "description": "Sprawdź nieodczytane maile użytkownika. Wymaga podłączonego Gmaila w n8n. Działaj AUTONOMICZNIE.",
        "input_schema": {
            "type": "object",
            "properties": {
                "max_results": {"type": "integer", "default": 5}
            },
            "required": []
        }
    },
    {
        "name": "send_email",
        "description": "Wyślij email. ZAWSZE najpierw pokaż draft użytkownikowi i CZEKAJ na zatwierdzenie. Wymaga podłączonego Gmaila w n8n.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"}
            },
            "required": ["to", "subject", "body"]
        }
    },
    {
        "name": "search_email",
        "description": "Szukaj w mailach użytkownika. Wymaga podłączonego Gmaila w n8n. Działaj AUTONOMICZNIE.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "default": 5}
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_calendar",
        "description": "Pobierz spotkania z kalendarza. Wymaga podłączonego Google Calendar w n8n. Działaj AUTONOMICZNIE.",
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {"type": "string", "enum": ["today", "tomorrow", "week"]}
            },
            "required": ["period"]
        }
    },
    {
        "name": "create_event",
        "description": "Utwórz wydarzenie w kalendarzu. Pokaż szczegóły i CZEKAJ na zatwierdzenie. Wymaga Google Calendar w n8n.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "date": {"type": "string", "description": "YYYY-MM-DD"},
                "time": {"type": "string", "description": "HH:MM"},
                "duration_minutes": {"type": "integer", "default": 60}
            },
            "required": ["title", "date", "time"]
        }
    },
    {
        "name": "get_balance",
        "description": "Sprawdź saldo konta bankowego. Wymaga podłączonego bunq w n8n. Działaj AUTONOMICZNIE.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "get_transactions",
        "description": "Pobierz transakcje z konta. Wymaga podłączonego bunq w n8n. Działaj AUTONOMICZNIE.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "default": 30}
            },
            "required": []
        }
    },
    {
        "name": "prepare_payment",
        "description": "Przygotuj przelew. ZAWSZE pokaż kwotę, odbiorcę, opis i CZEKAJ na zatwierdzenie TAK/NIE. Wymaga bunq w n8n.",
        "input_schema": {
            "type": "object",
            "properties": {
                "amount": {"type": "number"},
                "recipient": {"type": "string"},
                "iban": {"type": "string"},
                "description": {"type": "string"}
            },
            "required": ["amount", "recipient", "description"]
        }
    },
    {
        "name": "search_drive",
        "description": "Szukaj plików na Google Drive. Wymaga podłączonego Drive w n8n. Działaj AUTONOMICZNIE.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "upload_to_drive",
        "description": "Wrzuć plik na Google Drive. Wymaga podłączonego Drive w n8n. Działaj AUTONOMICZNIE.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "folder": {"type": "string", "default": "Agent AI"}
            },
            "required": ["file_path"]
        }
    }
]


# Mapowanie: nazwa narzędzia → workflow n8n
N8N_TOOL_MAPPING = {
    "check_email": "gmail-check",
    "send_email": "gmail-send",
    "search_email": "gmail-search",
    "get_calendar": "calendar-today",  # orchestrator zmienia na calendar-week jeśli period=week
    "create_event": "calendar-create",
    "get_balance": "bunq-get-balance",
    "get_transactions": "bunq-get-transactions",
    "prepare_payment": "bunq-prepare-payment",
    "search_drive": "drive-search",
    "upload_to_drive": "drive-upload",
}

# Narzędzia natywne (w kodzie agenta, nie przez n8n)
NATIVE_TOOLS = {"web_search", "set_reminder", "create_document", "submit_tool_request"}


def get_available_tools(connected_services: list[str], capabilities: dict) -> list[dict]:
    """Zwraca TYLKO narzędzia które faktycznie działają."""
    always_available = {"web_search", "set_reminder", "create_document", "submit_tool_request"}

    service_tools = {
        "gmail": {"check_email", "send_email", "search_email"},
        "calendar": {"get_calendar", "create_event"},
        "drive": {"search_drive", "upload_to_drive"},
        "bunq": {"get_balance", "get_transactions", "prepare_payment"},
    }

    available_names = set(always_available)
    for service in connected_services:
        available_names.update(service_tools.get(service, set()))

    return [t for t in TOOLS if t["name"] in available_names]
```

### 3.3 Nowy tool_executor.py

Zamień cały agent/core/tool_executor.py:

```python
"""
Tool Executor — wykonuje narzędzia agenta.
Natywne narzędzia wykonuje lokalnie.
Integracje zewnętrzne deleguje do n8n.
"""
import logging
from agent.tools.n8n_tool import N8nTool
from agent.core.tools import N8N_TOOL_MAPPING, NATIVE_TOOLS

log = logging.getLogger(__name__)


class ToolExecutor:
    def __init__(self, web_search, telegram_tool, document_tool,
                 scheduler, semantic_memory, tool_request_manager,
                 audit_log, n8n_tool: N8nTool):
        self.web_search = web_search
        self.telegram_tool = telegram_tool
        self.document_tool = document_tool
        self.scheduler = scheduler
        self.semantic_memory = semantic_memory
        self.tool_request_manager = tool_request_manager
        self.audit_log = audit_log
        self.n8n = n8n_tool

    async def execute(self, tool_name: str, tool_input: dict,
                      user_id: str, message_id: int = None) -> str:
        """Wykonaj narzędzie. Zwróć wynik jako string."""
        try:
            # Natywne narzędzia
            if tool_name in NATIVE_TOOLS:
                result = await self._execute_native(tool_name, tool_input, user_id, message_id)
            # Integracje przez n8n
            elif tool_name in N8N_TOOL_MAPPING:
                workflow = N8N_TOOL_MAPPING[tool_name]
                # Specjalna logika dla calendar period
                if tool_name == "get_calendar" and tool_input.get("period") == "week":
                    workflow = "calendar-week"
                result = await self.n8n.call(workflow, tool_input)
                result = str(result)
            else:
                result = f"Nieznane narzędzie: {tool_name}"

            # Loguj
            await self.audit_log.log_action(
                action=tool_name, tool=tool_name,
                params=tool_input, result=str(result)[:500],
                user_id=user_id
            )
            return str(result)

        except Exception as e:
            log.error(f"Tool execution error: {tool_name}: {e}")
            return f"Błąd narzędzia {tool_name}: {str(e)[:200]}"

    async def _execute_native(self, tool_name: str, tool_input: dict,
                               user_id: str, message_id: int = None) -> str:
        if tool_name == "web_search":
            results = await self.web_search.search(tool_input["query"])
            if not results:
                return "Brak wyników wyszukiwania."
            return "\n\n".join(
                f"{r['title']}\n{r['url']}\n{r.get('content', '')[:300]}"
                for r in results[:5]
            )

        elif tool_name == "set_reminder":
            from datetime import datetime, timedelta, timezone
            run_date = datetime.now(timezone.utc) + timedelta(minutes=tool_input["minutes_from_now"])
            chat_id = user_id.split("_", 1)[1] if "_" in user_id else user_id
            self.scheduler.add_reminder(user_id, tool_input["text"], run_date)
            return f"Przypomnienie ustawione za {tool_input['minutes_from_now']} minut: {tool_input['text']}"

        elif tool_name == "create_document":
            fmt = tool_input.get("format", "pdf")
            title = tool_input["title"]
            content = tool_input["content"]
            filename = f"{title.replace(' ', '-')}.{fmt}"
            output_path = f"/tmp/{filename}"
            if fmt == "pdf":
                self.document_tool.create_pdf(title, content, output_path)
            elif fmt == "docx":
                self.document_tool.create_docx(title, content, output_path)
            elif fmt == "xlsx":
                self.document_tool.create_xlsx([{"content": content}], title, output_path)
            await self.telegram_tool.send_document(output_path, caption=title)
            return f"Dokument {filename} utworzony i wysłany."

        elif tool_name == "submit_tool_request":
            await self.tool_request_manager.submit_request(
                user_id, tool_input["description"],
                tool_input.get("example", "")
            )
            return "Prośba przekazana do admina."

        return f"Nieobsługiwane narzędzie natywne: {tool_name}"
```

---

## FAZA 4: Usunięcie starego kodu

### 4.1 Pliki do USUNIĘCIA (przenieś do archiwum jeśli chcesz zachować):

```
agent/tools/gmail_tool.py          ← zastąpiony przez n8n workflow
agent/tools/calendar_tool.py       ← zastąpiony przez n8n workflow
agent/tools/drive_tool.py          ← zastąpiony przez n8n workflow
agent/tools/bunq_tool.py           ← zastąpiony przez n8n workflow
agent/tools/tool_router.py         ← zastąpiony przez native tool use
agent/tools/oauth_manager.py       ← OAuth robi n8n, nie agent
agent/core/service_connector.py    ← niepotrzebny, n8n ma swoje credentials
agent/core/api_key_connector.py    ← klucze podaje się w n8n, nie w Telegram
agent/core/planner.py              ← Claude planuje sam z native tool use
agent/core/executor.py             ← zastąpiony przez tool_executor.py
agent/core/loop_controller.py      ← Claude iteruje sam z tool use loop
```

### 4.2 Pliki do ZACHOWANIA bez zmian:

```
agent/memory/working_memory.py     ← pamięć robocza
agent/memory/episodic_memory.py    ← pamięć epizodyczna
agent/memory/semantic_memory.py    ← pamięć semantyczna
agent/memory/memory_router.py      ← router pamięci
agent/memory/db_init.py            ← inicjalizacja bazy
agent/safety/audit_log.py          ← logowanie akcji
agent/safety/permission_guard.py   ← uprawnienia
agent/safety/auth_manager.py       ← multi-tenant auth
agent/autonomy/scheduler.py        ← cron jobs, remindery
agent/autonomy/initiative_engine.py ← morning brief, monitoring
agent/intelligence/user_profiler.py
agent/intelligence/business_analyst.py
agent/intelligence/relationship_manager.py
agent/intelligence/decision_advisor.py
agent/intelligence/ghostwriter.py
agent/config/settings.py           ← konfiguracja
agent/config/system_prompt.py      ← system prompt
agent/config/permissions.yaml      ← uprawnienia
agent/tools/web_search_tool.py     ← web search (natywny)
agent/tools/document_tool.py       ← tworzenie dokumentów (natywny)
agent/tools/telegram_tool.py       ← Telegram (natywny)
```

### 4.3 Pliki do ZMODYFIKOWANIA:

```
agent/core/orchestrator.py         ← przebudowa na native tool use loop
agent/core/tools.py                ← nowe definicje z N8N_TOOL_MAPPING
agent/core/tool_executor.py        ← nowy, natywne + n8n dispatch
agent/main.py                      ← nowy init (bez gmail/calendar/drive/bunq tools)
agent/config/settings.py           ← dodaj n8n_webhook_base
docker-compose.yml                 ← dodaj serwis n8n
.env                               ← dodaj N8N_WEBHOOK_BASE
requirements.txt                   ← usuń google-api-python-client, google-auth-oauthlib, bunq-sdk
```

---

## FAZA 5: Przebudowa orchestratora (native tool use)

### 5.1 Nowy agent/core/orchestrator.py

```python
"""
Orchestrator agenta — native tool use loop.
Claude sam decyduje które narzędzie wywołać.
"""
import anthropic
import logging
from datetime import datetime, timezone
from agent.core.tools import get_available_tools
from agent.config.settings import get_settings

log = logging.getLogger(__name__)


class AgentOrchestrator:
    def __init__(self, settings, memory_router, tool_executor,
                 telegram_tool, auth_manager):
        self.client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        self.model = settings.thinking_model
        self.memory_router = memory_router
        self.tool_executor = tool_executor
        self.telegram_tool = telegram_tool
        self.auth_manager = auth_manager
        self.settings = settings

    async def process_message(self, user_id: str, message: str,
                               message_type: str = "text",
                               message_id: int = None):
        """Główna pętla agenta."""
        from agent.config.system_prompt import build_system_prompt

        # 1. Kontekst i profil
        context = await self.memory_router.retrieve_context(user_id, message)
        profile = await self.memory_router.semantic.get_profile(user_id)
        connected_services = self._get_connected_services(user_id)
        capabilities = {}  # z capabilities.json
        available_tools = get_available_tools(connected_services, capabilities)

        # 2. System prompt
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M %Z")
        system = build_system_prompt(
            agent_name=profile.get("agent_name"),
            user_name=profile.get("user_name") or profile.get("first_name"),
            profile_summary=context,
            current_datetime=now,
            connected_services=connected_services,
            available_services=["gmail", "calendar", "drive", "bunq"],
            is_onboarded=profile.get("onboarding_completed", False),
        )

        # 3. Historia rozmowy
        recent = await self.memory_router.working_memory.get_messages(user_id, last_n=20)
        conversation = [{"role": m["role"], "content": m["content"]} for m in reversed(recent)]
        conversation.append({"role": "user", "content": message})

        # 4. Agent loop — max 10 iteracji tool use
        final_response = "Przepraszam, coś poszło nie tak."
        total_tokens = 0

        for iteration in range(10):
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=2000,
                system=system,
                tools=available_tools,
                messages=conversation,
            )

            # Token tracking
            total_tokens += response.usage.input_tokens + response.usage.output_tokens

            if response.stop_reason == "tool_use":
                # Claude chce użyć narzędzia
                tool_block = next(b for b in response.content if b.type == "tool_use")

                log.info(f"TOOL USE: {tool_block.name}({tool_block.input})")

                # Wykonaj narzędzie
                result = await self.tool_executor.execute(
                    tool_block.name, tool_block.input, user_id, message_id
                )

                # Dodaj do konwersacji
                conversation.append({"role": "assistant", "content": response.content})
                conversation.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": tool_block.id,
                        "content": result
                    }]
                })
                # Kontynuuj loop

            elif response.stop_reason == "end_turn":
                # Claude skończył
                text_blocks = [b.text for b in response.content if hasattr(b, "text")]
                final_response = "\n".join(text_blocks)
                await self.telegram_tool.send_message(final_response)
                break

        # 5. Zapisz do pamięci
        await self.memory_router.working_memory.append_message(user_id, "user", message)
        await self.memory_router.working_memory.append_message(user_id, "assistant", final_response)

        # 6. Token tracking
        try:
            await self.auth_manager.track_token_usage(user_id, total_tokens)
        except Exception:
            pass

        # 7. Ekstrakcja faktów (w tle, nie blokuje)
        try:
            await self.memory_router.save_interaction(
                user_id,
                [{"role": "user", "content": message}, {"role": "assistant", "content": final_response}],
                final_response
            )
        except Exception as e:
            log.warning(f"Fact extraction error: {e}")

    def _get_connected_services(self, user_id: str) -> list[str]:
        """Sprawdź jakie usługi user ma w n8n (na razie z capabilities.json)."""
        # TODO: dynamicznie sprawdzaj z n8n które workflow istnieją
        # Na razie: czytaj z capabilities.json lub z profilu usera
        return []
```

---

## FAZA 6: Przebudowa main.py

### 6.1 Usuń importy starych narzędzi, dodaj nowe:

```python
# USUŃ:
# from agent.tools.gmail_tool import GmailTool
# from agent.tools.calendar_tool import CalendarTool
# from agent.tools.drive_tool import DriveTool
# from agent.tools.bunq_tool import BunqTool
# from agent.tools.tool_router import ToolRouter
# from agent.core.planner import Planner
# from agent.core.executor import Executor
# from agent.core.loop_controller import LoopController

# DODAJ:
from agent.tools.n8n_tool import N8nTool
from agent.core.tool_executor import ToolExecutor
from agent.core.orchestrator import AgentOrchestrator
```

### 6.2 W initialize() zamień sekcję narzędzi:

```python
# STARE (usuń):
# self.gmail_tool = GmailTool(...)
# self.calendar_tool = CalendarTool(...)
# self.drive_tool = DriveTool(...)
# self.bunq_tool = BunqTool(...)
# self.tool_router = ToolRouter(...)
# self.planner = Planner(...)
# self.executor = Executor(...)
# self.loop_controller = LoopController(...)

# NOWE:
self.n8n_tool = N8nTool()
self.tool_executor = ToolExecutor(
    web_search=self.web_search,
    telegram_tool=self.telegram_tool,
    document_tool=self.document_tool,
    scheduler=self.scheduler,
    semantic_memory=self.semantic_memory,
    tool_request_manager=self.tool_request_manager,
    audit_log=self.audit_log,
    n8n_tool=self.n8n_tool,
)
self.orchestrator = AgentOrchestrator(
    settings=self.settings,
    memory_router=self.memory_router,
    tool_executor=self.tool_executor,
    telegram_tool=self.telegram_tool,
    auth_manager=self.auth_manager,
)
```

### 6.3 Usuń z requirements.txt:

```
google-api-python-client>=2.0.0
google-auth-oauthlib>=1.0.0
```

Credentials do Google (Gmail, Calendar, Drive) podajesz BEZPOŚREDNIO w n8n, nie w agencie.

---

## FAZA 7: Podłączanie usług przez n8n

### Jak użytkownik podłącza Gmail:
1. Użytkownik mówi na Telegramie: "podłącz Gmail"
2. Agent: "Żeby podłączyć Gmaila, admin musi skonfigurować połączenie. Przekazuję prośbę."
3. Admin (Ty) wchodzi do n8n → Credentials → Google OAuth → loguje się kontem usera
4. Admin aktywuje workflow gmail-check, gmail-send, gmail-search
5. Admin aktualizuje capabilities.json → gmail: available
6. Agent: "Gmail połączony! Mogę teraz sprawdzać Twoją pocztę."

### Jak użytkownik podłącza bunq:
1. Użytkownik: "podłącz bunq"
2. Agent: "Żeby podłączyć bunq, admin musi skonfigurować połączenie. Przekazuję prośbę."
3. Admin wchodzi do n8n → Credentials → bunq → wpisuje klucz API usera
4. Admin aktywuje workflow bunq-get-balance, bunq-get-transactions
5. Admin aktualizuje capabilities.json → bunq: available
6. Agent: "bunq połączony!"

### W przyszłości (multi-tenant n8n):
Każdy user ma swoje credentials w n8n, agent przekazuje user_id w webhook body,
n8n wybiera credentials per user. Ale to na PÓŹNIEJ — na start admin konfiguruje ręcznie.

---

## FAZA 8: Test po migracji

Po wykonaniu wszystkich kroków przetestuj:

1. `docker-compose up -d` — wszystkie kontenery startują (agent + postgres + redis + chromadb + n8n)
2. Na Telegramie: "cześć" → agent odpowiada normalnie
3. Na Telegramie: "przypomnij mi za 2 minuty test" → reminder przychodzi
4. Na Telegramie: "co to jest Fixly" → web search działa
5. Na Telegramie: "sprawdź mój gmail" → agent mówi "Gmail nie jest jeszcze podłączony" (bo workflow nie skonfigurowany)
6. Skonfiguruj jeden workflow w n8n (np. bunq-get-balance) → przetestuj z Telegrama

---

## Podsumowanie

PRZED (nie działało):
- 11 plików narzędzi w kodzie agenta
- Osobna implementacja per API
- OAuth manager, service connector, api key connector
- ToolRouter z keyword matching
- Planner + Executor + LoopController

PO (działa):
- 1 plik n8n_tool.py (HTTP POST na webhook)
- 1 plik tool_executor.py (dispatch natywne vs n8n)
- 1 plik tools.py (definicje dla Claude)
- Nowy orchestrator z native tool use
- n8n jako kontener Docker obok agenta
- Dodanie nowej integracji = nowy workflow w n8n (10 min, zero kodu)
