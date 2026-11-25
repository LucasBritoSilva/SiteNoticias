"""A política de 2 tentativas, executando o script que está DENTRO do workflow.

O passo "Gerar o site" é extraído do YAML e roda no bash, como no GitHub Actions (bash -e), em um
repositório git temporário. `sleep` e `timeout` são substituídos (a hora de espera não passa de
verdade) e `python` chama tests/simular_tentativa.py, que usa o pipeline real com o Gemini
SIMULADO. Quando o passo termina com sucesso, o passo de envio roda contra um remoto local.
O que depende do próprio GitHub (cron, fila de concorrência, pular passos) não é coberto aqui.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
WORKFLOW = (RAIZ / ".github" / "workflows" / "atualizar-site.yml").read_text(encoding="utf-8")


def achar_bash() -> str | None:
    if os.name != "nt":
        return shutil.which("bash")
    git = shutil.which("git")  # no Windows, usa o bash do Git (o do System32 é o do WSL)
    for pai in Path(git).resolve().parents if git else []:
        if (pai / "bin" / "bash.exe").exists():
            return str(pai / "bin" / "bash.exe")
    return None


BASH = achar_bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="bash não encontrado")


def script_do_passo(nome: str) -> str:
    """Devolve o bloco `run: |` do passo cujo nome começa com `nome`."""
    trecho = WORKFLOW[WORKFLOW.index(f"- name: {nome}"):]
    corpo = trecho[trecho.index("run: |\n") + len("run: |\n"):]
    linhas = []
    for linha in corpo.split("\n"):
        if linha.strip() and not linha.startswith(" " * 10):
            break
        linhas.append(linha[10:])
    return "\n".join(linhas)


SUBSTITUTOS = """
python()  { "$PY_REAL" "$SIMULADOR" "$@"; }
sleep()   { echo "$1 $(cat "$REGISTRO/requisicoes" | wc -l)" >> "$REGISTRO/esperas"; }
timeout() { echo "$1 $2" >> "$REGISTRO/limites"; shift 2; "$@"; }
"""


class Ambiente:
    def __init__(self, base: Path):
        self.repo, self.registro, self.remoto = base / "repo", base / "registro", base / "remoto.git"
        self.base = base
        self.registro.mkdir()
        (self.repo / "docs" / "img").mkdir(parents=True)
        for nome, texto in [("img/generica.svg", "<svg/>"), ("style.css", "x"), ("index.html", "VERSAO ANTERIOR\n")]:
            (self.repo / "docs" / nome).write_text(texto, encoding="utf-8")
        (self.repo / "dados").mkdir()
        (self.repo / "dados" / "resumos_gerados.json").write_text('{"versao": 1, "grupos": []}\n', encoding="utf-8")
        (self.repo / "nichos.json").write_text(
            json.dumps({"nichos": ["inteligência artificial"]}), encoding="utf-8")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remoto)], check=True)
        self.git("init", "-q", "-b", "main")
        for chave, valor in [("user.name", "teste"), ("user.email", "teste@exemplo.com"),
                             ("core.autocrlf", "false"), ("commit.gpgsign", "false")]:
            self.git("config", chave, valor)
        self.git("add", "-A")
        self.git("commit", "-qm", "versao anterior")
        self.git("remote", "add", "origin", self.remoto.as_posix())
        self.git("push", "-q", "origin", "main")

    def git(self, *args: str) -> str:
        r = subprocess.run(["git", *args], cwd=self.repo, capture_output=True, text=True,
                           encoding="utf-8", check=True)
        return r.stdout.strip()

    def rodar(self, passo: str, substitutos: str = SUBSTITUTOS, cenario: str = "200"):
        arquivo = self.base / "passo.sh"
        arquivo.write_text(substitutos + script_do_passo(passo), encoding="utf-8", newline="\n")
        (self.registro / "requisicoes").touch()
        ambiente = dict(
            os.environ, PY_REAL=Path(sys.executable).as_posix(), CENARIO=cenario,
            SIMULADOR=(RAIZ / "tests" / "simular_tentativa.py").as_posix(),
            REGISTRO=self.registro.as_posix(), GEMINI_API_KEY="chave-falsa-de-teste",
            GEMINI_MODEL="", PYTHONUTF8="1",
        )
        r = subprocess.run([BASH, "--noprofile", "--norc", "-e", arquivo.as_posix()], cwd=self.repo,
                           env=ambiente, capture_output=True, text=True, encoding="utf-8", timeout=120)
        r.saida = r.stdout + r.stderr
        return r

    def ciclo(self, cenario: str):
        """Como no GitHub: o envio só roda se a geração terminar com sucesso."""
        geracao = self.rodar("Gerar o site", cenario=cenario)
        if geracao.returncode == 0:
            envio = self.rodar("Enviar alterações", substitutos="")
            assert envio.returncode == 0, envio.stdout + envio.stderr
        return geracao

    def ler(self, nome: str) -> list[str]:
        caminho = self.registro / nome
        return caminho.read_text(encoding="utf-8").splitlines() if caminho.exists() else []

    def execucoes(self) -> list[dict]:
        return [json.loads(l) for l in self.ler("execucoes")]

    def commits_no_remoto(self) -> int:
        return int(self.git("--git-dir", self.remoto.as_posix(), "rev-list", "--count", "main"))

    def conferir_preservado(self):
        assert (self.repo / "docs" / "index.html").read_text(encoding="utf-8") == "VERSAO ANTERIOR\n"
        assert self.git("status", "--porcelain") == ""
        assert self.commits_no_remoto() == 1


@pytest.fixture
def amb(tmp_path):
    return Ambiente(tmp_path)


def horario(saida: str, texto: str) -> datetime:
    m = re.search(r"\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) UTC\] " + texto, saida)
    assert m, f"sem a linha '{texto}' em:\n{saida}"
    return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------ cenário A

def test_a_sucesso_na_primeira_tentativa_publica_sem_esperar(amb):
    r = amb.ciclo("200")
    assert r.returncode == 0, r.saida
    assert len(amb.execucoes()) == 1 and amb.ler("requisicoes") == ["200"]
    assert amb.ler("esperas") == []
    assert amb.commits_no_remoto() == 2
    assert "Resumo simulado do grupo 0" in (amb.repo / "docs" / "index.html").read_text(encoding="utf-8")
    assert "sucesso na tentativa 1/2" in r.saida and "Tentativa 2/2" not in r.saida
    assert amb.ler("limites") == ["--kill-after=30s 15m"]
    assert amb.execucoes()[0]["argumentos"] == ["-u", "scripts/main.py"]


# ------------------------------------------------------------ cenário B

def test_b_falha_e_depois_sucesso_espera_3600s_e_publica_uma_vez(amb):
    r = amb.ciclo("503,200")
    assert r.returncode == 0, r.saida
    assert len(amb.execucoes()) == 2 and amb.ler("requisicoes") == ["503", "200"]
    assert amb.ler("esperas") == ["3600 1"]      # uma espera, com 1 requisição feita até ali
    assert amb.commits_no_remoto() == 2
    assert "sucesso na tentativa 2/2" in r.saida
    assert "Fim da espera: este é o horário efetivo da tentativa 2/2" in r.saida
    inicio = horario(r.saida, "Início da espera de 3600 s")
    previsto = re.search(r"Tentativa 2/2 prevista para (\S+ \S+) UTC", r.saida).group(1)
    diferenca = datetime.strptime(previsto, "%Y-%m-%d %H:%M:%S") - inicio
    assert 3590 <= diferenca.total_seconds() <= 3600


@pytest.mark.parametrize("falha", ["429", "500", "timeout", "rede", "invalida", "400"])
def test_qualquer_falha_de_geracao_ganha_uma_segunda_tentativa(amb, falha):
    r = amb.ciclo(f"{falha},200")
    assert r.returncode == 0, r.saida
    assert amb.ler("requisicoes") == [falha, "200"] and len(amb.ler("esperas")) == 1
    assert amb.commits_no_remoto() == 2


def test_segunda_tentativa_comeca_de_estado_limpo_apos_falha_no_meio_da_publicacao(amb):
    r = amb.ciclo("falha_apos_publicar,200")
    assert r.returncode == 0, r.saida
    assert [e["sujeira_no_inicio"] for e in amb.execucoes()] == ["", ""]
    assert not (amb.repo / "dados" / "resumos_gerados.tmp").exists()


def test_falha_no_meio_da_publicacao_nas_duas_vezes_nao_deixa_residuo(amb):
    r = amb.ciclo("falha_apos_publicar,falha_apos_publicar")
    assert r.returncode == 1, r.saida
    amb.conferir_preservado()


# ------------------------------------------------------------ cenários C e D

@pytest.mark.parametrize("cenario", ["503,503", "429,429", "429,503", "503,429", "timeout,invalida"])
def test_c_d_duas_falhas_encerram_com_erro_sem_terceira_tentativa(amb, cenario):
    r = amb.ciclo(cenario + ",200")              # o "200" extra não pode chegar a ser usado
    assert r.returncode == 1, r.saida
    assert len(amb.execucoes()) == 2
    assert amb.ler("requisicoes") == cenario.split(",")   # 1 requisição por tentativa, sem retry
    assert amb.ler("esperas") == ["3600 1"]
    amb.conferir_preservado()
    assert "RESULTADO FINAL: as 2 tentativas falharam" in r.saida and "::error::" in r.saida
    assert "Dados anteriores preservados" in r.saida


def test_logs_trazem_tentativa_modelo_requisicoes_codigo_e_motivo_em_utc(amb):
    r = amb.ciclo("429,503")
    for esperado in (
        "Início da atualização.", "Tentativa 1/2: iniciando", "Tentativa 2/2: iniciando",
        "Modelo Gemini: gemini-flash-latest", "Gemini respondeu HTTP 429", "Gemini respondeu HTTP 503",
        "RPM (requisições por minuto)", "requisições ao Gemini: 1",
        "LIMITE DE USO EXCEDIDO", "GEMINI INDISPONÍVEL",
    ):
        assert esperado in r.saida, (esperado, r.saida)
    do_python = [l for l in r.saida.splitlines() if re.search(r" (INFO|ERROR|WARNING) ", l)]
    assert do_python and all(re.match(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d UTC ", l) for l in do_python)
    assert "chave-falsa-de-teste" not in r.saida


def test_tempo_esgotado_conta_como_falha_da_tentativa(amb):
    estouro = SUBSTITUTOS + """
timeout() { if [ ! -e "$REGISTRO/estourou" ]; then : > "$REGISTRO/estourou"; return 124; fi; shift 2; "$@"; }
"""
    r = amb.rodar("Gerar o site", substitutos=estouro, cenario="200")
    assert r.returncode == 0, r.saida
    assert "Tentativa 1/2: FALHOU por tempo esgotado" in r.saida
    assert len(amb.ler("esperas")) == 1 and amb.ler("requisicoes") == ["200"]


def test_espera_interrompida_nao_dispara_a_segunda_tentativa(amb):
    r = amb.rodar("Gerar o site", substitutos=SUBSTITUTOS + "sleep() { return 1; }\n", cenario="503,200")
    assert r.returncode == 1 and len(amb.execucoes()) == 1, r.saida
    amb.conferir_preservado()


def test_sem_conseguir_restaurar_nao_faz_a_segunda_tentativa(amb):
    quebra_git = SUBSTITUTOS + 'git() { if [ "$1" = clean ]; then return 1; fi; command git "$@"; }\n'
    r = amb.rodar("Gerar o site", substitutos=quebra_git, cenario="503,200")
    assert r.returncode == 1 and len(amb.execucoes()) == 1, r.saida
    assert amb.ler("esperas") == []
