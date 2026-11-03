# JambaChat

Assistente pessoal no WhatsApp para lembretes, controle de gastos, pesquisas rápidas e utilitários do dia a dia.

## Status

Em desenvolvimento. Marco atual: M1 — Conversa.

## O que ele faz

| Área | Exemplos |
|---|---|
| Lembretes | "me lembra de pagar a luz dia 10 às 9h", "todo dia às 8h tomar remédio", "adia 10 min" |
| Gastos | "gastei 42 no ifood", "recebi 3000 de salário", "quanto gastei esse mês?", "limite de 800 pra mercado", "aluguel de 1500 todo dia 5", "me manda a planilha de outubro" |
| Listas | "põe leite e pão na lista de compras", "marca o pão", "o que falta comprar?" |
| Pesquisa | "quem ganhou o jogo ontem?", "que horas abre o Detran?", "quem foi Santos Dumont?" |
| Utilitários | câmbio ("100 dólares em reais"), clima, CEP, calculadora, contas com datas |
| Perfil | "me chama de Jão", "mudei pra Manaus", "minha cidade é Recife" |
| Áudio e foto | áudios são transcritos; fotos de notas viram gastos (com modelo de visão) |

Comandos: `/ajuda`, `/limpar` e `/apagar-meus-dados`.

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

Para criar o app na Meta, registrar o webhook e o modelo de mensagem dos lembretes, siga [docs/whatsapp.md](docs/whatsapp.md).

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

No Ollama, aumente a janela de contexto: o padrão é pequeno demais para o prompt com todas as ferramentas e o modelo passa a ignorar instruções. Inicie o servidor com `OLLAMA_CONTEXT_LENGTH=16384 ollama serve` (ou ajuste "Context length" nas configurações do app).

Com um modelo que enxerga imagens (ex.: `gemini-2.5-flash`, `claude-haiku-5-5`, `qwen2.5vl` no Ollama), defina `LLM_VISION=true` para o assistente ler fotos.

### Áudio

A transcrição usa qualquer API compatível com `/audio/transcriptions` da OpenAI. Sem `STT_BASE_URL`, áudios recebem uma resposta pedindo texto.

| Provedor | `STT_BASE_URL` | `STT_MODEL` | Custo |
|---|---|---|---|
| Groq | `https://api.groq.com/openai/v1` | `whisper-large-v3-turbo` | Plano gratuito |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini-transcribe` | Pago |
| Speaches (na sua máquina) | `http://localhost:8000/v1` | `Systran/faster-whisper-small` | Gratuito |

### Pesquisa e utilitários

A pesquisa na web usa a [Tavily](https://tavily.com) (plano gratuito) e só fica disponível com `TAVILY_API_KEY`. Perguntas de conhecimento geral usam a Wikipédia, sempre disponível. Câmbio ([AwesomeAPI](https://docs.awesomeapi.com.br)), clima ([Open-Meteo](https://open-meteo.com)) e CEP ([ViaCEP](https://viacep.com.br)) são gratuitos e não precisam de chave.

## Executando

```bash
uv sync
uv run alembic upgrade head
uv run uvicorn jambachat.main:app --reload
```

Rode um único processo do servidor (o padrão do uvicorn e da imagem Docker): a fila que responde as mensagens de cada pessoa em ordem fica em memória. Em produção use Postgres; o SQLite serve para desenvolvimento e para o modo terminal.

Para conversar pelo terminal, sem WhatsApp (bom para testar o provedor de IA antes de configurar a Meta):

```bash
uv run python -m jambachat.cli "Seu nome"
```

Ou com Docker:

```bash
docker compose up --build
```

## Produção

Para colocar num servidor com HTTPS, Postgres e atualização pela imagem publicada, siga [docs/deploy.md](docs/deploy.md).

## Desenvolvimento

```bash
scripts/check.sh        # formatação, lint, tipos e testes em paralelo
scripts/check.sh --fix  # corrige formatação e lint antes
```

## Segurança

Veja [SECURITY.md](SECURITY.md).

## Licença

[MIT](LICENSE)
