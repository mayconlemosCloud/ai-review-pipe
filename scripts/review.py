"""Review de PR (GitHub) ou MR (GitLab) com Claude.

Detecta a plataforma pelas variáveis de ambiente da CI, pega o diff,
pede o review ao Claude e publica (ou atualiza) um comentário no PR/MR.

Variáveis:
  ANTHROPIC_API_KEY   obrigatória
  REVIEW_MODEL        padrão: claude-opus-5-5
  REVIEW_EFFORT       low | medium | high | xhigh | max (padrão: high)
  REVIEW_LANGUAGE     idioma do comentário (padrão: português do Brasil)
  REVIEW_RULES_FILE   arquivo do repo com regras extras (padrão: .ai-review.md)
  REVIEW_MAX_CHARS    limite do diff enviado (padrão: 400000)
  REVIEW_DRY_RUN      "1" imprime o review em vez de comentar
  GitHub: GITHUB_TOKEN (permissão pull-requests: write)
  GitLab: GITLAB_TOKEN (Project Access Token com escopo "api")
"""

import json
import os
import sys
import urllib.error
import urllib.request

import anthropic

MARKER = "<!-- ai-review-pipe -->"
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}


def env(name, default=None, required=False):
    value = os.environ.get(name, default)
    if required and not value:
        sys.exit(f"[ai-review] variável obrigatória ausente: {name}")
    return value


