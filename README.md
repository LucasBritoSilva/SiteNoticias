# SiteNoticias — Portal de Notícias com Inteligência Artificial

O **SiteNoticias** é um projeto pessoal desenvolvido com o objetivo de criar um portal de notícias que utiliza **inteligência artificial para organizar, resumir e atualizar conteúdos automaticamente**.

A proposta é reunir notícias recentes em um único ambiente, facilitando o acesso às informações de maneira prática e organizada.

## Como funciona?

O projeto possui um processo automatizado de atualização das notícias:

1. **Coleta de notícias:** o sistema busca notícias recentes no Google Notícias, considerando os temas definidos.
2. **Processamento com IA:** a inteligência artificial **Google Gemini** analisa as notícias coletadas.
3. **Organização do conteúdo:** notícias relacionadas ao mesmo acontecimento são agrupadas, evitando conteúdos repetidos.
4. **Geração de resumos:** a IA produz resumos das notícias com base nas fontes encontradas.
5. **Atualização automática:** o GitHub Actions executa o processo quatro vezes ao dia, mantendo o portal atualizado sem intervenção manual.

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

## Horários de execução

O GitHub Actions usa UTC; abaixo, o horário de Brasília (UTC-3) de cada execução. Também dá para rodar na hora
pelo gatilho manual (`workflow_dispatch`).

| Brasília | UTC | cron |
|---|---|---|
| 07:50 | 10:50 | `50 10 * * *` |
| 13:50 | 16:50 | `50 16 * * *` |
| 18:50 | 21:50 | `50 21 * * *` |
| 23:30 | 02:30 (dia seguinte) | `30 2 * * *` |

O GitHub pode atrasar execuções agendadas em alguns minutos nos horários de pico.
