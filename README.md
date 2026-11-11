# SiteNoticias

Página de notícias por nicho. A cada execução o projeto busca notícias recentes no Google Notícias
(RSS, em português), agrupa as que tratam do mesmo fato, pede ao Gemini um resumo de cada grupo e
publica um site estático em `docs/` (GitHub Pages).

> Este README é atualizado a cada etapa. A versão final traz as instruções completas.

## Estrutura

| Caminho | Função |
|---|---|
| `nichos.json` | Lista de termos de interesse (um feed RSS por termo). |
| `scripts/` | Pipeline em Python. |
| `docs/` | Site publicado (gerado pelo pipeline, exceto `editar.html`, `style.css` e a imagem genérica). |
| `tests/` | Testes automatizados. |
| `.github/workflows/` | Agendamento no GitHub Actions. |

## Decisões tomadas

- `nichos.json` usa o formato `{"nichos": ["termo 1", "termo 2"]}`.
