# Plan projektu: Autonomiczny Agent AI (Telegram)

## Krok 1: Struktura katalogów i Docker
Utwórz strukturę katalogów projektu:
agent/, agent/core/, agent/memory/, agent/autonomy/,
agent/tools/, agent/intelligence/,
agent/safety/, agent/config/, logs/, data/, tests/, scripts/

Utwórz plik agent/__init__.py i wszystkie __init__.py w podkatalogach:
agent/core/__init__.py, agent/memory/__init__.py, agent/autonomy/__init__.py,
agent/tools/__init__.py, agent/intelligence/__init__.py,
agent/safety/__init__.py, agent/config/__init__.py, tests/__init__.py

Utwórz docker-compose.yml z serwisami:
- agent (Python app, build: ., depends_on: postgres, redis, chromadb, restart: unless-stopped, env_file: .env, volumes: ./logs:/app/logs, ./data:/app/data)
- postgres (image: postgres:15, volumes: ./data/postgres:/var/lib/postgresql/data, environment: POSTGRES_DB=agent, POSTGRES_USER=agent, POSTGRES_PASSWORD=agent, restart: unless-stopped)
- redis (image: redis:7-alpine, volumes: ./data/redis:/data, restart: unless-stopped)
- chromadb (image: chromadb/chroma:latest, volumes: ./data/chromadb:/chroma/chroma, ports: "8000:8000", restart: unless-stopped)

Utwórz Dockerfile:
FROM python:3.11-slim
RUN apt-get update && apt-get install -y gcc libpq-dev curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY agent/ /app/agent/
CMD ["python", "-m", "agent.main"]

Utwórz .dockerignore: venv, .venv, .git, __pycache__, *.pyc, data/, logs/, .env, node_modules, *.md

## Krok 2: Zależności Python (requirements.txt)
Utwórz plik requirements.txt — KAŻDA zależność w osobnej linii.
Komentarze TYLKO po znaku # (NIE w nawiasach).

```
anthropic>=0.40.0
langgraph>=0.2.0
python-telegram-bot>=20.0
chromadb>=0.5.0
psycopg2-binary>=2.9.0
redis>=5.0.0                  # redis.asyncio wbudowane
apscheduler>=3.10.0
python-dotenv>=1.0.0
tavily-python>=0.3.0
google-api-python-client>=2.0.0
google-auth-oauthlib>=1.0.0
python-docx>=1.0.0
reportlab>=4.0.0
openpyxl>=3.1.0
pydub>=0.25.0
openai>=1.0.0                 # Whisper API
fastapi>=0.110.0
uvicorn>=0.27.0
pydantic>=2.0.0
pydantic-settings>=2.0.0
aiofiles>=23.0.0
httpx>=0.27.0                 # HTTP client do bunq REST API
pytest>=8.0.0
pytest-asyncio>=0.23.0
pyyaml>=6.0.0                 # permissions.yaml
```

WAŻNE:
- NIE dodawaj sentence-transformers (ChromaDB ma wbudowane embeddingi)
- NIE dodawaj bunq-sdk (przestarzałe, użyjemy httpx + bunq REST API)
- NIE dodawaj aioredis (deprecated, redis>=5.0 ma wbudowane redis.asyncio)

## Krok 3: Konfiguracja (pydantic-settings)
Utwórz agent/config/settings.py.

Import: from pydantic_settings import BaseSettings, SettingsConfigDict

Klasa Settings(BaseSettings):
- anthropic_api_key: str
- telegram_bot_token: str
- telegram_chat_id: str
- postgres_url: str = "postgresql://agent:agent@postgres:5432/agent"
- redis_url: str = "redis://redis:6379/0"
- chromadb_host: str = "chromadb"
- chromadb_port: int = 8000
- tavily_api_key: str = ""
- google_credentials_path: str = "credentials.json"
- openai_api_key: str = ""
- bunq_api_token: str = ""
- bunq_environment: str = "sandbox"
- primary_model: str = "claude-opus-4-6"
- fast_model: str = "claude-sonnet-4-6"
- max_iterations: int = 5
- eval_threshold: float = 0.75
- morning_brief_time: str = "08:00"
- weekly_report_day: str = "monday"
- log_level: str = "INFO"
- audit_log_path: str = "logs/audit.jsonl"

model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

Dodaj cached singleton:
```python
_settings = None
def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
```

Utwórz config.env.example (identyczny z .env ale z pustymi wartościami i komentarzami).

