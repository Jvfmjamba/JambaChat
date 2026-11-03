# Compras do iPhone pelo app Atalhos

Com uma automação do app Atalhos (iOS 17 ou mais novo), cada compra feita com um cartão da Carteira é enviada ao JambaChat e vira um gasto, já com categoria.

A Apple não oferece uma forma de servidores lerem as transações da Carteira. A automação roda no próprio iPhone e manda só o valor, o nome do estabelecimento e o nome do cartão.

## Pré-requisitos

- JambaChat publicado com HTTPS (veja [deploy.md](deploy.md)) e `PUBLIC_BASE_URL` preenchida no `.env`, ex.: `https://bot.seudominio.com`.
- Seu número em `ALLOWED_PHONES`.

## 1. Gere seu código pessoal

Mande `/atalho` para o JambaChat no WhatsApp. Ele responde com um código e o endereço a usar. O código aparece uma única vez; mandar `/atalho` de novo gera outro e invalida o anterior. Para desligar a integração, mande `/atalho desligar`.

## 2. Crie a automação

1. Abra **Atalhos → Automação → Nova Automação**.
2. Escolha **Transação**, marque os cartões que quer acompanhar e selecione **Executar Imediatamente**.
3. Adicione a ação **Obter Conteúdo de URL** e configure:
   - **URL**: o endereço que o JambaChat mandou (termina em `/integrations/wallet`).
   - **Método**: `POST`.
   - **Cabeçalhos**: chave `Authorization`, valor `Bearer ` seguido do código.
   - **Corpo da Solicitação**: `JSON`, com três campos de texto:
     - `amount` → variável **Valor** da transação
     - `merchant` → variável **Comerciante**
     - `card` → variável **Cartão ou Passe**
4. Opcional: adicione **Mostrar Notificação** com o campo `message` do resultado para ver a confirmação na hora.

## Como funciona

- O gasto é registrado na hora com a categoria deduzida pelo nome do estabelecimento (ex.: Uber → Transporte, iFood → Alimentação). Se errar, é só pedir no WhatsApp: "o último gasto foi mercado".
- Se você conversou com o JambaChat nas últimas 24 horas, ele também confirma pelo WhatsApp e avisa se algum orçamento foi atingido.
- Pedidos com código errado, de números fora de `ALLOWED_PHONES` ou em excesso (mais de 10 por minuto) são recusados.
