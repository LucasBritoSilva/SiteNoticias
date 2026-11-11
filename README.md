# SiteNoticias

Página de notícias por nicho. Duas vezes por dia, um workflow do GitHub Actions busca notícias recentes
no Google Notícias (RSS em português), agrupa as que tratam do mesmo fato, pede ao Gemini um resumo de
cada grupo e publica um site estático em `docs/` (GitHub Pages).

## Como funciona

```
nichos.json ─► RSS do Google Notícias (últimas 24 h) ─► remove duplicadas
            ─► Gemini agrupa por fato (2–3 fontes) ─► top 12 grupos
            ─► lê a página original (texto e og:image) ─► Gemini escreve o resumo (JSON validado)
            ─► baixa a imagem (ou usa a genérica) ─► gera docs/ ─► commit na main
```

| Caminho | Função |
|---|---|
| `nichos.json` | Lista de termos de interesse (um feed RSS por termo). |
| `scripts/main.py` | Ponto de entrada; 3 tentativas com 15 min de intervalo. |
| `scripts/coleta.py` | Leitura dos feeds, filtro de 24 h e deduplicação. |
| `scripts/gemini.py` | Cliente da API REST do Gemini (só `requests`). |
| `scripts/resumos.py` | Agrupamento, seleção dos 12 grupos, resumo e validação do JSON. |
| `scripts/paginas.py` | URL original da matéria, `robots.txt`, `og:image` e download de imagens. |
| `scripts/site_builder.py` | Geração do HTML e troca segura do conteúdo de `docs/`. |
| `docs/` | Site publicado. Gerados: `index.html`, `resumos/`, `img/*`. Fixos: `style.css`, `editar.html`, `editar.js`, `img/generica.svg`. |
| `.github/workflows/atualizar-site.yml` | Agendamento. |
| `tests/` | Testes (`pytest` e um teste em Node para a página de edição). |

## Configuração (uma vez)

### 1. Secret `GEMINI_API_KEY`

1. Gere uma chave em <https://aistudio.google.com/apikey>.
2. No repositório: **Settings → Secrets and variables → Actions → New repository secret**.
3. Nome: `GEMINI_API_KEY`; valor: a chave. (A chave nunca fica no código.)

Opcional: para trocar o modelo, crie em **Settings → Secrets and variables → Actions → Variables** a
variável `GEMINI_MODEL` (por exemplo `gemini-2.5-flash`). Sem ela, usa-se `gemini-flash-latest`.

### 2. Ativar o GitHub Pages

**Settings → Pages → Build and deployment → Source: Deploy from a branch → Branch: `main`, pasta `/docs` → Save.**
O endereço será `https://<dono>.github.io/<repositório>/`.

### 3. Permissão do workflow

**Settings → Actions → General → Workflow permissions → Read and write permissions.**
(O workflow também declara `permissions: contents: write`, mas a configuração do repositório pode limitar isso.)

### 4. Token fino para editar os nichos pela página

1. GitHub → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
2. **Repository access: Only select repositories** → escolha apenas este repositório.
3. **Permissions → Repository permissions → Contents: Read and write.** Nenhuma outra permissão.
4. Defina uma validade e gere o token.
5. Abra `https://<dono>.github.io/<repositório>/editar.html`, cole o token, edite os nichos (um por linha)
   e clique em **Salvar**. O token fica só no `localStorage` do seu navegador; o botão
   **Esquecer token** o remove.

### 5. Primeira execução

**Actions → Atualizar notícias → Run workflow** (gatilho manual `workflow_dispatch`).
Com o GitHub CLI: `gh workflow run atualizar-site.yml`.

## Horários

Cuiabá é UTC-4 o ano todo (sem horário de verão); o Actions usa UTC:

| Cuiabá | UTC | cron |
|---|---|---|
| 03:00 | 07:00 | `0 7 * * *` |
| 15:00 | 19:00 | `0 19 * * *` |

O GitHub pode atrasar execuções agendadas em alguns minutos nos horários de pico.

## Falhas e concorrência

- Cada execução tenta o pipeline até 3 vezes, esperando 15 minutos entre as tentativas.
- O site é montado numa pasta temporária (`.staging/`) e só substitui os arquivos gerados de `docs/` depois de
  tudo pronto e com os links internos conferidos. Se as 3 tentativas falharem, `docs/` não muda e a versão anterior
  continua no ar. O projeto não envia notificações (o GitHub, porém, pode enviar o e-mail padrão de workflow
  com falha, conforme as suas preferências de conta).
- `concurrency: atualizar-site` (sem cancelar) garante uma execução por vez.
- A edição de nichos e o workflow editam arquivos diferentes (`nichos.json` x `docs/`). No envio do workflow,
  se a `main` andou, ele faz `git pull --rebase` e tenta de novo (até 5 vezes), sem `--force`.
  Na página de edição, um erro 409 do GitHub faz reler o SHA e repetir, mas só se `nichos.json` continuar
  igual ao que a página carregou; se outra pessoa o alterou, nada é gravado e a página pede para recarregar.

## Testes

```
pip install -r requirements-dev.txt
python -m pytest
```

Executa os testes Python e, se o Node.js estiver instalado, `tests/editar.test.js` (lógica da página de edição).
`python tests/gerar_exemplo.py <pasta>` gera um site de demonstração com dados simulados (útil para ver o layout).

**Os testes do Gemini usam respostas simuladas**: não havia chave de API no ambiente de desenvolvimento, então
nenhuma chamada real ao Gemini foi testada.

### Testado localmente × depende do GitHub Actions