def http(method, url, headers, body=None, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = resp.read().decode("utf-8", errors="replace")
            return payload if raw else (json.loads(payload) if payload else None), resp.headers
    except urllib.error.HTTPError as e:
        sys.exit(f"[ai-review] {method} {url} -> {e.code}: {e.read().decode(errors='replace')[:500]}")


# ---------------------------------------------------------------- GitHub

class GitHub:
    def __init__(self):
        event = json.load(open(env("GITHUB_EVENT_PATH", required=True), encoding="utf-8"))
        pr = event.get("pull_request")
        if not pr:
            sys.exit("[ai-review] evento não é de pull request; nada a fazer.")
        self.api = env("GITHUB_API_URL", "https://api.github.com")
        self.repo = env("GITHUB_REPOSITORY", required=True)
        self.number = pr["number"]
        self.title = pr.get("title", "")
        self.description = pr.get("body") or ""
        self.headers = {
            "Authorization": f"Bearer {env('GITHUB_TOKEN', required=True)}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def diff(self):
        headers = dict(self.headers, Accept="application/vnd.github.diff")
        text, _ = http("GET", f"{self.api}/repos/{self.repo}/pulls/{self.number}", headers, raw=True)
        return text

    def publish(self, body):
        base = f"{self.api}/repos/{self.repo}/issues"
        page = 1
        while True:
            comments, _ = http("GET", f"{base}/{self.number}/comments?per_page=100&page={page}", self.headers)
            for c in comments:
                if MARKER in (c.get("body") or ""):
                    http("PATCH", f"{base}/comments/{c['id']}", self.headers, {"body": body})
                    return "atualizado"
            if len(comments) < 100:
                break
            page += 1
        http("POST", f"{base}/{self.number}/comments", self.headers, {"body": body})
        return "criado"


# ---------------------------------------------------------------- GitLab

class GitLab:
    def __init__(self):
        self.iid = env("CI_MERGE_REQUEST_IID")
        if not self.iid:
            sys.exit("[ai-review] pipeline não é de merge request; nada a fazer.")
        self.base = f"{env('CI_API_V4_URL', required=True)}/projects/{env('CI_PROJECT_ID', required=True)}/merge_requests/{self.iid}"
        self.title = env("CI_MERGE_REQUEST_TITLE", "")
        self.description = env("CI_MERGE_REQUEST_DESCRIPTION", "")
        self.headers = {"PRIVATE-TOKEN": env("GITLAB_TOKEN", required=True)}

    def diff(self):
        parts, page = [], 1
        while True:
            items, headers = http("GET", f"{self.base}/diffs?per_page=100&page={page}", self.headers)
            for d in items:
                parts.append(f"diff --git a/{d['old_path']} b/{d['new_path']}\n--- a/{d['old_path']}\n+++ b/{d['new_path']}\n{d['diff']}")
            if not headers.get("X-Next-Page"):
                break
            page += 1
        return "\n".join(parts)

    def publish(self, body):
        page = 1
        while True:
            notes, headers = http("GET", f"{self.base}/notes?per_page=100&page={page}", self.headers)
            for n in notes:
                if MARKER in (n.get("body") or ""):
                    http("PUT", f"{self.base}/notes/{n['id']}", self.headers, {"body": body})
                    return "atualizado"
            if not headers.get("X-Next-Page"):
                break
            page += 1
        http("POST", f"{self.base}/notes", self.headers, {"body": body})
        return "criado"


# ---------------------------------------------------------------- Review

def build_prompt(platform, diff, truncated):
    language = env("REVIEW_LANGUAGE", "português do Brasil")
    rules_file = env("REVIEW_RULES_FILE", ".ai-review.md")
    rules = open(rules_file, encoding="utf-8").read() if os.path.isfile(rules_file) else ""

    system = (
        "Você é um revisor de código sênior. Revise o diff de um pull/merge request e aponte "
        "apenas o que importa: bugs, falhas de segurança, problemas de desempenho, casos de borda "
        "não tratados e código difícil de manter. Não comente estilo que um linter pegaria. "
        "Cite arquivo e linha quando possível e sugira a correção. "
        f"Responda em {language}, em Markdown, neste formato:\n"
        "## Resumo\n(1 a 3 frases sobre o que o PR faz)\n"
        "## Problemas\n(lista com 🔴 crítico, 🟡 atenção ou 🔵 sugestão; escreva 'Nenhum problema encontrado.' se for o caso)\n"
        "## Veredito\n(✅ Aprovar, ⚠️ Aprovar com ressalvas ou ❌ Pedir mudanças)"
    )
    if rules:
        system += f"\n\nRegras específicas deste repositório:\n{rules}"

    user = f"Título: {platform.title}\n\nDescrição:\n{platform.description or '(sem descrição)'}\n\n"
    if truncated:
        user += "ATENÇÃO: o diff foi cortado por ser muito grande; revise só o que está abaixo e avise isso no resumo.\n\n"
    user += f"<diff>\n{diff}\n</diff>"
    return system, user


def ask_claude(system, user):
    client = anthropic.Anthropic()
    model = env("REVIEW_MODEL", "claude-opus-5-5")
    kwargs = dict(
        model=model,
        max_tokens=64000,
        output_config={"effort": env("REVIEW_EFFORT", "high")},
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    if model in FALLBACK_MODELS:
        # Se o modelo recusar por política, a API refaz a chamada num modelo de fallback.
        stream = client.beta.messages.stream(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
    else:
        stream = client.messages.stream(**kwargs)
    with stream as s:
        response = s.get_final_message()

    if response.stop_reason == "refusal":
        return "⚠️ O modelo recusou revisar este diff."
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    if response.stop_reason == "max_tokens":
        text += "\n\n_(review cortado: limite de tokens atingido)_"
    return text


def main():
    env("ANTHROPIC_API_KEY", required=True)
    if env("GITHUB_ACTIONS") == "true":
        platform = GitHub()
    elif env("GITLAB_CI") == "true":
        platform = GitLab()
    else:
        sys.exit("[ai-review] rode dentro do GitHub Actions ou do GitLab CI.")

    diff = platform.diff()
    if not diff.strip():
        print("[ai-review] diff vazio; nada a revisar.")
        return

    limit = int(env("REVIEW_MAX_CHARS", "400000"))
    truncated = len(diff) > limit
    if truncated:
        print(f"[ai-review] diff com {len(diff)} caracteres; enviando só os primeiros {limit}.")
        diff = diff[:limit]

    review = ask_claude(*build_prompt(platform, diff, truncated))
    body = f"{MARKER}\n### 🤖 AI Review\n\n{review}"

    if env("REVIEW_DRY_RUN") == "1":
        print(body)
        return
    print(f"[ai-review] comentário {platform.publish(body)}.")


if __name__ == "__main__":
    main()
