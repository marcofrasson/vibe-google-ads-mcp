# Como rodar este MCP na sua máquina

Fork Vibe do `google-ads-mcp` do Google: leitura (GAQL) + escrita (`mutate_*`) +
planejamento de palavra-chave (`planning_*`). Branch de trabalho: `vibe/mutate-tools`.

## O que é compartilhado e o que é seu

| Peça | O que é | Compartilhada? |
|---|---|---|
| Este código | o fork | sim |
| `GOOGLE_ADS_DEVELOPER_TOKEN` | diz **qual empresa** chama a API | sim, o mesmo para o time |
| `GOOGLE_ADS_LOGIN_CUSTOMER_ID` | a MCC (`187-999-9144`) | sim |
| Login OAuth (ADC) | diz **quem** chama a API | **não**. Cada pessoa faz o seu |

O developer token sozinho não abre conta nenhuma. Ele só vale junto com um login que
já tenha acesso às contas dentro daquela MCC.

## Antes de começar, alguém com acesso precisa fazer 2 coisas por você

1. **Te adicionar como usuário na MCC** `187-999-9144`
   (Google Ads → Administrador → Acesso e segurança → Usuários).
   Sem isso o passo 5 devolve lista vazia.
2. **Nada no app OAuth.** O app do projeto `vibe-digital-503216` está publicado
   (*Em produção*), então não existe lista de test user para entrar e o login
   não expira sozinho. Verificado em 08/set/2026: um login feito em 11/ago,
   28 dias antes, seguia chamando a API.

Você também vai precisar receber o arquivo do cliente OAuth
(`vibe_oauth_client.json`) e os dois valores do `.env`. Peça a quem administra a conta.

## Passo a passo

### 1. Clonar e entrar

```bash
git clone <url-do-fork> vibe-google-ads-mcp
cd vibe-google-ads-mcp
git checkout vibe/mutate-tools
```

### 2. Instalar

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

### 3. Os dois segredos

```bash
cp .env.example .env
```

Abra o `.env` e preencha os dois valores. Ele é gitignored — nunca commite.

Se você prefere guardar em outro lugar, aponte:
`export VIBE_ADS_ENV_FILE=/caminho/do/seu/arquivo`

### 4. Seu login (é individual)

Guarde o `vibe_oauth_client.json` em `~/.config/gcloud/` e rode:

```bash
gcloud auth application-default login \
  --client-id-file="$HOME/.config/gcloud/vibe_oauth_client.json" \
  --scopes=https://www.googleapis.com/auth/adwords,https://www.googleapis.com/auth/cloud-platform
```

Os dois escopos são obrigatórios. Sem `--client-id-file` o Google bloqueia o login.

> O app OAuth está *Em produção*, então este login **não expira em 7 dias** —
> aquele ciclo valia enquanto o app estava em *Testing*, e acabou. Se um dia
> aparecer `invalid_grant: Token has been expired or revoked`, é só rodar o
> comando de novo.

### 5. Testar antes de registrar no Claude

```bash
./bin/vibe-ads-mcp --help
```

O wrapper avisa em português o que está faltando: variável, instalação ou arquivo.

### 6. Registrar no Claude Code

No `.mcp.json` do repo onde você vai operar (no nosso caso, `gestor-ads/`):

```json
{
  "mcpServers": {
    "vibe-google-ads": {
      "type": "stdio",
      "command": "${VIBE_ADS_MCP_BIN:-${HOME}/www/apps/vibe-google-ads-mcp/bin/vibe-ads-mcp}",
      "args": [],
      "env": {}
    }
  }
}
```

Se você clonou o fork em outro lugar, exporte o caminho no seu shell:

```bash
export VIBE_ADS_MCP_BIN="$HOME/onde/voce/clonou/bin/vibe-ads-mcp"
```

Reinicie a sessão do Claude Code para o MCP carregar.

## Deu errado?

| Mensagem | O que é |
|---|---|
| `faltam variáveis: ...` | passo 3 |
| `não achei o servidor em .../.venv/bin/google-ads-mcp` | passo 2 |
| `invalid_grant: Token has been expired or revoked` | login foi revogado, refaça o passo 4 |
| `The developer token is only approved for use with test accounts` | o token está em nível *Conta de teste* — não opera conta real |
| `USER_PERMISSION_DENIED` | seu e-mail não está na MCC, ou a conta do cliente não está vinculada a ela |
| lista de contas acessíveis vem vazia | idem acima |

## Escrita: como funciona a trava

Toda tool `mutate_*` roda com `validate_only=True` por padrão e devolve
`{"dry_run": true, ...}`. Para valer, é preciso uma segunda chamada com
`confirm=True`. Campanha nasce `PAUSED`. Detalhe em `VIBE.md`.