## Krok 4: Bezpieczeństwo — Audit Log i Permission Guard
Utwórz agent/safety/audit_log.py:
Klasa AuditLog:
- __init__(self, log_path: str): ścieżka do pliku JSONL
- async log_action(self, action: str, tool: str, params: dict, result: str, user_id: str) -> str:
  Generuje action_id = str(uuid4()).
  Tworzy dict: {"timestamp": datetime.now(UTC).isoformat(), "action_id": action_id, "action": action, "tool": tool, "params": params, "result": result[:500], "user_id": user_id}
  Otwiera plik w trybie append, zapisuje json.dumps(entry) + "\n".
  Tworzy katalog parents jeśli nie istnieje (Path.mkdir(parents=True, exist_ok=True)).
  CAŁY blok w try/except — nigdy nie rzuca wyjątku, loguje błędy do logging.error.
  Zwraca action_id.
- async get_action(self, action_id: str) -> dict | None:
  Szuka wpisu po action_id w pliku JSONL. Zwraca dict lub None.

Utwórz agent/safety/permission_guard.py:
Klasa PermissionGuard:
- __init__(self, permissions_path: str = "agent/config/permissions.yaml"):
  Wczytuje YAML z trzema listami: autonomous, requires_approval, forbidden.
- can_execute(self, action: str) -> str:
  Zwraca "autonomous", "requires_approval" lub "forbidden".
  Jeśli akcja nie jest w żadnej liście — zwraca "requires_approval" (bezpieczny default).

Utwórz agent/config/permissions.yaml:
```yaml
autonomous:
  - check_email
  - search_web
  - read_calendar
  - read_drive
  - check_balance
  - get_transactions
  - detect_anomalies
  - send_telegram_message
  - create_document
  - morning_brief
  - weekly_report
  - check_and_act
  - research_topic
  - get_contact_brief
  - check_overdue_contacts
  - transcribe_voice

requires_approval:
  - send_email
  - create_event
  - execute_payment
  - upload_file
  - update_relationship
  - write_email_as_user
  - add_new_payment_recipient

forbidden:
  - delete_data
  - change_permissions
  - modify_audit_log
  - disable_safety
  - execute_payment_without_approval
```

## Krok 5: Warstwa pamięci — Working Memory (Redis)
Utwórz agent/memory/working_memory.py.

WAŻNE: Używaj redis.asyncio (wbudowane w pakiet redis>=5.0).
NIE używaj aioredis — jest deprecated i nie działa z Pythonem 3.11+.

```python
from redis.asyncio import Redis
```

Klasa WorkingMemory:
- __init__(self, redis_url: str):
  self.redis = Redis.from_url(redis_url, decode_responses=True)

- async set_context(self, user_id: str, key: str, value: Any, ttl: int = 86400):
  await self.redis.set(f"agent:{user_id}:{key}", json.dumps(value), ex=ttl)

- async get_context(self, user_id: str, key: str) -> Any | None:
  data = await self.redis.get(f"agent:{user_id}:{key}")
  return json.loads(data) if data else None

- async get_session(self, user_id: str) -> dict:
  keys = await self.redis.keys(f"agent:{user_id}:session:*")
  session = {}
  for key in keys:
      field = key.split(":")[-1]
      session[field] = json.loads(await self.redis.get(key))
  return session

- async update_session(self, user_id: str, data: dict):
  for key, value in data.items():
      await self.redis.set(f"agent:{user_id}:session:{key}", json.dumps(value), ex=86400)

- async clear_session(self, user_id: str):
  keys = await self.redis.keys(f"agent:{user_id}:session:*")
  if keys:
      await self.redis.delete(*keys)

- async append_message(self, user_id: str, role: str, content: str):
  msg = json.dumps({"role": role, "content": content, "timestamp": datetime.now(UTC).isoformat()})
  key = f"agent:{user_id}:messages"
  await self.redis.lpush(key, msg)
  await self.redis.ltrim(key, 0, 49)
  await self.redis.expire(key, 86400)

- async get_messages(self, user_id: str, last_n: int = 20) -> list[dict]:
  key = f"agent:{user_id}:messages"
  raw = await self.redis.lrange(key, 0, last_n - 1)
  return [json.loads(m) for m in raw]

- async close(self):
  await self.redis.close()

Każda metoda w try/except — loguj błędy przez logging.error, nigdy nie crashuj.

## Krok 6: Warstwa pamięci — Episodic Memory (ChromaDB)
Utwórz agent/memory/episodic_memory.py.

