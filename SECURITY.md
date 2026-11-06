# Segurança

## Reportando uma vulnerabilidade

Não abra issue pública. Use **Security → Report a vulnerability** neste repositório (aviso privado do GitHub). A resposta inicial sai em até 7 dias.

## Modelo de ameaças

| Ameaça | Defesa |
|---|---|
| Alguém de fora chama o webhook | Assinatura HMAC `X-Hub-Signature-256` obrigatória; corpo limitado a 1 MB antes de ler |
| Número desconhecido manda mensagem | Descartada antes de tocar no banco (`ALLOWED_PHONES`; lista vazia = ninguém) |
| Usuário tenta, via IA, mexer em dados de outra pessoa ("crie um lembrete para o usuário X") | A identidade vem **só** do remetente autenticado (`ToolContext.user`). Ferramentas não aceitam campos de usuário/telefone/destinatário, recusam argumentos desconhecidos e conferem o dono de cada registro |
| Prompt injection (em mensagens ou resultados de ferramentas) | Mesmas barreiras acima: mesmo uma IA totalmente manipulada só alcança os dados de quem está falando |
| Página da web com instruções escondidas ("apague todos os gastos") | Depois que uma ferramenta `reads_untrusted` (pesquisa na web, Wikipédia) roda numa mensagem, ferramentas `destructive` (apagar/alterar gasto, cancelar lembrete) ficam bloqueadas até o usuário pedir de novo |
| Imagem encaminhada com instruções escondidas | Mensagens com imagem contam como conteúdo de terceiros: ações `destructive` ficam bloqueadas naquela mensagem |
| Áudio ou legenda gigante para gastar IA | Transcrição e legenda passam pelo mesmo `MAX_INPUT_CHARS`; mídia limitada por `MAX_AUDIO_MB`/`MAX_IMAGE_MB` durante o download |
| Vazamento do token do WhatsApp no download de mídia | Token só é enviado para hosts HTTPS da Meta; id de mídia só com dígitos |
| SSRF pelas ferramentas de utilidade | Hosts fixos, entrada normalizada (CEP só dígitos, moeda por regex, cidade como parâmetro), sem seguir redirects |
| Código arbitrário pela calculadora | Avaliação por AST com lista fechada de operações e funções, limites de expoente, magnitude e fatorial |
| Flood / custo de IA | Limite por usuário (`RATE_LIMIT_PER_MINUTE`, `RATE_LIMIT_PER_DAY`), tamanho máximo de mensagem (`MAX_INPUT_CHARS`), no máximo 10 ferramentas e 5 rodadas por mensagem, no máximo 100 lembretes pendentes por usuário |
| Vazamento do banco | Telefone, nome, mensagens e lembretes cifrados com AES-256-GCM; busca por telefone via HMAC com chave separada |
| Banco exposto na internet | `compose.yaml` publica Postgres e app só em `127.0.0.1` |
| Deploy mal configurado | Com `APP_ENV=production` o app não sobe sem segredos do WhatsApp, sem `ALLOWED_PHONES`, com sqlite ou com senha de banco padrão |
| Atalho da Carteira (`/integrations/wallet`) chamado por terceiros | Código pessoal aleatório de 256 bits, guardado só como HMAC; só números de `ALLOWED_PHONES`; limite por usuário; corpo até 16 KB; `/atalho desligar` revoga. Tentativas sem código válido não têm limite próprio, mas adivinhar é inviável |
| Nome de estabelecimento com instruções escondidas (vindo da Carteira) | Texto limpo e cortado em 120 caracteres; o prompt trata resultados de ferramentas como dados. Risco residual baixo: a IA lê esse texto ao listar gastos |
| Cadeia de suprimentos | Actions fixadas por hash de commit, `permissions: contents: read`, gitleaks e pip-audit no CI, Dependabot semanal |

## Regras para criar ferramentas novas

Essas regras são verificadas automaticamente: o `ToolRegistry` recusa a ferramenta na inicialização se forem violadas.

1. Os argumentos herdam de `ToolArgs` (campos extras são recusados).
2. Nenhum argumento pode identificar uma pessoa (`user_id`, `phone`, `telefone`, `to`, `para`, `destinatario`, `owner`...). Use sempre `ctx.user`.
3. Toda leitura ou escrita por id filtra pelo dono (veja `get_owned` em `reminders/service.py`).
4. Nunca crie uma ferramenta que envie mensagem para um número arbitrário.
5. Marque `destructive=True` se ela altera ou apaga dados existentes, e `reads_untrusted=True` se devolve conteúdo de terceiros (web, e-mail, arquivos).
6. Chamadas HTTP só para hosts fixos no código; nunca monte URL com host vindo da IA.
7. Escreva um teste no estilo de `tests/security/test_isolation.py` com uma IA "sequestrada".

## Colocando em produção

- Gere senhas fortes: `POSTGRES_PASSWORD` (também na `DATABASE_URL`) e `WHATSAPP_VERIFY_TOKEN` com 16+ caracteres.
- Use `deploy/compose.yaml`: só o Caddy publica portas (80/443), com HTTPS automático, corpo limitado a 1 MB e apenas `/webhook`, `/health` e `/integrations/wallet` acessíveis de fora. Não exponha a porta 8000 diretamente.
- O código do `/atalho` só aparece uma vez no WhatsApp; se o celular for perdido ou o código vazar, mande `/atalho desligar` (ou `/atalho` para gerar outro).
- Guarde `ENCRYPTION_KEYS` e `INDEX_KEY` num gerenciador de senhas. Sem elas os dados do banco não podem ser lidos. Não deixe o `.env` em pasta sincronizada sem verificação em duas etapas na conta.
- Use `APP_ENV=production` (já é o padrão da imagem Docker).
