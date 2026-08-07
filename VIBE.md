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

## Tools do namespace `planning` (leitura)

Cobrem a capability **Keyword Planning Services**, declarada no formulário de Basic Access:

- `generate_keyword_ideas` — ideias de palavra-chave com volume de busca médio mensal, concorrência
  (nível + índice 0–100) e faixas de lance, já convertidas de micros para a moeda da conta.
  Semeia por `keywords` (até 20), por `page_url`, ou pelos dois juntos (o melhor input). Defaults
  para conta brasileira: geo `2076` (Brasil) e idioma `1014` (português). Ordena por volume.
- `suggest_geo_targets` — descobre o id de um geo target pelo nome ("Joinville", "Caxias do Sul"),
  para alimentar o `geo_target_ids` da tool acima. Não precisa de `customer_id`.

Ambas são `readOnlyHint=True` e não alteram nada na conta — não têm, nem devem ter, `confirm`.

> **Restrição de uso declarada no formulário:** dado do Keyword Planner é de **uso interno**. Não
> pode ser exibido ao cliente, publicado em relatório/dashboard voltado ao cliente, nem revendido.
> Expor a terceiros exigiria conformidade com a Required Minimum Functionality, que este servidor
> não implementa.

Desligar: `planning: false` no `ads_mcp/tools_config.yaml`.

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

## Estado atual (07/ago/2026)

O developer token está com **Test Access**: só opera em contas de teste
(`The developer token is only approved for use with test accounts`). O 1º pedido de Basic Access
(23/jul, case `29842141618`) foi **recusado em 25/jul** porque o campo do MCC foi preenchido com
`777-012-3631`, que é conta de anúncios comum. O reenvio corrigido — MCC `187-999-9144` — foi feito
em **05/ago** e teve recebimento confirmado em 06/ago, case **`[3-1041000041172]`**; revisão inicial
em ~5 dias úteis. A brand verification do OAuth já está aprovada desde 28/jul.

Enquanto não sai, o caminho de teste é uma hierarquia de contas de teste (manager de teste criado
com outra Google Account; o developer token de produção funciona nela).

**Validado até aqui:** as 16 tools montam no servidor (13 originais + `mutate` + as 2 de
`planning`), a validação local recusa entradas inválidas, e o request chega à API com
`validate_only=True` e o proto correto.

**Falta:** commit real das tools de `mutate` numa conta acessível, e a primeira chamada real de
`planning` — o `KeywordPlanIdeaService` também passa pelo gate do Test Access, então só dá para
exercitar de verdade depois do Basic Access (ou numa conta de teste).

**Gotcha de ambiente:** com o app OAuth em modo *Testing*, o refresh token do ADC **expira em 7
dias** e as chamadas passam a falhar com `invalid_grant: Token has been expired or revoked`. Refazer
`gcloud auth application-default login --scopes=...adwords,...cloud-platform`. Publicar o app "Em
produção" resolveria de vez, agora que a marca está verificada.