WAŻNE: Używaj WBUDOWANYCH embeddingów ChromaDB.
NIE instaluj sentence-transformers — ChromaDB domyślnie używa all-MiniLM-L6-v2
przez swój wewnętrzny embedding function.

```python
import chromadb
from uuid import uuid4
```

Klasa EpisodicMemory:
- __init__(self, host: str, port: int):
  self.client = chromadb.HttpClient(host=host, port=port)

- _get_collection(self, user_id: str):
  return self.client.get_or_create_collection(
      name=f"episodes_{user_id}",
      metadata={"hnsw:space": "cosine"}
  )

- async add_episode(self, user_id: str, content: str, metadata: dict):
  collection = self._get_collection(user_id)
  episode_id = f"{user_id}_{uuid4().hex[:12]}"
  metadata["timestamp"] = metadata.get("timestamp", datetime.now(UTC).isoformat())
  metadata.setdefault("episode_type", "conversation")
  clean_meta = {k: str(v) if not isinstance(v, (int, float, bool)) else v for k, v in metadata.items()}
  collection.add(documents=[content], metadatas=[clean_meta], ids=[episode_id])

- async search(self, user_id: str, query: str, n_results: int = 5) -> list[dict]:
  collection = self._get_collection(user_id)
  results = collection.query(query_texts=[query], n_results=n_results)
  episodes = []
  for i in range(len(results["documents"][0])):
      episodes.append({
          "content": results["documents"][0][i],
          "metadata": results["metadatas"][0][i],
          "distance": results["distances"][0][i]
      })
  return episodes

- async get_recent(self, user_id: str, n: int = 10) -> list[dict]:
  collection = self._get_collection(user_id)
  all_data = collection.get(limit=n, include=["documents", "metadatas"])
  episodes = []
  for i in range(len(all_data["documents"])):
      episodes.append({
          "content": all_data["documents"][i],
          "metadata": all_data["metadatas"][i]
      })
  episodes.sort(key=lambda e: e["metadata"].get("timestamp", ""), reverse=True)
  return episodes[:n]

- async add_conversation(self, user_id: str, messages: list):
  combined = "\n".join(f"{m.get('role','user')}: {m.get('content','')}" for m in messages)
  await self.add_episode(user_id, combined, {
      "episode_type": "conversation",
      "message_count": len(messages)
  })

Każda metoda w try/except — loguj błędy, nigdy nie crashuj.

## Krok 7: Warstwa pamięci — Semantic Memory i DB Init
Utwórz agent/memory/db_init.py z funkcją init_database(postgres_url: str):
Łączy się przez psycopg2 i tworzy tabele jeśli nie istnieją:
```sql
CREATE TABLE IF NOT EXISTS user_profiles (
    user_id TEXT PRIMARY KEY,
    profile JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS user_facts (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    category TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    confidence FLOAT DEFAULT 1.0,
    source TEXT DEFAULT 'conversation',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, category, key)
);
CREATE TABLE IF NOT EXISTS relationships (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    person_name TEXT NOT NULL,
    relationship_type TEXT,
    last_contact TIMESTAMP,
    notes TEXT,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_user_facts_user ON user_facts(user_id);
CREATE INDEX IF NOT EXISTS idx_user_facts_category ON user_facts(user_id, category);
CREATE INDEX IF NOT EXISTS idx_relationships_user ON relationships(user_id);
```

Utwórz agent/memory/semantic_memory.py.
Klasa SemanticMemory:
- __init__(self, postgres_url: str): połączenie przez psycopg2
- async get_profile(self, user_id: str) -> dict
- async update_profile(self, user_id: str, updates: dict):
  INSERT ON CONFLICT UPDATE SET profile = profile || updates, updated_at = NOW()
- async add_fact(self, user_id: str, category: str, key: str, value: str, confidence: float = 1.0):
  INSERT ON CONFLICT (user_id, category, key) DO UPDATE SET value, confidence, updated_at
- async get_facts(self, user_id: str, category: str = None) -> list[dict]
- async upsert_relationship(self, user_id: str, person: str, data: dict)
- async get_relationships(self, user_id: str) -> list[dict]
- async get_context_summary(self, user_id: str) -> str:
  Pobiera profil + top 10 faktów + relacje.
  Formatuje jako czytelny string, przycina do max 2000 znaków.
- async close(self): zamyka połączenie

Każda metoda w try/except.

