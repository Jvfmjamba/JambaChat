# Configurando o WhatsApp Cloud API

Passo a passo para ligar o JambaChat a um número do WhatsApp.

## 1. Criar o app na Meta

1. Em [developers.facebook.com/apps](https://developers.facebook.com/apps), crie um app do tipo **Empresa** e adicione o produto **WhatsApp**.
2. Em **WhatsApp → Configuração da API**, anote o **ID do número de telefone** (`WHATSAPP_PHONE_NUMBER_ID`).
3. Cadastre o seu celular como destinatário de teste.
4. Em **Configurações do app → Básico**, copie a **Chave secreta do app** (`WHATSAPP_APP_SECRET`).

## 2. Gerar um token permanente

O token temporário da tela de configuração expira em 24 horas. Para um token que não expira:

1. Em [business.facebook.com](https://business.facebook.com) → **Configurações do negócio → Usuários do sistema**, crie um usuário do sistema administrador.
2. Atribua a ele o app e a conta do WhatsApp.
3. Gere um token com as permissões `whatsapp_business_messaging` e `whatsapp_business_management`.
4. Use esse token em `WHATSAPP_ACCESS_TOKEN`.

## 3. Expor o servidor com HTTPS

A Meta só chama webhooks em HTTPS. Para testar na sua máquina:

```bash
cloudflared tunnel --url http://localhost:8000
# ou
ngrok http 8000
```

## 4. Registrar o webhook

1. Invente um texto qualquer para `WHATSAPP_VERIFY_TOKEN` e coloque no `.env`.
2. Em **WhatsApp → Configuração**, em **Webhook**, informe:
   - URL de callback: `https://SEU-ENDERECO/webhook`
   - Token de verificação: o mesmo de `WHATSAPP_VERIFY_TOKEN`
3. Clique em **Verificar e salvar** com o servidor rodando.
4. Em **Campos do webhook**, assine o campo `messages`.

## 5. Liberar quem pode conversar

Só os números em `ALLOWED_PHONES` recebem resposta, no formato internacional sem `+` (ex.: `5511999999999`). Números em `ADMIN_PHONES` também podem usar `/status`.

## 6. Modelo de mensagem para lembretes

Fora da janela de 24 horas desde a última mensagem do usuário, o WhatsApp só permite mensagens a partir de modelos aprovados. Os lembretes usam um modelo nesse caso.

Em **WhatsApp → Gerenciador do WhatsApp → Modelos de mensagem**, crie:

- Nome: `lembrete` (ou o valor de `WHATSAPP_REMINDER_TEMPLATE`)
- Categoria: **Utilidade**
- Idioma: **Português (BR)**
- Corpo: `⏰ Lembrete: {{1}}`

A aprovação costuma levar alguns minutos. Dentro da janela de 24 horas, os lembretes saem como texto comum.

## 7. Testar

Mande "oi" do celular cadastrado. Se não vier resposta, confira os logs do servidor:

- `invalid_signature`: `WHATSAPP_APP_SECRET` errado.
- `ignored_unauthorized_sender`: o número não está em `ALLOWED_PHONES`.
- `send_failed` com status 401: token expirado ou sem permissão.
- `llm_failed`: o provedor de IA não respondeu; confira `LLM_BASE_URL`, `LLM_MODEL` e `LLM_API_KEY`.
