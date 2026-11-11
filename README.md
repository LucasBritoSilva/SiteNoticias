# SiteNoticias — Portal de Notícias com Inteligência Artificial

O **SiteNoticias** é um projeto pessoal desenvolvido com o objetivo de criar um portal de notícias que utiliza **inteligência artificial para organizar, resumir e atualizar conteúdos automaticamente**.

A proposta é reunir notícias recentes em um único ambiente, facilitando o acesso às informações de maneira prática e organizada.

## Como funciona?

O projeto possui um processo automatizado de atualização das notícias:

1. **Coleta de notícias:** o sistema busca notícias recentes no Google Notícias, considerando os temas definidos.
2. **Processamento com IA:** a inteligência artificial **Google Gemini** analisa as notícias coletadas.
3. **Organização do conteúdo:** notícias relacionadas ao mesmo acontecimento são agrupadas, evitando conteúdos repetidos.
4. **Geração de resumos:** a IA produz resumos das notícias com base nas fontes encontradas.
5. **Atualização automática:** o GitHub Actions executa o processo duas vezes ao dia, mantendo o portal atualizado sem intervenção manual.

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

## Notas técnicas

### Chamadas ao Gemini

- Cada execução faz **uma única chamada** ao Gemini, que agrupa as manchetes, escolhe os grupos mais
  relevantes e já devolve o título e o resumo de cada um (JSON validado; se vier inválido, há uma nova tentativa).
  Antes eram 1 chamada para agrupar mais 1 por cartão.
- Não foi preciso dividir em duas chamadas: a entrada (até 150 manchetes, só título e veículo) e a saída
  (cerca de 11 resumos curtos) ficam muito abaixo dos limites do modelo.
- **Consequência:** como a chamada acontece antes de abrir as páginas das matérias, o resumo é escrito só a partir
  das manchetes, e não mais de um trecho do texto. Os resumos tendem a ser mais curtos e genéricos.
- As notícias avulsas (uma fonte só) que completam a lista, quando faltam grupos de 2 a 3 fontes, agora também são
  escolhidas pelo Gemini nessa mesma chamada; o código confere que os ids existem e que não repetem o veículo.
- Tudo que é específico do Gemini fica em `scripts/gemini.py` (`GeminiResumidor.resumir_grupos`). O pipeline só
  depende desse método, então trocar de provedor é implementar outra classe com a mesma assinatura.

### Cache de resumos (`dados/resumos_gerados.json`)

- Cada grupo resumido é guardado com um identificador, as fontes (nome, URL original e link do RSS), o resultado
  e a data. O arquivo é versionado pelo workflow junto com `docs/`; ele só tem textos de notícias e URLs
  (o código recusa gravar algo que pareça chave ou token, e um teste confere isso).
- Antes de chamar o Gemini, as notícias cujas fontes já apareceram num resumo gerado nas últimas 24 horas são
  retiradas do envio, e os resumos guardados que ainda têm alguma fonte no feed das últimas 24 horas são
  reaproveitados. O Gemini só recebe as notícias novas e só é chamado para preencher as vagas que faltam
  (e se houver ao menos 2 notícias novas). Se tudo estiver reaproveitado, **não há chamada nenhuma**.
- O cache só é atualizado depois que o site foi publicado com sucesso.
- **Consequência:** uma notícia continua na página enquanto houver uma fonte dela no feed das últimas 24 h, então
  duas execuções seguidas podem produzir a mesma página (com a data de atualização nova). Isso é o que economiza
  chamadas.

### Erros do Gemini

- **Erro de cota** (HTTP 429 com cota diária, `quota`, `billing` ou `daily limit`): a execução **para na hora**, sem
  novas tentativas e sem entrar na contagem das 3 tentativas. `docs/` e `dados/` ficam como estavam. O log diz
  `EXECUÇÃO INTERROMPIDA POR ERRO DE COTA`.
- **Erros temporários** (HTTP 429 por minuto, HTTP 503/500/502/504 e falha de rede): 2 repetições curtas (5 s e 20 s)
  dentro da chamada; se persistirem, a regra de 3 tentativas com 15 minutos entre elas continua valendo. O log
  mostra `Tipo de erro: TEMPORÁRIO ...`.
- Como o 429 de cota e o de limite por minuto trazem texto parecido, a distinção usa primeiro o campo `quotaId` da
  resposta (`...PerDay...` = cota; `...PerMinute...` = temporário) e só depois a mensagem.
