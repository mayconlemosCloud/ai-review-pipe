# ai-review-pipe

Pipe central de **review de código com IA (Claude)**. Os outros repositórios só "chamam" esta pipe e, a cada PR (GitHub) ou MR (GitLab), recebem um comentário com o review.

```
repo-qualquer (código normal)          ai-review-pipe (este repo)
  PR/MR aberto ──── chama ────────────▶  pega o diff → Claude → comenta no PR/MR
```

O comentário é **atualizado** a cada novo push (não vira spam).

## Estrutura

| Arquivo | Para quê |
|---|---|
| `scripts/review.py` | O cérebro: detecta GitHub/GitLab, pega o diff, chama o Claude e comenta |
| `.github/workflows/ai-review.yml` | Reusable workflow (a "pipe" do GitHub) |
| `action.yml` | Action composta usada pelo workflow (também dá para usar direto) |
| `gitlab/ai-review.yml` | Template para `include` no GitLab |
| `examples/` | O que copiar nos outros repositórios |

## 1. Publicar esta pipe

1. Os arquivos já apontam para `mayconlemosCloud/ai-review-pipe`.
2. Crie o repositório **público** `ai-review-pipe` no GitHub e faça push.
   (Se for privado: em *Settings → Actions → General → Access*, libere o acesso para os outros repos. O GitLab precisa do repo público para baixar o script.)
3. Opcional: crie uma tag `v1` e use `@v1` em vez de `@main` nos repos que consomem.

## 2. Usar em um repositório do GitHub

1. Em *Settings → Secrets and variables → Actions*, crie o secret `ANTHROPIC_API_KEY`.
2. Copie [`examples/github/pr-review.yml`](examples/github/pr-review.yml) para `.github/workflows/pr-review.yml`.

Pronto. Parâmetros opcionais em `with:` → `model`, `effort` (`low`…`max`), `language`, `rules_file`.

> PRs vindos de **fork** não recebem secrets no evento `pull_request`, então o review não roda neles.

## 3. Usar em um projeto do GitLab

1. Em *Settings → CI/CD → Variables*, crie (Masked):
   - `ANTHROPIC_API_KEY`
   - `GITLAB_TOKEN` → *Settings → Access Tokens*, escopo `api`, papel Reporter ou superior.
2. Adicione ao `.gitlab-ci.yml`:

```yaml
include:
  - remote: https://raw.githubusercontent.com/mayconlemosCloud/ai-review-pipe/main/gitlab/ai-review.yml
```

O job `ai-review` roda no stage `test`, só em MRs (não draft), com `allow_failure: true` — nunca trava o pipeline. Exemplo completo em [`examples/gitlab/.gitlab-ci.yml`](examples/gitlab/.gitlab-ci.yml).

## Regras por repositório

Crie um `.ai-review.md` na raiz do repositório consumidor com o que a IA deve cobrar (ex.: "toda rota precisa de teste"). Veja [`examples/.ai-review.md`](examples/.ai-review.md).

## Configuração (variáveis de ambiente do script)

| Variável | Padrão |
|---|---|
| `REVIEW_MODEL` | `claude-opus-5-5` |
| `REVIEW_EFFORT` | `high` (use `medium`/`low` para gastar menos) |
| `REVIEW_LANGUAGE` | `português do Brasil` |
| `REVIEW_RULES_FILE` | `.ai-review.md` |
| `REVIEW_MAX_CHARS` | `400000` (diffs maiores são cortados e o review avisa) |
| `REVIEW_DRY_RUN` | `1` imprime o review sem comentar |

## Custo

Cada review é uma chamada à API da Anthropic, cobrada pelo tamanho do diff. Para baratear: `effort: medium` ou `model: claude-sonnet-5-5`.
