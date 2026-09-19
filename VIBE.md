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

O que é checado antes de gastar uma chamada: formato do `customer_id` (aceita `123-456-7890`),
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
set -a; source .env; set +a   # developer token + login customer id (ver .env.example)
```

Auth via ADC: `gcloud auth application-default login` com os escopos `adwords` e `cloud-platform`.

## Estado atual

**Basic Access aprovado (10/ago/2026).** O developer token da MCC opera contas reais, leitura e
escrita, com cota de 15.000 operações/dia. O gate de Test Access
(`The developer token is only approved for use with test accounts`) não se aplica mais, e com ele
caiu a necessidade da hierarquia de contas de teste.

**Validado:** as 16 tools montam no servidor (3 do upstream + 11 de `mutate` + 2 de `planning`),
a validação local recusa entradas inválidas, e o request chega à API com `validate_only=True` e o
proto correto. O fork está registrado como MCP desde 12/ago/2026.

**Gotcha de ambiente (resolvido):** enquanto o app OAuth esteve em modo *Testing*, o refresh token
do ADC expirava em 7 dias e as chamadas falhavam com `invalid_grant: Token has been expired or
revoked`. Com o app publicado, o ciclo acabou: medido em 08/set/2026, um ADC criado 28 dias antes
seguia chamando a API sem reautenticação. Também não existe lista de *test user* para entrar: quem
tem acesso à MCC loga direto.

Se o `invalid_grant` voltar (revogação manual, senha trocada), reautenticar com
`gcloud auth application-default login --client-id-file=<seu-oauth-client>.json
--scopes=...adwords,...cloud-platform`.

## Rodar em outra máquina

Passo a passo em `SETUP.md`. O resumo: o código e os dois valores da MCC (developer token e
`GOOGLE_ADS_LOGIN_CUSTOMER_ID`) são os mesmos para o time; o login OAuth é individual e exige que
o e-mail da pessoa esteja na MCC.

