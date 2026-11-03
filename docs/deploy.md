# Colocando no ar

Receita para um servidor Linux com Docker (uma VPS pequena, de 1 GB de RAM, já dá conta). O Caddy emite o certificado HTTPS sozinho, e a Meta exige HTTPS no webhook.

## 1. Domínio

Aponte um domínio ou subdomínio (registro `A`) para o IP do servidor, por exemplo `jamba.seudominio.com`. As portas 80 e 443 precisam estar abertas.

## 2. Arquivos

No servidor:

```bash
mkdir jambachat && cd jambachat
curl -fsSLO https://raw.githubusercontent.com/jvfmjamba/jambachat/main/deploy/compose.yaml
curl -fsSLO https://raw.githubusercontent.com/jvfmjamba/jambachat/main/deploy/Caddyfile
curl -fsSL https://raw.githubusercontent.com/jvfmjamba/jambachat/main/.env.example -o .env
```

Se o repositório for privado, copie esses três arquivos com `scp`. A imagem `ghcr.io/jvfmjamba/jambachat` também precisa estar pública, ou faça `docker login ghcr.io` antes.

## 3. Configuração

Edite o `.env`:

- Gere as chaves de criptografia em qualquer máquina com o projeto (`uv run python -m jambachat.security.keygen`) e cole `ENCRYPTION_KEYS` e `INDEX_KEY`. Guarde uma cópia: sem elas, os dados salvos não podem ser lidos.
- Acrescente `DOMAIN=jamba.seudominio.com` e `POSTGRES_PASSWORD=` com uma senha forte.
- Preencha WhatsApp, provedor de IA e o resto como em [whatsapp.md](whatsapp.md) e no README.

`DATABASE_URL` não precisa ser preenchida: o compose monta o endereço do Postgres.

## 4. Subir

```bash
docker compose up -d
docker compose logs -f app
```

As migrações rodam sozinhas a cada início. Confira `https://jamba.seudominio.com/health/ready`, que deve responder `{"status":"ok"}`.

Depois registre `https://jamba.seudominio.com/webhook` na Meta (passo 4 de [whatsapp.md](whatsapp.md)).

## Atualizar

```bash
docker compose pull app && docker compose up -d app
```

Cada commit na `main` publica `latest` e uma tag com o hash do commit. Para fixar uma versão, defina `JAMBACHAT_TAG=<hash>` no `.env`.

## Backup

```bash
docker compose exec db pg_dump -U jambachat jambachat | gzip > backup-$(date +%F).sql.gz
```

Guarde os backups junto com as chaves de criptografia: um sem o outro não serve.
