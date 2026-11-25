"""Faz o papel de `python scripts/main.py` nos testes do workflow (tests/test_tentativas.py).

Roda o main.main() de verdade, com o GeminiClient de verdade, na pasta atual (um repositório
temporário). Só são SIMULADOS a coleta de notícias, a leitura das páginas e o POST ao Gemini:
nada sai para a rede e nenhuma cota é usada.

Variáveis: CENARIO = o que o Gemini faz em cada execução, separado por vírgula (200, um status
HTTP de erro, "timeout", "rede", "invalida" ou "falha_apos_publicar"); REGISTRO = pasta dos registros.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

TESTES = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTES.parent / "scripts"))

import requests  # noqa: E402

import main  # noqa: E402
from coleta import Artigo  # noqa: E402

registro = Path(os.environ["REGISTRO"])


def anotar(arquivo: str, linha: str) -> None:
    with open(registro / arquivo, "a", encoding="utf-8") as f:
        f.write(linha + "\n")


def linhas(arquivo: str) -> int:
    caminho = registro / arquivo
    return len(caminho.read_text(encoding="utf-8").splitlines()) if caminho.exists() else 0


execucao = linhas("execucoes")
comportamento = os.environ["CENARIO"].split(",")[execucao]  # uma 3ª execução estouraria aqui
sujeira = subprocess.run(
    ["git", "status", "--porcelain", "--", "docs", "dados"], capture_output=True, text=True
).stdout.strip()
anotar("execucoes", json.dumps({"argumentos": sys.argv[1:], "sujeira_no_inicio": sujeira}))


class Resposta:
    def __init__(self, status: int, corpo: dict):
        self.status_code, self._corpo, self.text = status, corpo, json.dumps(corpo)

    def json(self):
        return self._corpo


def post_simulado(self, url, headers=None, json=None, timeout=None):
    anotar("requisicoes", comportamento)
    if comportamento == "timeout":
        raise requests.ReadTimeout("Read timed out.")
    if comportamento == "rede":
        raise requests.ConnectionError("conexão recusada")
    if comportamento == "invalida":
        return Resposta(200, {"candidates": [{"content": {"parts": [{"text": "isto não é json"}]}}]})
    if comportamento in ("200", "falha_apos_publicar"):
        return Resposta(200, {"candidates": [{"content": {"parts": [{"text": texto_dos_grupos()}]}}]})
    mensagem = "falha simulada"
    erro = {"code": int(comportamento), "message": mensagem}
    if comportamento == "429":
        erro["details"] = [{"violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]}]
    return Resposta(int(comportamento), {"error": erro})


def texto_dos_grupos() -> str:
    resumo = "Frase simulada do resumo, unindo as informações das fontes do grupo. " * 10
    return json.dumps({"grupos": [
        {"nicho": "inteligência artificial", "relevancia": 9 - n, "ids": [2 * n, 2 * n + 1],
         "titulo": f"Resumo simulado do grupo {n}", "resumo": resumo}
        for n in range(3)
    ]})


def enriquecer(artigos, robots, sessao=None):
    for i, a in enumerate(artigos):
        a.url, a.trecho = f"https://exemplo.com/{i}", "trecho"


ARTIGOS = json.loads((TESTES / "fixtures" / "exemplo_coleta.json").read_text(encoding="utf-8"))["artigos"]
requests.Session.post = post_simulado
main.coleta.coletar = lambda nichos, agora=None: [Artigo(**a) for a in ARTIGOS]
main.paginas.enriquecer_artigos = enriquecer
main.paginas.imagem_do_grupo = lambda *a, **k: None
if comportamento == "falha_apos_publicar":  # docs/ já foi trocado quando o cache falha ao gravar
    def falhar(caminho, entradas, agora):
        Path(caminho).parent.mkdir(parents=True, exist_ok=True)
        Path(caminho).with_suffix(".tmp").write_text("pela metade", encoding="utf-8")
        raise OSError("disco cheio (simulado)")
    main.cache.salvar = falhar
main.RAIZ = Path.cwd()
sys.exit(main.main())
