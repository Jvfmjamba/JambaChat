# JambaChat

Assistente pessoal no WhatsApp para lembretes, controle de gastos, pesquisas rápidas e utilitários do dia a dia.

## Status

Release candidate. Marco atual: M2 — Lembretes.

## Requisitos

- Python 3.12+ e [uv](https://docs.astral.sh/uv/), ou Docker
- Um número de teste na [WhatsApp Cloud API](https://developers.facebook.com/docs/whatsapp/cloud-api)
- Um provedor de IA (pode ser local e gratuito)

## Configuração

```bash
cp .env.example .env
uv run python -m jambachat.security.keygen >> .env
```

Depois remova as linhas `ENCRYPTION_KEYS=` e `INDEX_KEY=` vazias do `.env` e preencha o resto.

### Provedor de IA

Qualquer serviço com API compatível com OpenAI funciona, além da Anthropic. O modelo precisa suportar chamada de ferramentas.

| Provedor | `LLM_PROVIDER` | `LLM_BASE_URL` | `LLM_MODEL` (exemplo) | Custo |
|---|---|---|---|---|
| Ollama (na sua máquina) | `openai_compatible` | `http://localhost:11434/v1` | `qwen3:8b` | Gratuito |
| LM Studio (na sua máquina) | `openai_compatible` | `http://localhost:1234/v1` | modelo carregado | Gratuito |
| Google Gemini | `openai_compatible` | `https://generativelanguage.googleapis.com/v1beta/openai` | `gemini-2.5-flash` | Plano gratuito |
| Groq | `openai_compatible` | `https://api.groq.com/openai/v1` | `llama-3.3-70b-versatile` | Plano gratuito |
| OpenRouter | `openai_compatible` | `https://openrouter.ai/api/v1` | modelos `:free` | Plano gratuito |
| Anthropic | `anthropic` | vazio | `claude-haiku-5-5` | Pago |

`LLM_API_KEY` fica vazia para provedores locais.

## Executando

```bash
uv sync
uv run alembic upgrade head
uv run uvicorn jambachat.main:app --reload
```

Ou com Docker:

```bash
docker compose up --build
```

## Desenvolvimento

```bash
scripts/check.sh        # formatação, lint, tipos e testes em paralelo
scripts/check.sh --fix  # corrige formatação e lint antes
```

## Segurança

Veja [SECURITY.md](SECURITY.md).

## Licença

[MIT](LICENSE)