## Krok 8: Memory Router
Utwórz agent/memory/memory_router.py.

Klasa MemoryRouter:
- __init__(self, working: WorkingMemory, episodic: EpisodicMemory, semantic: SemanticMemory, settings: Settings)

- async retrieve_context(self, user_id: str, query: str) -> str:
  1. Pobierz profil z SemanticMemory (zawsze)
  2. Pobierz ostatnie 5 wiadomości z WorkingMemory
  3. Semantic search w EpisodicMemory (top 5 wyników)
  4. Filtruj wyniki episodic po distance < 0.4 (cosine — mniejsze = lepsze)
  5. Złącz w strukturowany string
  6. Przycina do max 12000 znaków (heurystyka: ~3000 tokenów = 12000 znaków)
  Jeśli za długo — przytnij episodic, zachowaj profil i messages.

- async save_interaction(self, user_id: str, messages: list, outcome: str):
  1. Zapisz rozmowę do EpisodicMemory
  2. Wywołaj extract_and_save_facts
  3. Zaktualizuj WorkingMemory (append messages)

- async extract_and_save_facts(self, user_id: str, conversation: str):
  Wywołaj Claude API (fast_model):
  "Wyciągnij fakty o użytkowniku z rozmowy. JSON: [{\"category\": \"...\", \"key\": \"...\", \"value\": \"...\"}]. Kategorie: personal, work, preferences, contacts, projects"
  Parsuj JSON i zapisz do SemanticMemory.
  W except: loguj i pomiń.

## Krok 9: Narzędzie Telegram (interfejs użytkownika)
Utwórz agent/tools/telegram_tool.py.

Klasa TelegramTool:
- __init__(self, bot_token: str, chat_id: str):
  from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
  self.bot = Bot(token=bot_token)
  self.chat_id = chat_id

- async send_message(self, text: str, parse_mode: str = "HTML"):
  Jeśli text > 4096 — podziel na części i wyślij kolejno.
- async send_with_buttons(self, text: str, buttons: list[dict]) -> int:
  Tworzy InlineKeyboardMarkup. Zwraca message_id.
- async wait_for_approval(self, prompt: str, action_id: str, timeout: int = 3600) -> bool:
  Wysyła wiadomość z TAK/NIE. Tworzy asyncio.Future.
  await Future z timeout. Zwraca True/False.
- async transcribe_voice(self, file_id: str) -> str:
  Pobiera audio z Telegram. Wysyła do OpenAI Whisper API.
  Zwraca tekst lub "[błąd transkrypcji]".
- async send_document(self, file_path: str, caption: str = "")
- async send_typing(self): ChatAction.TYPING

Klasa TelegramPoller:
- __init__(self): self.pending_approvals: dict[str, asyncio.Future] = {}
- async start_polling(self, message_handler, callback_handler):
  Używa telegram.ext.Application. Dodaje handlery. Uruchamia polling.
- resolve_approval(self, action_id: str, approved: bool):
  Rozwiązuje Future.

## Krok 10: Narzędzie Web Search (Tavily)
Utwórz agent/tools/web_search_tool.py.

Klasa WebSearchTool:
- __init__(self, api_key: str)
- async search(self, query: str, max_results: int = 5) -> list[dict]:
  Zwraca: [{"title", "url", "content", "score"}]
- async research(self, topic: str) -> str:
  2-3 warianty query. Deduplikacja po URL. Zwraca raport.

## Krok 11: Narzędzie Gmail
Utwórz agent/tools/gmail_tool.py.

Klasa GmailTool:
- __init__(self, credentials_path: str, telegram_tool: TelegramTool)
- async get_unread(self, max_results: int = 10) -> list[dict]
- async get_message(self, message_id: str) -> dict
- async send_email(self, to: str, subject: str, body: str, requires_approval: bool = True) -> bool
- async search_emails(self, query: str, max_results: int = 10) -> list[dict]
- async mark_as_read(self, message_id: str)
Google Gmail API v1 z OAuth2.

## Krok 12: Narzędzie Calendar
Utwórz agent/tools/calendar_tool.py.

Klasa CalendarTool:
- __init__(self, credentials_path: str)
- async get_today_events(self) -> list[dict]
- async get_week_events(self) -> list[dict]
- async create_event(self, title: str, start: datetime, end: datetime, description: str = "") -> dict
- async find_free_slots(self, duration_minutes: int, days_ahead: int = 7) -> list[dict]
Google Calendar API v3.

