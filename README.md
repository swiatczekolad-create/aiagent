# Orchestrator — Autonomiczny Agent AI

System który buduje autonomicznego agenta AI krok po kroku używając Claude Code CLI jako executora i Anthropic API jako reviewera. Użytkownik dostaje raporty i steruje procesem przez Telegram.

## Wymagania

- Python 3.11+
- Claude Code CLI (`npm install -g @anthropic-ai/claude-code`)
- Konto Anthropic z API key
- Bot Telegram (utwórz przez @BotFather)
- Serwer VPS lub lokalny komputer

## Instalacja

```bash
# 1. Sklonuj / skopiuj pliki orchestratora
cd ~/orchestrator

# 2. Utwórz środowisko wirtualne
python3 -m venv venv
source venv/bin/activate

# 3. Zainstaluj zależności
pip install anthropic requests

# 4. Skonfiguruj
cp config.env.example config.env
nano config.env  # Uzupełnij tokeny
```

## Konfiguracja (config.env)

| Zmienna | Opis | Wymagana | Domyślnie |
|---------|------|----------|-----------|
| `ANTHROPIC_API_KEY` | Klucz API Anthropic | ✅ | — |
| `TELEGRAM_BOT_TOKEN` | Token bota z @BotFather | ✅ | — |
| `TELEGRAM_CHAT_ID` | Twoje Chat ID (z @userinfobot) | ✅ | — |
| `REVIEWER_MODEL` | Model do oceny kroków | ❌ | claude-sonnet-4-6 |
| `MAX_REVIEW_ITERATIONS` | Max poprawek na krok | ❌ | 3 |
| `INTERACTIVE_MODE` | Czekaj na zgodę po każdym kroku | ❌ | true |
| `STEP_TIMEOUT` | Timeout Claude Code (sekundy) | ❌ | 300 |
| `DECISION_TIMEOUT` | Timeout na decyzję usera (sekundy) | ❌ | 7200 |

## Użycie

### 1. Inicjalizacja projektu

```bash
python3 orchestrator.py init ~/agent-project
```

### 2. Skopiuj gotowe pliki projektu agenta

```bash
cp plan.md ~/agent-project/plan.md
cp CLAUDE.md ~/agent-project/CLAUDE.md
```

### 3. Uruchom budowanie

```bash
python3 orchestrator.py run ~/agent-project
```

Orchestrator:
- Wykonuje każdy krok przez Claude Code CLI
- Ocenia wynik przez Anthropic API (reviewer)
- Wysyła raport na Telegram po każdym kroku
- W trybie interaktywnym pyta Cię przez Telegram czy kontynuować
- Automatycznie poprawia błędy (max 3 iteracje)
- Aktualizuje CLAUDE.md i PROGRESS.md po każdym kroku

### 4. Tryb autonomiczny (auto-dekompozycja)

```bash
python3 orchestrator.py auto ~/agent-project
```

Dla dużych projektów (>10 kroków). Automatycznie dzieli plan na moduły i buduje je sekwencyjnie.

### 5. Inne komendy

```bash
python3 orchestrator.py status ~/agent-project   # pokaż stan
python3 orchestrator.py reset ~/agent-project     # resetuj (zacznij od nowa)
```

## Sterowanie przez Telegram

| Przycisk | Akcja |
|---------|-------|
| ▶️ Dalej | Przejdź do następnego kroku |
| 🔧 Popraw | Opisz co poprawić — executor poprawi |
| ⏭️ Pomiń następny | Pomiń kolejny krok |
| 🛑 Stop | Wstrzymaj projekt |
| 🔄 Spróbuj ponownie | Przy błędzie — powtórz krok |
| 💬 Dam instrukcje | Napisz własne instrukcje naprawcze |

## Struktura plików

```
~/orchestrator/
├── orchestrator.py          # Główny skrypt
├── reviewer_prompt.py       # System prompt dla reviewera
├── config.env               # Twoja konfiguracja (nie commituj!)
├── config.env.example       # Przykładowa konfiguracja
├── CLAUDE.md                # Kontekst projektu agenta
├── plan.md                  # Plan budowy agenta (35 kroków)
├── templates/
│   ├── plan-template.md     # Szablon planu (dla init)
│   └── CLAUDE-template.md   # Szablon kontekstu (dla init)
├── skills/                  # Opcjonalne skille (frontend, pdf, docx)
└── README.md                # Ten plik
```

## Jak działa review

Każdy krok jest oceniany przez Claude API. Reviewer sprawdza:

Dla kodu Python:
- Czy jest async/await
- Czy ma type hints
- Czy obsługuje błędy (try/except)
- Czy zmienne z .env (nie hardcoded)
- Czy transakcje finansowe wymagają zgody
- Czy używa redis.asyncio (nie aioredis)
- Czy ChromaDB bez sentence-transformers

Dla plików konfiguracyjnych (Docker, YAML, requirements.txt):
- Poprawna składnia
- Spójność z resztą projektu
- Brak sekretów w commitowanych plikach

Verdicts: APPROVED, NEEDS_FIX (auto-poprawka), NEEDS_DECISION (pytanie do usera).

## Kluczowe decyzje techniczne

1. **redis.asyncio** zamiast aioredis — aioredis jest deprecated od 2023
2. **Wbudowane embeddingi ChromaDB** zamiast sentence-transformers — oszczędza 2GB+ (PyTorch)
3. **httpx + bunq REST API** zamiast bunq-sdk — SDK przestarzałe, nie działa z Python 3.11+
4. **pydantic-settings** do konfiguracji — type-safe, walidacja na starcie
5. **JSONL** (nie JSON) dla audit log — append-friendly, łatwy do parsowania

## Troubleshooting

**Claude Code nie znaleziony:**
```bash
npm install -g @anthropic-ai/claude-code
claude --version
```

**Telegram nie odbiera wiadomości:**
- Sprawdź TELEGRAM_CHAT_ID (użyj @userinfobot)
- Upewnij się że napisałeś do bota /start

**Rate limit Anthropic:**
- Orchestrator automatycznie retry 3x z exponential backoff
- Sprawdź limity: console.anthropic.com

**Krok się zawiesza (timeout):**
- Domyślny timeout: 300s (5 min)
- Zwiększ STEP_TIMEOUT w config.env
- Rozważ podzielenie kroku na mniejsze

**Reviewer ciągle odrzuca krok:**
- Po 3 próbach pyta Cię przez Telegram
- Możesz: pominąć krok, dać instrukcje, zatrzymać