| Testado localmente | Depende de execução no GitHub (ainda não verificado) |
|---|---|
| Leitura real dos feeds RSS, filtro de 24 h, deduplicação | Disparo dos crons nos horários certos |
| Resolução real dos links do Google e leitura real de páginas/imagens (inclusive sites que bloqueiam) | Secret `GEMINI_API_KEY` e a resposta **real** do Gemini (formato, cotas, nome do modelo) |
| Pipeline completo com Gemini simulado e rede real; falha não altera `docs/` | Commit/push com o `GITHUB_TOKEN` e a publicação do Pages depois dele |
| Layout em tela de PC e de celular (Edge headless) | Tempo total da execução com as esperas de 15 min |
| Lógica da página de edição (Node) e fluxo completo da interface com API simulada | Edição de nichos com um token real e a API real do GitHub |
| Lógica de `git pull --rebase` + push contra um remoto temporário | Comportamento de `concurrency` no GitHub |

## Decisões tomadas

- **Formato de `nichos.json`:** `{"nichos": ["termo 1", "termo 2"]}`. Uma lista simples também é aceita na leitura.
  Máximo de 10 termos na página de edição.
- **Links do Google Notícias:** o RSS entrega links de redirecionamento do Google, não a URL da matéria. Para
  listar a "URL original" e ler `og:image`, o script resolve cada link pelo endpoint interno `batchexecute` do
  Google, só para os artigos dos grupos escolhidos (cerca de 20 a 30 requisições por execução). Esse endpoint
  não é uma API documentada e pode mudar; se a resolução falhar, a fonte usa o link do Google (que
  redireciona para a matéria) e o cartão usa a imagem genérica.
- **Texto para o resumo:** o RSS só traz o título. Para o Gemini resumir com base em algo mais que manchetes, o
  script lê a página original e envia um trecho (descrição e primeiros parágrafos, até 1500 caracteres) junto com
  o título. Por isso a leitura da página acontece antes do resumo, e a mesma leitura fornece o `og:image`. Os
  prompts mandam o modelo não acrescentar fatos além das fontes. Os textos coletados são tratados como dados, não
  como instruções.
- **Coleta respeitosa:** `robots.txt` é consultado por site (cache por execução). Se proíbe o agente
  `SiteNoticiasBot`, responde 401/403 ou não pode ser lido, a página e a imagem não são coletadas e vale a imagem
  genérica. Imagens: só JPEG, PNG, WebP e GIF, até 5 MB.
- **Agrupamento e seleção em uma chamada:** o Gemini devolve os grupos já com uma nota de relevância (1–10) para
  os nichos; o código escolhe os 12 melhores. No máximo 150 manchetes entram no prompt (alternando entre nichos,
  das mais recentes para as mais antigas).
- **Regras dos grupos:** cada notícia entra em um só grupo, veículos repetidos no mesmo grupo são ignorados e o
  máximo é 3 fontes. Grupos com menos de 2 fontes são descartados.
- **Faltaram grupos?** Se houver menos de 12 grupos com 2+ fontes, a lista é completada com notícias avulsas
  (uma fonte só, de veículos diferentes, mais recentes primeiro). Nada é inventado. Alternativa mais simples seria
  mostrar menos de 12 cartões; escolhi completar para a página não ficar vazia.
- **Validação do JSON:** agrupamento e resumo são validados (tipos, tamanhos, ids existentes, URLs de fonte que
  realmente foram coletadas). Resposta inválida → uma nova tentativa. Se continuar inválida, o grupo é pulado e
  entra o próximo candidato (há 5 candidatos de reserva). Os nomes e URLs das fontes exibidos vêm dos dados
  coletados, nunca do texto livre do modelo, e todas as fontes do grupo são listadas.
- **Erros da API do Gemini** (rede, cota, 5xx): 2 novas tentativas curtas dentro da chamada (5 s e 20 s). Se
  persistir, a tentativa do pipeline falha e entra a regra das 3 tentativas. Há um intervalo mínimo de 4 s entre
  chamadas, por causa das cotas do plano gratuito.
- **Modelo:** `gemini-flash-latest` por padrão (alias que acompanha o modelo Flash atual); pode ser trocado com
  a variável `GEMINI_MODEL`. A chave vai no cabeçalho `x-goog-api-key`, nunca na URL.
- **Publicar só se algo mudou:** o workflow só faz commit se `docs/` mudou. O commit é feito pelo
  `github-actions[bot]`.
- **Falha parcial:** o site é publicado se pelo menos 1 resumo válido foi gerado (menos de 12 cartões é
  aceitável, por exemplo num dia de poucas notícias); com zero resumos, a tentativa falha e `docs/` fica como está.
- **Horário exibido:** fixo em UTC-4 (Cuiabá), sem depender de banco de fuso horário.
- **Arquivos fixos em `docs/`:** `style.css`, `editar.html`, `editar.js` e `img/generica.svg` são editados à mão; o
  pipeline só troca `index.html`, `resumos/` e as imagens baixadas.
- **`docs/index.html` inicial:** um marcador simples, substituído na primeira execução bem-sucedida.
- **Página de edição:** o repositório (`dono/nome`) é deduzido do endereço `*.github.io` e pode ser corrigido no
  campo. Ao abrir, a lista é lida (sem token, se o repositório for público). O token só é gravado no
  `localStorage` após um envio bem-sucedido.
- **Sem acionar o workflow ao salvar nichos:** a nova lista vale na próxima execução agendada ou manual, como
  pedido.
- **Dependências:** apenas `feedparser`, `requests` e `beautifulsoup4` (e `pytest` para testes). O Node.js é usado
  apenas opcionalmente para testar o JavaScript da página de edição, e não faz parte do projeto em produção.
- **Repositório inativo:** o GitHub desativa workflows agendados de repositórios públicos sem atividade por 60 dias.
  Se o agendamento parar por isso, reative em **Actions** (não verifiquei se os commits automáticos do próprio
  workflow contam como atividade).
