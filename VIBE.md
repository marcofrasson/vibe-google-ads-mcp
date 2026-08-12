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
- `add_keywords`, `update_keyword_status`
- `create_responsive_search_ad`

`update_keyword_status` é a tool da otimização do dia a dia: o relatório de termos de busca mostra
uma keyword comprando a intenção errada e ela precisa sair sem mexer no resto do grupo. Aceita
uma **lista** de resource names, porque na prática se pausa várias de uma vez. Os resource names
vêm de um GAQL em `ad_group_criterion` e têm o formato
`customers/<cid>/adGroupCriteria/<ad_group_id>~<criterion_id>` — passar um resource name de ad
group por engano é barrado antes do round trip, com a mensagem explicando o formato esperado.
`REMOVED` não tem volta: o histórico segue consultável por GAQL, mas a keyword não pode ser
reativada. Para desligar temporariamente, use `PAUSED`.

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

## Estado atual (10/ago/2026)

**✅ Basic Access aprovado.** O developer token do MCC `187-999-9144` opera contas reais —
leitura e escrita —, com cota de **15.000 operações/dia**. O gate de Test Access
(`The developer token is only approved for use with test accounts`) não existe mais, e com ele
caiu a necessidade da hierarquia de contas de teste.

**Validado:** as 16 tools montam no servidor (3 do upstream + 11 de `mutate` + 2 de `planning`),
a validação local recusa entradas inválidas, e o request chega à API com `validate_only=True` e o
proto correto.

**Falta:** (a) registrar este fork no Claude Code — o MCP ativo ainda é o pipx do repo oficial,
que é read-only, então as 13 tools próprias não estão expostas; (b) primeira chamada real das
tools de `mutate` (dry-run e commit) e de `planning` numa conta sob o MCC.

**Gotcha de ambiente:** com o app OAuth em modo *Testing*, o refresh token do ADC **expira em 7
dias** e as chamadas falham com `invalid_grant: Token has been expired or revoked`. Reautenticar
com `gcloud auth application-default login --scopes=...adwords,...cloud-platform`. Agora que a
marca está verificada (28/jul) e o token aprovado, publicar o app "Em produção" encerra esse
ciclo de reautenticação.