## Krok 13: Narzędzia Drive i Documents
Utwórz agent/tools/drive_tool.py:
Klasa DriveTool: search_files, get_file_content, create_document, upload_file.
Google Drive API v3.

Utwórz agent/tools/document_tool.py:
Klasa DocumentTool (metody synchroniczne):
- create_docx (python-docx)
- create_pdf (reportlab)
- create_xlsx (openpyxl)
Każda zwraca ścieżkę do pliku.

## Krok 14: Narzędzie finansowe — bunq (REST API)
Utwórz agent/tools/bunq_tool.py.

WAŻNE: NIE używaj bunq-sdk. Używaj httpx do bunq REST API.

Klasa BunqTool:
- __init__(self, api_token: str, environment: str, telegram_tool: TelegramTool, audit_log: AuditLog)
  Base URL: production lub sandbox.
- async get_balance(self) -> dict
- async get_transactions(self, days: int = 30) -> list[dict]
- async get_summary(self, days: int = 30) -> dict
- async prepare_payment(self, amount: float, recipient: str, description: str) -> dict
  NIE wykonuje — tylko tworzy draft.
- async execute_payment(self, payment_draft: dict, approval_id: str) -> bool
  Sprawdza approval_id w AuditLog. Bez approval — odmawia.
- async detect_anomalies(self) -> list[str]
  7 dni vs 30 dni średnia. >150% = anomalia.

KRYTYCZNE: Żaden przelew bez approval_id z AuditLog.

## Krok 15: Tool Router
Utwórz agent/tools/tool_router.py.

Klasa ToolRouter:
- __init__(self, tools: dict, audit_log: AuditLog, settings: Settings)
- get_tools_for_task(self, task_description: str) -> list[str]:
  Keyword matching z fallbackiem na Claude API (fast_model) gdy 0 lub >3 wyniki.
- async execute_tool(self, tool_name: str, method: str, **kwargs) -> Any:
  Loguje do AuditLog. W except zwraca {"error": str(e)}.

## Krok 16: Evaluator — ocena wyników
Utwórz agent/core/evaluator.py.

EvaluationResult (dataclass): score, passed, feedback, missing.

Klasa Evaluator:
- async evaluate(task, result, context) -> EvaluationResult
- async self_critique(response, task) -> str

## Krok 17: Planner — dekompozycja celów
Utwórz agent/core/planner.py.

Step (dataclass): id, action, tool, params, depends_on, status, result.

Klasa Planner:
- async decompose(goal, context) -> list[Step]
- async replan(original_goal, failed_step, feedback, context) -> list[Step]

## Krok 18: Executor — wykonanie kroków planu
Utwórz agent/core/executor.py.

Klasa Executor:
- async execute_step(step, context) -> str
- async execute_plan(steps, context) -> dict

Funkcja topological_sort(steps) — sortuje kroki po depends_on.

## Krok 19: Loop Controller — pętla iteracyjna
Utwórz agent/core/loop_controller.py.

LoopResult (dataclass): final_result, iterations_used, final_score, success.

Klasa LoopController:
- async run_until_satisfied(goal, user_id, max_iterations) -> LoopResult
  Decompose → execute → evaluate → replan jeśli trzeba. Max N iteracji.

## Krok 20: Orchestrator główny (LangGraph)
Utwórz agent/core/orchestrator.py.

Graf LangGraph: IDLE → PERCEIVING → ROUTING → EXECUTING → REPORTING → SAVING.
Plus WAITING_APPROVAL dla operacji wymagających zgody.

AgentState (TypedDict): user_id, message, message_type, task_type, context, plan, result, needs_approval, approval_data, iterations.

async process_message(user_id, message, message_type) — wejście do grafu.

## Krok 21: Initiative Engine — autonomiczna inicjatywa
Utwórz agent/autonomy/initiative_engine.py.

Klasa InitiativeEngine:
- async check_and_act(user_id):
  Zbiera WSZYSTKIE alerty w jedną listę, wysyła JEDNĄ wiadomość (nie osobne).
  Sprawdza: maile, terminy, anomalie finansowe, osoby bez kontaktu.
- async morning_brief(user_id): brief o 8:00
- async weekly_report(user_id): raport w poniedziałek

## Krok 22: Scheduler — zadania cykliczne
Utwórz agent/autonomy/scheduler.py.

Klasa AgentScheduler (APScheduler AsyncIOScheduler):
- morning_brief — cron dziennie
- weekly_report — cron poniedziałek 9:00
- check_and_act — interval 15 min
- start(), stop(), add_reminder()

