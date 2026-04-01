#!/usr/bin/env python3
"""
Orchestrator: Claude Code (executor) + Anthropic API (reviewer) + Telegram (powiadomienia)

Użycie:
  python3 orchestrator.py init <project_dir>    # inicjalizuj nowy projekt
  python3 orchestrator.py run <project_dir>     # uruchom/kontynuuj wykonanie
  python3 orchestrator.py status <project_dir>  # pokaż stan
  python3 orchestrator.py reset <project_dir>   # resetuj stan
  python3 orchestrator.py auto <project_dir>    # tryb autonomiczny z auto-dekompozycją
"""

import os
import sys
import json
import re
import subprocess
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from glob import glob

import anthropic
import requests

# ──────────────────────────────────────────────
# Logowanie
# ──────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Konfiguracja
# ──────────────────────────────────────────────

_config_cache = None


def load_config() -> dict:
    """Wczytuje konfigurację z config.env (z cache)."""
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    config = {}
    env_path = Path(__file__).parent / "config.env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    config[key.strip()] = value.strip()

    # Defaults
    config.setdefault("REVIEWER_MODEL", "claude-sonnet-4-6")
    config.setdefault("MAX_REVIEW_ITERATIONS", "3")
    config.setdefault("INTERACTIVE_MODE", "true")
    config.setdefault("STEP_TIMEOUT", "300")
    config.setdefault("DECISION_TIMEOUT", "7200")

    # Konwersje typów
    config["MAX_REVIEW_ITERATIONS"] = int(config["MAX_REVIEW_ITERATIONS"])
    config["STEP_TIMEOUT"] = int(config["STEP_TIMEOUT"])
    config["DECISION_TIMEOUT"] = int(config["DECISION_TIMEOUT"])

    # Walidacja wymaganych kluczy
    required = ["ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
    missing = [k for k in required if not config.get(k)]
    if missing:
        log.error(f"Brakuje wymaganych zmiennych w config.env: {missing}")
        sys.exit(1)

    _config_cache = config
    return config


def reload_config():
    """Wymusza ponowne wczytanie konfiguracji."""
    global _config_cache
    _config_cache = None
    return load_config()


# ──────────────────────────────────────────────
# Telegram Bot
# ──────────────────────────────────────────────

class TelegramBot:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = str(chat_id)
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.last_update_id = 0
        self._polling = False

    def send(self, text: str, reply_markup: dict = None) -> dict:
        """Wysyła wiadomość tekstową."""
        # Telegram limit: 4096 znaków
        if len(text) > 4096:
            text = text[:4090] + "\n(...)"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML"
        }
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)
        try:
            resp = requests.post(f"{self.base_url}/sendMessage", json=payload, timeout=15)
            return resp.json()
        except Exception as e:
            log.error(f"Telegram send error: {e}")
            return {}

    def send_with_buttons(self, text: str, buttons: list) -> dict:
        """Wysyła wiadomość z przyciskami inline."""
        reply_markup = {"inline_keyboard": [[btn] for btn in buttons]}
        return self.send(text, reply_markup=reply_markup)

    def wait_for_response(self, timeout: int = 3600) -> str:
        """Czeka na odpowiedź użytkownika. Zwraca tekst lub '__TIMEOUT__'."""
        self._polling = True
        deadline = time.time() + timeout

        while time.time() < deadline and self._polling:
            try:
                updates = requests.get(
                    f"{self.base_url}/getUpdates",
                    params={"offset": self.last_update_id + 1, "timeout": 30},
                    timeout=40
                ).json()

                for update in updates.get("result", []):
                    self.last_update_id = update["update_id"]

                    # Callback z inline buttona
                    if "callback_query" in update:
                        cb = update["callback_query"]
                        if str(cb["message"]["chat"]["id"]) == self.chat_id:
                            requests.post(
                                f"{self.base_url}/answerCallbackQuery",
                                json={"callback_query_id": cb["id"]},
                                timeout=10
                            )
                            self._polling = False
                            return cb["data"]

                    # Zwykła wiadomość tekstowa
                    if "message" in update:
                        msg = update["message"]
                        if str(msg["chat"]["id"]) == self.chat_id:
                            # Voice message — fallback
                            if "voice" in msg:
                                self._polling = False
                                return msg.get("text", "[wiadomość głosowa — transkrypcja niedostępna w orchestratorze]")
                            self._polling = False
                            return msg.get("text", "")

            except requests.exceptions.RequestException as e:
                log.warning(f"Telegram polling error: {e}")
                time.sleep(5)

        return "__TIMEOUT__"


# ──────────────────────────────────────────────
# Stan projektu
# ──────────────────────────────────────────────

def load_state(project_dir: str) -> dict:
    """Wczytuje stan projektu."""
    state_path = Path(project_dir) / "state.json"
    if state_path.exists():
        with open(state_path) as f:
            return json.load(f)
    return {"current_step": 0, "total_steps": 0, "status": "initialized", "history": []}


