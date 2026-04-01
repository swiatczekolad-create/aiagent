# Autonomiczny Agent AI — Kontekst Projektu

## Cel projektu
Budujemy autonomicznego agenta AI który działa na własnym serwerze VPS.
Komunikuje się z użytkownikiem WYŁĄCZNIE przez Telegram Bot.
Poznaje użytkownika głębiej z każdą interakcją.
Działa proaktywnie — sam inicjuje działania bez oczekiwania na polecenie.
Iteruje swoje działania w pętli aż osiągnie satysfakcjonujący wynik.
Informuje użytkownika co robi i po co — ale nie czeka na zgodę przy rutynowych zadaniach.

## Interfejs użytkownika
**TELEGRAM ONLY** — jedyny kanał komunikacji.
- Bot API: python-telegram-bot 20.x (async)
- Polling (nie webhook) dla prostoty deploymentu
- Obsługa tekstu, głosu (transkrypcja Whisper), dokumentów, zdjęć
- Inline buttons dla decyzji wymagających zgody
- Agent sam pisze do użytkownika gdy coś ważnego — bez limitu czasowego

## Stack technologiczny
- Python 3.11+
- LangGraph 0.2+ — graf stanów agenta (orchestrator)
- Anthropic SDK — claude-opus-4-6 (złożone), claude-sonnet-4-6 (rutynowe)
- ChromaDB — vector store (pamięć epizodyczna) z WBUDOWANYMI embeddingami (NIE sentence-transformers)
- PostgreSQL 15 + psycopg2 — pamięć semantyczna (profil usera)
- Redis 7 + redis.asyncio (wbudowane w redis>=5.0, NIE aioredis) — pamięć robocza
- APScheduler 3.x — zadania cykliczne
- httpx — HTTP client do bunq REST API i innych integracji (NIE bunq-sdk — przestarzałe)
- Tavily API — web search
- Google API Python Client — Gmail, Calendar, Drive
- python-docx, reportlab, openpyxl — tworzenie dokumentów
- FastAPI — opcjonalny endpoint healthcheck
- Docker + docker-compose — konteneryzacja
- pydantic-settings — konfiguracja z .env

## Architektura pamięci (4 warstwy)
1. ROBOCZA (Redis): kontekst bieżącej sesji, TTL 24h, via redis.asyncio
2. EPIZODYCZNA (ChromaDB): każda rozmowa jako wektor, wbudowane embeddingi ChromaDB
3. SEMANTYCZNA (PostgreSQL): profil użytkownika jako JSONB, tabele user_facts, relationships
4. PROCEDURALNA (YAML/JSON): zasady działania agenta, preferencje, permissions.yaml

## Zasady autonomii
DZIAŁA SAM (bez pytania):
- Monitoring maili, kalendarza, finansów
- Poranny brief o 8:00
- Tygodniowy raport w poniedziałek
- Research i wywiad rynkowy
- Przypomnienia i follow-upy
- Alerty anomalii

PYTA O ZGODĘ (inline button TAK/NIE):
- Każdy przelew wychodzący
- Ważne maile do klientów zewnętrznych
- Nowy odbiorca płatności
- Decyzje strategiczne

NIGDY SAM:
- Transakcje bez potwierdzenia
- Usuwanie danych
- Zmiany uprawnień

## Pętla iteracyjna agenta
Agent NIE kończy po pierwszej próbie.
Loop Controller sprawdza wynik przez Evaluator.
Jeśli score < threshold — refinuje plan i próbuje ponownie.
Maksymalnie 5 iteracji na zadanie.
User widzi tylko efekt końcowy + informację ile iteracji zajęło.

## Konwencje kodu
- Async/await wszędzie (asyncio) dla operacji I/O
- Type hints obowiązkowe w każdej funkcji
- Każda funkcja ma docstring
- Logowanie przez Python logging (nie print)
- Błędy obsługiwane gracefully — agent nigdy nie crashuje
- Każda akcja logowana do audit.jsonl (JSONL, nie JSON)
- Zmienne środowiskowe z .env przez pydantic-settings (nigdy hardcoded)
- Graceful shutdown (obsługa SIGTERM/SIGINT)
- Tokeny liczone heurystyką: len(text) // 4 ≈ ilość tokenów

## Struktura plików
(auto-aktualizowana przez orchestrator)

## Aktualny stan
Projekt nowy, krok 0/35.