## Krok 23: Intelligence — User Profiler
Utwórz agent/intelligence/user_profiler.py.

Klasa UserProfiler:
- async update_from_conversation(user_id, messages): ekstrakcja faktów przez Claude API
- async get_profile_summary(user_id) -> str

## Krok 24: Intelligence — Business Analyst
Utwórz agent/intelligence/business_analyst.py.

Klasa BusinessAnalyst:
- async generate_weekly_kpi(user_id) -> str (HTML)
- async detect_anomalies(user_id) -> list[str]

## Krok 25: Intelligence — Relationship Manager
Utwórz agent/intelligence/relationship_manager.py.

Klasa RelationshipManager:
- async get_contact_brief(user_id, person_name) -> str
- async check_overdue_contacts(user_id, days_threshold=14) -> list[str]
- async update_after_meeting(user_id, person, notes)

## Krok 26: Intelligence — Decision Advisor
Utwórz agent/intelligence/decision_advisor.py.

Klasa DecisionAdvisor:
- async analyze_decision(user_id, decision) -> str (HTML)

## Krok 27: System prompt agenta
Utwórz agent/config/system_prompt.py.

build_system_prompt(user_id, profile_summary, current_datetime) -> str
Dynamiczny prompt: tożsamość, profil usera, czas, zasady, styl, format HTML.

## Krok 28: Onboarding — pierwsze uruchomienie
Utwórz agent/core/onboarding.py.

Klasa Onboarding:
- async check_if_needed(user_id) -> bool: profil pusty = potrzebny
- async run_onboarding(user_id): seria pytań (imię, branża, timezone, priorytety)
  Zapisuje do SemanticMemory.

## Krok 29: Ghostwriter — pisanie w stylu użytkownika
Utwórz agent/intelligence/ghostwriter.py.

Klasa Ghostwriter:
- async learn_style(user_id, sample_emails)
- async write_email(user_id, recipient, subject, key_points) -> str
- async write_post(user_id, topic, platform="linkedin") -> str

## Krok 30: Główna aplikacja — połączenie wszystkiego
Utwórz agent/main.py.

Klasa AgentApp:
- async initialize(): inicjalizuj WSZYSTKIE komponenty, każdy w try/except
  Wywołaj db_init.init_database() na starcie.
- async handle_message(update, context): rozróżnij tekst/głos/dokument, onboarding check
- async handle_callback(update, context): resolve pending_approvals
- async shutdown(): graceful shutdown (SIGTERM/SIGINT), zamknij połączenia, wyślij "🔴 Agent zatrzymany."
- async run(): initialize → signal handlers → scheduler.start → telegram polling

if __name__ == "__main__": asyncio.run(AgentApp().run())

## Krok 31: Testy — pamięć
Utwórz tests/test_memory.py.

Testy pytest + pytest-asyncio:
- test_working_memory_set_get
- test_working_memory_messages
- test_working_memory_clear
- test_episodic_memory_add_search
- test_semantic_memory_profile
- test_semantic_memory_facts
- test_memory_router_context

Mockuj Redis i PostgreSQL (AsyncMock). ChromaDB z in-memory client.

## Krok 32: Testy — narzędzia i evaluator
Utwórz tests/test_tools.py, tests/test_evaluator.py, tests/test_loop.py.

Testy: permission_guard, audit_log, tool_router keywords, evaluator score, loop iteracji.
Mockuj zewnętrzne API.

## Krok 33: Healthcheck (FastAPI)
Utwórz agent/healthcheck.py.

check_all_systems() -> dict: sprawdza Redis, Postgres, ChromaDB, Telegram, Anthropic.
FastAPI GET /health endpoint.
Uruchom uvicorn w daemon thread w main.py.

## Krok 34: Dokumentacja i setup script
Utwórz README.md: opis, wymagania, instalacja, konfiguracja, architektura, bezpieczeństwo, troubleshooting.
Utwórz scripts/setup.sh: sprawdza Docker, tworzy katalogi, kopiuje .env, docker-compose up -d.

## Krok 35: Finalizacja — sprawdzenie importów
Sprawdź WSZYSTKIE importy we wszystkich plikach .py.
Uruchom: python -c "from agent.config.settings import get_settings; ..." (pełna lista importów).
Jeśli coś nie działa — napraw.
Uruchom: pytest tests/ -v --tb=short.
Jeśli testy przechodzą — gotowe.
