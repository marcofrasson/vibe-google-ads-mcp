# Fork Vibe do google-ads-mcp

Fork de [`googleads/google-ads-mcp`](https://github.com/googleads/google-ads-mcp) (que é read-only)
com um namespace `mutate` para criar e gerenciar campanhas. Upstream fica no remote `upstream`;
o trabalho local está na branch `vibe/mutate-tools`.

## Modelo de segurança: dry-run por padrão

Toda tool de escrita aceita `confirm` e **começa em falso**:

| `confirm` | O que acontece |
|---|---|
| ausente / `False` | Request vai com `validate_only=True`. A API valida tudo e **não altera nada**. Retorna `{"dry_run": true, ...}` com um `summary` legível. |
| `True` | Commita de verdade e retorna os `resource_names` criados. |

Foi assim que declaramos no formulário de Basic Access: validação prévia + confirmação humana antes
de qualquer alteração. O fluxo esperado do agente é sempre: chamar sem `confirm` → mostrar o
`summary` ao usuário → só chamar com `confirm=True` após aprovação explícita.

Campanhas nascem `PAUSED` por padrão, para nada começar a gastar por acidente.

## Tools do namespace `mutate`

- `create_campaign_budget`, `update_campaign_budget_amount`
- `create_campaign`, `update_campaign_status`
- `add_campaign_geo_targets`, `add_campaign_languages`, `add_campaign_negative_keywords`
- `create_ad_group`, `update_ad_group_status`
- `add_keywords`
- `create_responsive_search_ad`

Para rodar somente leitura, basta `mutate: false` no `ads_mcp/tools_config.yaml`.

## Validação local antes do round trip

O que é checado antes de gastar uma chamada: formato do `customer_id` (aceita `777-012-3631`),
datas reais no formato `YYYY-MM-DD` (`01/08/2026` é recusado explicitamente), contagem e limite de
caracteres dos assets de RSA (3–15 headlines de até 30, 2–4 descrições de até 90), e valores de enum,
com a lista de opções válidas na mensagem de erro.

## Gotchas da API

- **`start_date`/`end_date` não existem mais** em `Campaign` desde a v21: são `start_date_time` e
  `end_date_time`, no formato `YYYY-MM-DD HH:MM:SS`. Uma data pura vira início do dia (ou 23:59:59
  no `end_date`, para a campanha rodar o dia inteiro).
- A biblioteca chama a **v25** por padrão, mesmo com os imports de tipos apontando para v24.
- Contas de cliente exigem `GOOGLE_ADS_LOGIN_CUSTOMER_ID` (o MCC) no ambiente.

## Ambiente

```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -e .
set -a; source ~/www/apps/vibe-digital-site/.env.local; set +a   # developer token + login customer id
```

Auth via ADC: `gcloud auth application-default login` com os escopos `adwords` e `cloud-platform`.

## Estado atual (25/jul/2026)

O developer token está com **Test Access**: só opera em contas de teste
(`The developer token is only approved for use with test accounts`). O pedido de Basic Access foi
enviado em 23/jul. Enquanto não sai, o caminho de teste é uma hierarquia de contas de teste
(manager de teste criado com outra Google Account; o developer token de produção funciona nela).

Já validado: as 10 tools montam no servidor, a validação local recusa entradas inválidas, e o
request chega à API com `validate_only=True` e o proto correto — falta apenas uma conta acessível
para o commit real.