def save_state(project_dir: str, current_step: int, status: str, history: list = None):
    """Zapisuje stan projektu."""
    state_path = Path(project_dir) / "state.json"
    state = load_state(project_dir)
    state["current_step"] = current_step
    state["status"] = status
    if history is not None:
        state["history"] = history
    with open(state_path, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def read_file(path: str) -> str:
    """Bezpieczne czytanie pliku."""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def write_file(path: str, content: str):
    """Bezpieczny zapis pliku."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


# ──────────────────────────────────────────────
# Parser planu
# ──────────────────────────────────────────────

def parse_plan(plan_path: str) -> list:
    """Parsuje plan.md — zwraca listę kroków."""
    content = read_file(plan_path)
    if not content:
        return []

    steps = []
    current_step = None
    current_lines = []

    for line in content.split("\n"):
        match = re.match(r"^##\s+Krok\s+(\d+):\s+(.+)$", line)
        if match:
            if current_step:
                current_step["description"] = "\n".join(current_lines).strip()
                steps.append(current_step)
            current_step = {
                "number": int(match.group(1)),
                "title": match.group(2).strip(),
            }
            current_lines = []
        elif current_step:
            current_lines.append(line)

    if current_step:
        current_step["description"] = "\n".join(current_lines).strip()
        steps.append(current_step)

    return steps


# ──────────────────────────────────────────────
# Skill detection
# ──────────────────────────────────────────────

SKILL_KEYWORDS = {
    "frontend-design": ["landing page", "website", "ui", "interfejs", "dashboard", "strona", "html", "css", "frontend"],
    "canvas-design": ["plakat", "grafika", "poster", "logo", "visual", "wizualny", "png", "obraz"],
    "web-app-builder": ["react", "tailwind", "shadcn", "aplikacja web", "webapp", "komponent"],
    "docx": ["dokument word", "docx", "raport word"],
    "pdf": ["pdf", "formularz pdf"],
    "pptx": ["prezentacja", "slajd", "deck", "pptx"],
    "xlsx": ["arkusz", "excel", "xlsx", "tabela excel", "csv"],
}

SKILLS_DIR = Path(__file__).parent / "skills"


def detect_skills(step_description: str) -> list:
    """Wykryj które skille są potrzebne dla danego kroku."""
    desc_lower = step_description.lower()
    matched = []
    for skill_name, keywords in SKILL_KEYWORDS.items():
        if any(kw in desc_lower for kw in keywords):
            matched.append(skill_name)
    return matched


def load_skills(skill_names: list) -> str:
    """Załaduj treść dopasowanych skillów."""
    content = ""
    for name in skill_names:
        skill_path = SKILLS_DIR / f"{name}.md"
        if skill_path.exists():
            content += f"\n\n--- SKILL: {name} ---\n"
            content += skill_path.read_text(encoding="utf-8")
    return content


# ──────────────────────────────────────────────
# Executor (Claude Code CLI)
# ──────────────────────────────────────────────

def get_last_progress(project_dir: str, n: int = 3) -> str:
    """Zwraca ostatnie N wpisów z PROGRESS.md."""
    progress = read_file(f"{project_dir}/PROGRESS.md")
    if not progress.strip():
        return "Brak historii — to pierwszy krok."
    entries = [e.strip() for e in progress.split("\n---\n") if e.strip()]
    last = entries[-n:] if len(entries) >= n else entries
    return "\n---\n".join(last)


def execute_step(project_dir: str, step: dict, config: dict,
                 extra_instructions: str = "") -> str:
    """Uruchamia Claude Code CLI dla danego kroku."""
    claude_md = read_file(f"{project_dir}/CLAUDE.md")
    progress = get_last_progress(project_dir)
    total = load_state(project_dir).get("total_steps", "?")

    # Detekcja i ładowanie skillów
    skills = detect_skills(step["description"])
    skill_content = load_skills(skills) if skills else ""

    extra = ""
    if extra_instructions:
        extra = f"\n\nDODATKOWE INSTRUKCJE:\n{extra_instructions}"
    if skill_content:
        extra += f"\n\nUMIEJĘTNOŚCI SPECJALNE:{skill_content}"

    prompt = f"""KONTEKST PROJEKTU (CLAUDE.md):
{claude_md}

DOTYCHCZASOWY POSTĘP (ostatnie 3 kroki):
{progress}

AKTUALNY KROK {step['number']}/{total}: {step['title']}
{step['description']}
{extra}

INSTRUKCJE:
- Pracuj TYLKO nad tym krokiem
- Nie modyfikuj plików niezwiązanych z tym krokiem
- Użyj async/await, type hints, logging (nie print) — tylko w plikach .py
- Po wykonaniu podaj: co zrobiłeś, jakie pliki utworzyłeś/zmodyfikowałeś, uwagi
"""

    timeout = config["STEP_TIMEOUT"]

    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "json"],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=project_dir
        )

        if result.returncode != 0:
            return f"BŁĄD (exit {result.returncode}):\n{result.stderr[:2000]}"

        try:
            data = json.loads(result.stdout)
            return data.get("result", result.stdout)
        except json.JSONDecodeError:
            return result.stdout

    except subprocess.TimeoutExpired:
        raise TimeoutError(f"Krok {step['number']} przekroczył timeout {timeout}s")
    except FileNotFoundError:
        return "BŁĄD: Nie znaleziono polecenia 'claude'. Zainstaluj Claude Code CLI: npm install -g @anthropic-ai/claude-code"


# ──────────────────────────────────────────────
# Reviewer (Anthropic API)
# ──────────────────────────────────────────────

def review_step(project_dir: str, step: dict, executor_output: str, config: dict) -> dict:
    """Ocenia wynik kroku przez Anthropic API."""
    from reviewer_prompt import build_reviewer_prompt

    client = anthropic.Anthropic(api_key=config["ANTHROPIC_API_KEY"])

    claude_md = read_file(f"{project_dir}/CLAUDE.md")
    plan_md = read_file(f"{project_dir}/plan.md")

    prompt = build_reviewer_prompt(
        claude_md=claude_md,
        plan_md=plan_md,
        step_number=step["number"],
        step_title=step["title"],
        step_description=step["description"],
        executor_output=executor_output
    )

    # Retry z exponential backoff
    for attempt in range(3):
        try:
            response = client.messages.create(
                model=config["REVIEWER_MODEL"],
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}]
            )
            text = response.content[0].text
            return parse_review_response(text)
        except anthropic.RateLimitError:
            wait = 5 * (3 ** attempt)
            log.warning(f"Rate limit — czekam {wait}s...")
            time.sleep(wait)
        except Exception as e:
            log.error(f"Reviewer error: {e}")
            time.sleep(5)

    return {
        "verdict": "NEEDS_FIX",
        "summary": "Reviewer niedostępny po 3 próbach",
        "feedback": "Sprawdź API key i limity, potem spróbuj ponownie.",
        "question": "",
        "options": []
    }


def parse_review_response(text: str) -> dict:
    """Parsuje odpowiedź reviewera."""
    result = {
        "verdict": "NEEDS_FIX",
        "summary": "",
        "feedback": "",
        "question": "",
        "options": []
    }

    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("VERDICT:"):
            verdict = line.split(":", 1)[1].strip()
            if verdict in ("APPROVED", "NEEDS_FIX", "NEEDS_DECISION"):
                result["verdict"] = verdict
        elif line.startswith("SUMMARY:"):
            result["summary"] = line.split(":", 1)[1].strip()
        elif line.startswith("FEEDBACK:"):
            result["feedback"] = line.split(":", 1)[1].strip()
        elif line.startswith("QUESTION:"):
            result["question"] = line.split(":", 1)[1].strip()
        elif line.startswith("OPTIONS:"):
            opts = line.split(":", 1)[1].strip()
            result["options"] = [o.strip() for o in opts.split("|") if o.strip()]

    return result


# ──────────────────────────────────────────────
# Fix step
# ──────────────────────────────────────────────

def fix_step(project_dir: str, step: dict, feedback: str, config: dict) -> str:
    """Prosi Claude Code o poprawienie kroku."""
    return execute_step(
        project_dir, step, config,
        extra_instructions=f"FEEDBACK OD REVIEWERA — POPRAW:\n{feedback}"
    )


# ──────────────────────────────────────────────
# Progress i CLAUDE.md aktualizacja
# ──────────────────────────────────────────────

def update_progress(project_dir: str, step: dict, verdict: str,
                    summary: str, iterations: int):
    """Dopisuje wpis do PROGRESS.md."""
    entry = f"""## Krok {step['number']}: {step['title']}
**Data:** {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
**Status:** {verdict} (iteracje: {iterations})

### Co zrobiono:
{summary}

---
"""
    progress_path = f"{project_dir}/PROGRESS.md"
    existing = read_file(progress_path)
    write_file(progress_path, existing + entry)


def update_claude_md(project_dir: str, step: dict, summary: str, total: int):
    """Aktualizuje sekcje 'Aktualny stan' i 'Struktura plików' w CLAUDE.md."""
    claude_md = read_file(f"{project_dir}/CLAUDE.md")

    # Następny krok
    plan = parse_plan(f"{project_dir}/plan.md")
    next_step = next((s for s in plan if s["number"] == step["number"] + 1), None)
    next_info = f"Krok {step['number'] + 1}: {next_step['title']}" if next_step else "KONIEC"

    new_state = f"""## Aktualny stan
Ukończono krok {step['number']}/{total}: {step['title']}
Następny: {next_info}

Ostatnie zmiany:
{summary}
"""

    # Podmień sekcję "Aktualny stan"
    claude_md = _replace_section(claude_md, "Aktualny stan", new_state)
    write_file(f"{project_dir}/CLAUDE.md", claude_md)

    # Aktualizuj drzewo plików
    update_file_tree(project_dir)


def _replace_section(content: str, section_name: str, new_section: str) -> str:
    """Podmienia sekcję ## w markdown."""
    marker = f"## {section_name}"
    if marker in content:
        parts = content.split(marker)
        old_rest = parts[1]
        # Szukaj następnej sekcji ##
        next_section = re.search(r"\n## ", old_rest)
        if next_section:
            after = old_rest[next_section.start():]
            return parts[0] + new_section + after
        else:
            return parts[0] + new_section
    else:
        return content + "\n" + new_section


def update_file_tree(project_dir: str):
    """Aktualizuje sekcję 'Struktura plików' w CLAUDE.md."""
    try:
        result = subprocess.run(
            ["find", ".", "-type", "f",
             "-not", "-path", "./.git/*",
             "-not", "-path", "./node_modules/*",
             "-not", "-path", "./__pycache__/*",
             "-not", "-path", "./venv/*",
             "-not", "-path", "./.venv/*",
             "-not", "-path", "./data/*",
             "-not", "-path", "./logs/*",
             "-not", "-path", "./PROGRESS.md",
             "-not", "-path", "./state.json",
             "-not", "-path", "./.env",
             "-not", "-name", "*.pyc"],
            capture_output=True, text=True, cwd=project_dir, timeout=10
        )
        tree = result.stdout.strip()
        if not tree:
            return

        new_section = f"## Struktura plików\n```\n{tree}\n```\n"

        claude_md = read_file(f"{project_dir}/CLAUDE.md")
        claude_md = _replace_section(claude_md, "Struktura plików", new_section)
        write_file(f"{project_dir}/CLAUDE.md", claude_md)
    except Exception as e:
        log.warning(f"update_file_tree error: {e}")


# ──────────────────────────────────────────────
# Telegram komunikacja
# ──────────────────────────────────────────────

def report_step(bot: TelegramBot, project_name: str, step_num: int, total: int,
                title: str, verdict: str, summary: str, iterations: int):
    """Raport po kroku."""
    emoji = {"APPROVED": "✅", "NEEDS_FIX": "🔧", "SKIPPED": "⏭️", "STOPPED": "🛑"}.get(verdict, "❓")
    text = (
        f"<b>📋 {project_name}</b>\n\n"
        f"<b>Krok {step_num}/{total}: {title}</b>\n"
        f"Status: {emoji} {verdict} (iteracje: {iterations})\n\n"
        f"<b>Podsumowanie:</b>\n{summary[:1500]}"
    )
    if verdict == "APPROVED":
        if step_num < total:
            text += f"\n\n➡️ Przechodzę do kroku {step_num + 1}..."
        else:
            text += "\n\n🎉 To był ostatni krok! Projekt zakończony."
    bot.send(text)


def report_error(bot: TelegramBot, project_name: str, step_num: int,
                 title: str, error_msg: str) -> tuple:
    """Raport błędu z przyciskami akcji."""
    text = (
        f"<b>📋 {project_name}</b>\n\n"
        f"<b>🛑 BŁĄD w kroku {step_num}: {title}</b>\n\n"
        f"<code>{error_msg[:1000]}</code>\n\n"
        f"Co robimy?"
    )
    buttons = [
        {"text": "🔄 Spróbuj ponownie", "callback_data": "retry"},
        {"text": "⏭️ Pomiń ten krok", "callback_data": "skip"},
        {"text": "🛑 Zatrzymaj projekt", "callback_data": "stop"},
        {"text": "💬 Dam instrukcje", "callback_data": "freetext"},
    ]
    bot.send_with_buttons(text, buttons)
    response = bot.wait_for_response()
    if response == "freetext":
        bot.send("💬 Napisz instrukcje:")
        instructions = bot.wait_for_response()
        return ("fix_with_instructions", instructions)
    if response == "__TIMEOUT__":
        return ("stop", None)
    return (response, None)


def ask_user_confirmation(bot: TelegramBot, project_name: str,
                          step_num: int, total: int, title: str,
                          summary: str) -> tuple:
    """Pyta usera co dalej (tryb interaktywny)."""
    text = (
        f"<b>📋 {project_name}</b>\n\n"
        f"<b>✅ Krok {step_num}/{total}: {title}</b>\n\n"
        f"<b>Podsumowanie:</b>\n{summary[:1500]}\n\n"
        f"Co robimy?"
    )
    buttons = [
        {"text": "▶️ Dalej", "callback_data": "continue"},
        {"text": "🔧 Popraw", "callback_data": "fix"},
        {"text": "⏭️ Pomiń następny", "callback_data": "skip"},
        {"text": "🛑 Stop", "callback_data": "stop"},
    ]
    bot.send_with_buttons(text, buttons)
    response = bot.wait_for_response()

    if response == "fix":
        bot.send("💬 Opisz co poprawić:")
        feedback = bot.wait_for_response()
        return ("fix", feedback)
    elif response == "skip":
        return ("skip", None)
    elif response == "stop":
        return ("stop", None)
    elif response == "continue":
        return ("continue", None)
    elif response == "__TIMEOUT__":
        return ("stop", None)
    else:
        # User napisał tekst zamiast kliknąć — traktuj jako feedback
        return ("fix", response)


def handle_needs_decision(bot: TelegramBot, project_name: str, step_num: int,
                          title: str, summary: str, question: str,
                          options: list, config: dict) -> str | None:
    """Obsługa NEEDS_DECISION od reviewera."""
    text = (
        f"<b>📋 {project_name}</b>\n\n"
        f"<b>Krok {step_num}: {title}</b>\n"
        f"Status: 🤔 WYMAGA DECYZJI\n\n"
        f"<b>Sytuacja:</b>\n{summary}\n\n"
        f"<b>Pytanie:</b>\n{question}"
    )
    buttons = []
    for i, opt in enumerate(options):
        buttons.append({"text": f"{chr(65+i)}) {opt}", "callback_data": f"option_{i}"})
    buttons.append({"text": "💬 Odpowiem tekstem", "callback_data": "freetext"})
    bot.send_with_buttons(text, buttons)

    response = bot.wait_for_response(timeout=config["DECISION_TIMEOUT"])

    if response == "__TIMEOUT__":
        bot.send("⏰ Timeout — wstrzymuję projekt.")
        return None

    if response == "freetext":
        bot.send("💬 Napisz swoją odpowiedź:")
        response = bot.wait_for_response()

    if response.startswith("option_"):
        idx = int(response.split("_")[1])
        if idx < len(options):
            response = options[idx]

    bot.send(f"👍 Przyjąłem: <b>{response}</b>\nKontynuuję...")
    return response


# ──────────────────────────────────────────────
# Główna pętla
# ──────────────────────────────────────────────

def run(project_dir: str):
    """Uruchamia lub wznawia wykonanie projektu."""
    config = load_config()
    bot = TelegramBot(config["TELEGRAM_BOT_TOKEN"], config["TELEGRAM_CHAT_ID"])
    interactive = config.get("INTERACTIVE_MODE", "true").lower() == "true"
    project_name = Path(project_dir).name

    plan = parse_plan(f"{project_dir}/plan.md")
    if not plan:
        bot.send("❌ Nie znaleziono kroków w plan.md")
        log.error("Brak kroków w plan.md")
        return

    state = load_state(project_dir)
    state["total_steps"] = len(plan)
    save_state(project_dir, state["current_step"], "running", state.get("history", []))

    bot.send(
        f"<b>🚀 Projekt: {project_name}</b>\n"
        f"Kroków: {len(plan)}\n"
        f"Tryb: {'interaktywny' if interactive else 'automatyczny'}\n"
        f"Start od kroku: {state['current_step'] + 1}"
    )

    history = state.get("history", [])
    start_from = state["current_step"]

    for step in plan[start_from:]:
        log.info(f"--- Krok {step['number']}/{len(plan)}: {step['title']} ---")
        bot.send(f"⚙️ Wykonuję krok {step['number']}/{len(plan)}: <b>{step['title']}</b>...")

        # === EXECUTE ===
        try:
            result = execute_step(project_dir, step, config)
        except TimeoutError as e:
            action, data = report_error(bot, project_name, step["number"], step["title"], str(e))
            if action == "retry":
                try:
                    result = execute_step(project_dir, step, config)
                except TimeoutError:
                    bot.send("🛑 Ponowny timeout. Zatrzymuję.")
                    save_state(project_dir, step["number"] - 1, "stopped", history)
                    return
            elif action == "skip":
                update_progress(project_dir, step, "SKIPPED", "Pominięto (timeout)", 0)
                continue
            elif action == "stop":
                save_state(project_dir, step["number"] - 1, "stopped", history)
                return
            elif action == "fix_with_instructions":
                try:
                    result = execute_step(project_dir, step, config, extra_instructions=data)
                except TimeoutError:
                    bot.send("🛑 Timeout po instrukcjach. Zatrzymuję.")
                    save_state(project_dir, step["number"] - 1, "stopped", history)
                    return

        # === REVIEW LOOP ===
        review = review_step(project_dir, step, result, config)
        iterations = 1
        max_iter = config["MAX_REVIEW_ITERATIONS"]

        while review["verdict"] == "NEEDS_FIX" and iterations < max_iter:
            bot.send(
                f"🔧 Krok {step['number']}: wymaga poprawek ({iterations}/{max_iter})\n"
                f"<i>{review['summary'][:500]}</i>"
            )
            result = fix_step(project_dir, step, review["feedback"], config)
            review = review_step(project_dir, step, result, config)
            iterations += 1

        # === NEEDS_DECISION ===
        if review["verdict"] == "NEEDS_DECISION":
            decision = handle_needs_decision(
                bot, project_name, step["number"], step["title"],
                review["summary"], review.get("question", "Brak pytania"),
                review.get("options", ["Opcja A", "Opcja B"]),
                config
            )
            if decision is None:
                save_state(project_dir, step["number"] - 1, "waiting_decision", history)
                return
            result = execute_step(
                project_dir, step, config,
                extra_instructions=f"Decyzja użytkownika: {decision}"
            )
            review = review_step(project_dir, step, result, config)
            iterations += 1

        # === STILL NEEDS_FIX after max iterations ===
        if review["verdict"] == "NEEDS_FIX":
            action, data = report_error(
                bot, project_name, step["number"], step["title"],
                f"Po {iterations} poprawkach nadal nie OK.\n\n{review['feedback'][:500]}"
            )
            if action == "skip":
                update_progress(project_dir, step, "SKIPPED", review["summary"], iterations)
                history.append({
                    "step": step["number"], "title": step["title"],
                    "verdict": "SKIPPED", "iterations": iterations,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
                save_state(project_dir, step["number"], "running", history)
                continue
            elif action == "stop":
                save_state(project_dir, step["number"] - 1, "stopped", history)
                return
            elif action == "retry":
                result = execute_step(project_dir, step, config)
                review = review_step(project_dir, step, result, config)
                iterations += 1
            elif action == "fix_with_instructions":
                result = execute_step(project_dir, step, config, extra_instructions=data)
                review = review_step(project_dir, step, result, config)
                iterations += 1

        # === APPROVED (or best effort) ===
        report_step(bot, project_name, step["number"], len(plan),
                    step["title"], review["verdict"], review["summary"], iterations)
        update_progress(project_dir, step, review["verdict"], review["summary"], iterations)
        update_claude_md(project_dir, step, review["summary"], len(plan))

        history.append({
            "step": step["number"],
            "title": step["title"],
            "verdict": review["verdict"],
            "iterations": iterations,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        save_state(project_dir, step["number"], "running", history)

        # === Interaktywny tryb ===
        if interactive and step["number"] < len(plan):
            action, data = ask_user_confirmation(
                bot, project_name, step["number"], len(plan),
                step["title"], review["summary"]
            )
            if action == "fix":
                result = execute_step(project_dir, step, config, extra_instructions=data)
                review = review_step(project_dir, step, result, config)
                report_step(bot, project_name, step["number"], len(plan),
                            step["title"], review["verdict"], review["summary"], iterations + 1)
                update_progress(project_dir, step, review["verdict"], review["summary"], iterations + 1)
            elif action == "skip":
                # Pomiń NASTĘPNY krok
                next_step_idx = step["number"]  # bo plan jest 0-indexed, step.number jest 1-indexed
                if next_step_idx < len(plan):
                    skipped = plan[next_step_idx]
                    update_progress(project_dir, skipped, "SKIPPED", "Pominięto przez użytkownika", 0)
                    history.append({
                        "step": skipped["number"], "title": skipped["title"],
                        "verdict": "SKIPPED", "iterations": 0,
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    })
                    save_state(project_dir, skipped["number"], "running", history)
                continue
            elif action == "stop":
                save_state(project_dir, step["number"], "stopped", history)
                bot.send(f"🛑 Projekt wstrzymany.\nWznów: <code>python3 orchestrator.py run {project_dir}</code>")
                return

    # === KONIEC ===
    save_state(project_dir, len(plan), "completed", history)
    approved = len([h for h in history if h.get("verdict") == "APPROVED"])
    skipped = len([h for h in history if h.get("verdict") == "SKIPPED"])
    bot.send(
        f"🎉 <b>Projekt {project_name} zakończony!</b>\n"
        f"Wszystkie {len(plan)} kroków wykonane.\n\n"
        f"✅ APPROVED: {approved}\n⏭️ SKIPPED: {skipped}"
    )


# ──────────────────────────────────────────────
# Tryb autonomiczny (auto-dekompozycja)
# ──────────────────────────────────────────────

def auto_decompose(project_dir: str, config: dict) -> dict:
    """Faza 1: Analiza i dekompozycja projektu przez Anthropic API."""
    client = anthropic.Anthropic(api_key=config["ANTHROPIC_API_KEY"])

    claude_md = read_file(f"{project_dir}/CLAUDE.md")
    plan_md = read_file(f"{project_dir}/plan.md")

    prompt = f"""Jesteś architektem oprogramowania. Dostajesz plan projektu do zbudowania.

PLAN PROJEKTU:
{plan_md[:6000]}

KONTEKST:
{claude_md[:3000]}

Twoim zadaniem jest podzielić ten projekt na NIEZALEŻNE MODUŁY, które mogą być budowane osobno.

Zasady dekompozycji:
1. Każdy moduł musi być samodzielny — daje się zbudować i przetestować bez innych modułów
2. Moduł powinien mieć max 5-7 kroków (żeby zmieścić się w kontekście)
3. Zdefiniuj INTERFEJSY między modułami
4. Zaplanuj KOLEJNOŚĆ — które moduły muszą być najpierw
5. Jeden moduł to np.: "backend API", "baza danych + migracje", "frontend", "auth", "testy"

Odpowiedz TYLKO w JSON:
{{
  "modules": [
    {{
      "id": "m1",
      "name": "Nazwa modułu",
      "description": "Co ten moduł robi",
      "depends_on": [],
      "steps": [
        {{"number": 1, "title": "...", "description": "..."}}
      ],
      "interfaces": {{
        "exports": ["co ten moduł produkuje"],
        "imports": ["co ten moduł potrzebuje z innych modułów"]
      }},
      "context_notes": "Dodatkowe informacje"
    }}
  ],
  "execution_order": [["m1", "m2"], ["m3"], ["m4"]],
  "integration_steps": [
    {{"title": "...", "description": "Co zrobić żeby połączyć moduły"}}
  ]
}}"""

    response = client.messages.create(
        model=config["REVIEWER_MODEL"],
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}]
    )

    text = response.content[0].text

    # Wyciągnij JSON z odpowiedzi
    json_match = re.search(r'\{[\s\S]*\}', text)
    if json_match:
        return json.loads(json_match.group())
    else:
        raise ValueError("Reviewer nie zwrócił poprawnego JSON dekompozycji")


def prepare_modules(project_dir: str, decomposition: dict):
    """Faza 2: Tworzenie subprojektów z dekompozycji."""
    main_claude_md = read_file(f"{project_dir}/CLAUDE.md")

    # Wyciągnij sekcje Stack i Konwencje z głównego CLAUDE.md
    stack_section = ""
    conventions_section = ""
    for section_name, target in [("Stack technologiczny", "stack"), ("Konwencje", "conventions")]:
        marker = f"## {section_name}"
        if marker in main_claude_md:
            parts = main_claude_md.split(marker)
            rest = parts[1]
            next_sec = re.search(r"\n## ", rest)
            content = rest[:next_sec.start()] if next_sec else rest
            if target == "stack":
                stack_section = content.strip()
            else:
                conventions_section = content.strip()

    for module in decomposition["modules"]:
        module_dir = f"{project_dir}/modules/{module['id']}"
        os.makedirs(module_dir, exist_ok=True)

        # plan.md dla modułu
        plan_content = f"# Plan modułu: {module['name']}\n\n"
        for step in module["steps"]:
            plan_content += f"## Krok {step['number']}: {step['title']}\n"
            plan_content += f"{step['description']}\n\n"
        write_file(f"{module_dir}/plan.md", plan_content)

        # CLAUDE.md dla modułu
        exports = "\n".join(f"- {e}" for e in module["interfaces"].get("exports", []))
        imports = "\n".join(f"- {i}" for i in module["interfaces"].get("imports", []))

        claude_content = f"""# {module['name']}

## Cel modułu
{module['description']}
To jest moduł będący częścią większego projektu. Pracujesz TYLKO nad tym modułem.

## Stack technologiczny
{stack_section}

## Interfejsy
### Ten moduł eksportuje:
{exports}

### Ten moduł importuje (z innych modułów):
{imports}

## Uwagi kontekstowe
{module.get('context_notes', 'Brak')}

## Konwencje
{conventions_section}

## Struktura plików
(auto-aktualizowane)

## Aktualny stan
Moduł nowy, krok 0/{len(module['steps'])}.
"""
        write_file(f"{module_dir}/CLAUDE.md", claude_content)
        write_file(f"{module_dir}/PROGRESS.md", "")
        write_file(f"{module_dir}/state.json", json.dumps({
            "current_step": 0,
            "total_steps": len(module["steps"]),
            "status": "initialized",
            "module_id": module["id"],
            "depends_on": module.get("depends_on", []),
            "history": []
        }, indent=2))


def run_auto(project_dir: str):
    """Tryb autonomiczny z auto-dekompozycją."""
    config = load_config()
    bot = TelegramBot(config["TELEGRAM_BOT_TOKEN"], config["TELEGRAM_CHAT_ID"])
    project_name = Path(project_dir).name

    decomp_path = f"{project_dir}/decomposition.json"

    # Faza 1: Dekompozycja
    if not Path(decomp_path).exists():
        bot.send(f"<b>🧠 {project_name} — tryb AUTO</b>\n\nAnalizuję plan i dzielę na moduły...")
        try:
            decomposition = auto_decompose(project_dir, config)
            write_file(decomp_path, json.dumps(decomposition, indent=2, ensure_ascii=False))
        except Exception as e:
            bot.send(f"🛑 Błąd dekompozycji: {e}")
            return
    else:
        decomposition = json.loads(read_file(decomp_path))

    # Raport dekompozycji
    modules_info = "\n".join(
        f"  • {m['id']}: {m['name']} ({len(m['steps'])} kroków)"
        for m in decomposition["modules"]
    )
    order_info = " → ".join(str(phase) for phase in decomposition["execution_order"])
    bot.send(
        f"<b>📋 {project_name} (tryb AUTO)</b>\n\n"
        f"🧩 Dekompozycja: {len(decomposition['modules'])} modułów\n"
        f"{modules_info}\n\n"
        f"Kolejność: {order_info}\n"
        f"Rozpoczynam..."
    )

    # Faza 2: Przygotowanie
    prepare_modules(project_dir, decomposition)

    # Faza 3: Wykonanie sekwencyjne
    for phase in decomposition["execution_order"]:
        for module_id in phase:
            module_dir = f"{project_dir}/modules/{module_id}"
            module_info = next(m for m in decomposition["modules"] if m["id"] == module_id)

            bot.send(f"🔧 Rozpoczynam moduł: <b>{module_info['name']}</b>")

            # Sprawdź zależności
            for dep_id in module_info.get("depends_on", []):
                dep_state = load_state(f"{project_dir}/modules/{dep_id}")
                if dep_state["status"] != "completed":
                    bot.send(f"🛑 Moduł {module_id} zależy od {dep_id} który nie jest gotowy!")
                    return

            # Uruchom standardową pętlę na module
            run(module_dir)

            module_state = load_state(module_dir)
            if module_state["status"] == "stopped":
                bot.send(f"🛑 Moduł {module_info['name']} wymaga interwencji. Auto-mode zatrzymany.")
                return

            bot.send(f"✅ Moduł <b>{module_info['name']}</b> zakończony.")

        bot.send(f"✅ Faza {phase} zakończona.")

    # Faza 4: Integracja
    if decomposition.get("integration_steps"):
        bot.send("🔗 Wszystkie moduły gotowe. Rozpoczynam integrację...")

        # Zbierz summaries
        all_summaries = ""
        for module_dir_path in sorted(glob(f"{project_dir}/modules/*/")):
            progress = read_file(f"{module_dir_path}/PROGRESS.md")
            module_state = load_state(module_dir_path)
            mid = module_state.get("module_id", Path(module_dir_path).name)
            all_summaries += f"\n### Moduł {mid}:\n{progress[:1000]}\n"

        for i, step in enumerate(decomposition["integration_steps"]):
            int_step = {
                "number": i + 1,
                "title": step["title"],
                "description": step["description"] + f"\n\nPODSUMOWANIE MODUŁÓW:\n{all_summaries}"
            }
            bot.send(f"🔗 Integracja {i+1}/{len(decomposition['integration_steps'])}: {step['title']}...")
            result = execute_step(project_dir, int_step, config)
            review = review_step(project_dir, int_step, result, config)
            report_step(bot, project_name, i+1, len(decomposition["integration_steps"]),
                        step["title"], review["verdict"], review["summary"], 1)

    bot.send(f"🎉 <b>Projekt {project_name} (AUTO) zakończony!</b>")
    save_state(project_dir, 999, "completed")


# ──────────────────────────────────────────────
# Komendy CLI
# ──────────────────────────────────────────────

def cmd_init(project_dir: str):
    """Inicjalizuje nowy projekt."""
    Path(project_dir).mkdir(parents=True, exist_ok=True)

    templates_dir = Path(__file__).parent / "templates"

    # Skopiuj szablony (tylko jeśli pliki nie istnieją)
    for tmpl, dst in [("plan-template.md", "plan.md"), ("CLAUDE-template.md", "CLAUDE.md")]:
        src = templates_dir / tmpl
        dst_path = Path(project_dir) / dst
        if src.exists() and not dst_path.exists():
            dst_path.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    # Utwórz puste pliki
    for fname in ["PROGRESS.md"]:
        p = Path(project_dir) / fname
        if not p.exists():
            p.write_text("")

    # State
    state_path = Path(project_dir) / "state.json"
    if not state_path.exists():
        state_path.write_text(json.dumps({
            "current_step": 0,
            "total_steps": 0,
            "status": "initialized",
            "history": []
        }, indent=2))

    print(f"""
✅ Projekt zainicjalizowany w: {project_dir}

Kolejne kroki:
1. Edytuj {project_dir}/plan.md — wpisz kroki projektu
2. Edytuj {project_dir}/CLAUDE.md — opisz kontekst projektu
3. Uruchom: python3 orchestrator.py run {project_dir}
""")


def cmd_status(project_dir: str):
    """Pokazuje stan projektu."""
    state = load_state(project_dir)
    plan = parse_plan(f"{project_dir}/plan.md")

    print(f"\n📋 Projekt: {Path(project_dir).name}")
    print(f"Status: {state['status']}")
    print(f"Krok: {state['current_step']}/{len(plan)}")
    print(f"\nHistoria ({len(state.get('history', []))} kroków):")
    for h in state.get("history", [])[-10:]:
        emoji = {"APPROVED": "✅", "SKIPPED": "⏭️", "NEEDS_FIX": "🔧"}.get(h.get("verdict", ""), "❓")
        print(f"  {emoji} Krok {h['step']}: {h['title']} ({h.get('iterations', 0)} iter.)")


def cmd_reset(project_dir: str):
    """Resetuje stan projektu."""
    confirm = input(f"Resetować stan projektu {project_dir}? [tak/nie]: ")
    if confirm.lower() in ("tak", "t", "yes", "y"):
        save_state(project_dir, 0, "initialized", [])
        write_file(f"{project_dir}/PROGRESS.md", "")
        print("✅ Stan zresetowany.")
    else:
        print("Anulowano.")


# ──────────────────────────────────────────────
# Punkt wejścia
# ──────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]
    project = sys.argv[2]

    commands = {
        "init": cmd_init,
        "run": run,
        "status": cmd_status,
        "reset": cmd_reset,
        "auto": run_auto,
    }

    if command not in commands:
        print(f"Nieznana komenda: {command}")
        print(f"Dostępne: {', '.join(commands.keys())}")
        sys.exit(1)

    commands[command](project)
