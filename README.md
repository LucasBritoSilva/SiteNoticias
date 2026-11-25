# SiteNoticias — Portal de Notícias com Inteligência Artificial

O **SiteNoticias** é um projeto pessoal desenvolvido com o objetivo de criar um portal de notícias que utiliza **inteligência artificial para organizar, resumir e atualizar conteúdos automaticamente**.

A proposta é reunir notícias recentes em um único ambiente, facilitando o acesso às informações de maneira prática e organizada.

## Como funciona?

O projeto possui um processo automatizado de atualização das notícias:

1. **Coleta de notícias:** o sistema busca notícias recentes no Google Notícias, considerando os temas definidos.
2. **Processamento com IA:** a inteligência artificial **Google Gemini** analisa as notícias coletadas.
3. **Organização do conteúdo:** notícias relacionadas ao mesmo acontecimento são agrupadas, evitando conteúdos repetidos.
4. **Geração de resumos:** a IA produz resumos das notícias com base nas fontes encontradas.
5. **Atualização automática:** o GitHub Actions executa o processo uma vez ao dia, mantendo o portal atualizado sem intervenção manual.

## Tecnologias utilizadas

- **Google Gemini:** análise, organização e geração de resumos com inteligência artificial.
- **Google Notícias:** fonte de coleta das notícias.
- **GitHub Actions:** automação das atualizações.
- **GitHub Pages:** hospedagem e publicação do site.

## Objetivo do projeto

O SiteNoticias foi criado como um projeto pessoal para explorar a integração entre **inteligência artificial, automação e desenvolvimento web**.

Além de disponibilizar notícias de forma organizada, o projeto demonstra como a IA pode ser utilizada em aplicações práticas para processar informações e automatizar tarefas que normalmente exigiriam intervenção humana.

## Acesse o projeto

- **Site:** https://lucasbritosilva.github.io/SiteNoticias/

## Horário de execução

O site é atualizado automaticamente uma vez por dia. O GitHub Actions usa UTC.

| UTC | Mato Grosso do Sul (UTC-4) | Brasília (UTC-3) | cron |
|---|---|---|---|
| 22:17 | 18:17 | 19:17 | `17 22 * * *` |

O horário foi escolhido de forma experimental e não garante que o Gemini esteja disponível.
O GitHub pode atrasar (ou, sob muita carga, descartar) execuções agendadas.
Também dá para rodar na hora pelo gatilho manual (`workflow_dispatch`); se já houver uma
atualização em andamento, a manual espera na fila.

## Quando a atualização falha

- Cada tentativa faz **no máximo uma requisição** ao Gemini, sem nenhuma repetição interna
  (nenhuma requisição, se o cache das últimas 24 horas já bastar).
- Se a tentativa 1 falhar por qualquer motivo (HTTP 429, 503 ou outro, timeout, rede, resposta
  inválida), o workflow espera **3600 segundos** e executa a tentativa 2. Não existe terceira.
- Só uma geração bem-sucedida é publicada. Se as duas falharem, o site anterior continua no ar.
- A regra está em um único lugar: o passo "Gerar o site" de
  [`.github/workflows/atualizar-site.yml`](.github/workflows/atualizar-site.yml).
- A espera mantém o runner ocupado: um ciclo com falha usa pouco mais de 1 hora de GitHub Actions.
  Em repositório público, os runners padrão não são cobrados; em repositório privado, esses
  minutos contam na franquia. Para desistir da segunda tentativa, cancele a execução.
